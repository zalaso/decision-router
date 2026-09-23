import asyncio
import hmac
import logging
import math
import os
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from decision_router import __version__
from decision_router.catalog import CatalogService, CatalogView
from decision_router.config import Settings, load_settings
from decision_router.dashboard import Status, mount_dashboard
from decision_router.domain import DecisionRequest, DecisionResult
from decision_router.engine import DecisionEngine
from decision_router.policy import DeterministicPolicy
from decision_router.recommend import RecommendRequest, RecommendResult, recommend
from decision_router.routing import RouteRequest, RouteResult, route
from decision_router.runtime import runtime


class BodyLimit:
    def __init__(self, app: ASGIApp, max_bytes: int = 262144) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Buffer at most max_bytes before JSON parsing, including chunked bodies.
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            size += len(message.get("body", b""))
            if size > self.max_bytes:
                await JSONResponse({"error": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        async def replay() -> Message:
            return messages.pop(0) if messages else await receive()

        await self.app(scope, replay, send)


class RequestGuard:
    """One-process quota and concurrency cap. TLS termination stays outside ASGI."""

    def __init__(
        self, app: ASGIApp, *, per_minute: int, concurrent: int, require_https: bool
    ) -> None:
        self.app = app
        self.per_minute = per_minute
        self.concurrent = concurrent
        self.require_https = require_https
        self._times: deque[float] = deque()
        self._active = 0
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/v1/")
        ):
            await self.app(scope, receive, send)
            return
        if self.require_https and scope["scheme"] != "https":
            await JSONResponse({"error": "https_required"}, status_code=426)(scope, receive, send)
            return
        now = monotonic()
        async with self._lock:
            while self._times and self._times[0] <= now - 60:
                self._times.popleft()
            if len(self._times) >= self.per_minute:
                limited = True
                retry_after = max(1, math.ceil(60 - (now - self._times[0])))
            elif self._active >= self.concurrent:
                limited = True
                retry_after = 1
            else:
                limited = False
                self._times.append(now)
                self._active += 1
        if limited:
            await JSONResponse(
                {"error": "rate_limited"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            async with self._lock:
                self._active -= 1


def create_app(
    engine: DecisionEngine, settings: Settings, catalog: CatalogService | None = None
) -> FastAPI:
    app = FastAPI(title="Decision Router", version=__version__)
    app.add_middleware(BodyLimit)
    app.add_middleware(
        RequestGuard,
        per_minute=settings.max_requests_per_minute,
        concurrent=settings.max_concurrent_requests,
        require_https=settings.require_https,
    )
    policy = DeterministicPolicy(settings)
    models = catalog or CatalogService(settings)

    async def authenticate(authorization: str | None = Header(default=None)) -> None:
        token = settings.api_token.get_secret_value()
        if token and not hmac.compare_digest(
            (authorization or "").encode(), f"Bearer {token}".encode()
        ):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's default validation response includes raw input; deliberately omit it.
        return JSONResponse(status_code=422, content={"error": "invalid_request"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/status", response_model=Status, dependencies=[Depends(authenticate)])
    async def status() -> Status:
        return Status.from_settings(settings)

    @app.post("/v1/decisions", response_model=DecisionResult, dependencies=[Depends(authenticate)])
    async def decisions(request: DecisionRequest) -> DecisionResult:
        return await engine.decide(request)

    @app.post("/v1/route", response_model=RouteResult, dependencies=[Depends(authenticate)])
    async def routing(request: RouteRequest) -> RouteResult:
        return await route(request, engine, policy)

    @app.get("/v1/models", response_model=CatalogView, dependencies=[Depends(authenticate)])
    async def model_catalog(rescan: bool = False) -> CatalogView:
        return await models.view(rescan=rescan)

    @app.post(
        "/v1/models/recommend",
        response_model=RecommendResult,
        dependencies=[Depends(authenticate)],
    )
    async def recommend_model(request: RecommendRequest) -> RecommendResult:
        return await recommend(request, engine, policy, models)

    if settings.dashboard:
        mount_dashboard(app)
    return app


def app_factory() -> FastAPI:
    """Uvicorn --factory entrypoint; the lifespan owns clients and optional model runtime."""
    config = os.environ.get("ROUTER_CONFIG")
    settings = load_settings(Path(config) if config else None)
    shell = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    audit_logger = logging.getLogger("decision_router.audit")
    audit_logger.setLevel(logging.INFO)
    if not audit_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        audit_logger.addHandler(handler)
    audit_logger.propagate = False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with runtime(settings) as engine:
            child = create_app(engine, settings)
            app.mount("/", child)
            yield

    shell.router.lifespan_context = lifespan
    return shell
