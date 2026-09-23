import asyncio

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from starlette.responses import JSONResponse

from decision_router.api import RequestGuard, create_app
from decision_router.config import Settings
from decision_router.engine import DecisionEngine
from decision_router.providers.fake import FakeProvider


def app_for(settings, audit):
    engine = DecisionEngine(settings=settings, local=FakeProvider(), cloud=None, audit=audit)
    return create_app(engine, settings)


async def test_per_process_quota_returns_429(audit):
    app = app_for(Settings(max_requests_per_minute=1), audit)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post("/v1/route", json={"prompt": "code"})).status_code == 200
        limited = await client.post("/v1/route", json={"prompt": "code"})
        assert limited.status_code == 429
        assert limited.json() == {"error": "rate_limited"}
        assert 1 <= int(limited.headers["Retry-After"]) <= 60
        assert (await client.get("/health")).status_code == 200


async def test_https_gate_and_auth_required(audit):
    with pytest.raises(ValidationError):
        Settings(require_https=True)
    settings = Settings(require_https=True, api_token=SecretStr("test-only"))
    app = app_for(settings, audit)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/route", json={"prompt": "code"})
        assert response.status_code == 426
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.post(
            "/v1/route", json={"prompt": "code"}, headers={"Authorization": "Bearer test-only"}
        )
        assert response.status_code == 200


async def test_concurrency_limit_rejects_second_inflight_request():
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_app(scope, receive, send):
        entered.set()
        await release.wait()
        await JSONResponse({"ok": True})(scope, receive, send)

    app = RequestGuard(slow_app, per_minute=10, concurrent=1, require_https=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = asyncio.create_task(client.post("/v1/route", json={"prompt": "first"}))
        await asyncio.wait_for(entered.wait(), timeout=1)
        try:
            second = await client.post("/v1/route", json={"prompt": "second"})
            assert second.status_code == 429
            assert second.headers["Retry-After"] == "1"
        finally:
            release.set()
        assert (await first).status_code == 200
