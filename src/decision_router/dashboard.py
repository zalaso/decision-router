"""Browser dashboard: static files served by the API itself, no build step or CDN."""

from importlib.resources import files
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import RedirectResponse, Response

from decision_router import __version__
from decision_router.config import Settings
from decision_router.domain import Candidate, Model
from decision_router.routing import default_candidates

# Same-origin only: user-supplied text is rendered with textContent, never as markup.
_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-cache",
}
_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}


class Status(Model):
    """Non-secret runtime facts; credentials are reported only as present or absent."""

    version: str
    mode: Literal["cloud", "local", "auto", "shadow"]
    local_backend: Literal["fake", "nli", "openjev", "rizzo_flow"]
    local_model: str | None
    shadow_primary: Literal["local", "cloud"]
    synthetic: bool
    cloud_configured: bool
    auth_required: bool
    timeout_s: float
    local_accept_confidence: float
    review_below_confidence: float
    safety_min_confidence: float
    risk_threshold: float
    injection_threshold: float
    malicious_threshold: float
    default_candidates: list[Candidate]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Status":
        uses_local = settings.mode != "cloud"
        return cls(
            version=__version__,
            mode=settings.mode,
            local_backend=settings.local_backend,
            local_model=settings.local_model
            if uses_local and settings.local_backend in ("nli", "openjev")
            else None,
            shadow_primary=settings.shadow_primary,
            synthetic=uses_local and settings.local_backend == "fake",
            cloud_configured=settings.mode != "local"
            and bool(settings.typesafe_api_key.get_secret_value()),
            auth_required=bool(settings.api_token.get_secret_value()),
            timeout_s=settings.timeout_s,
            local_accept_confidence=settings.local_accept_confidence,
            review_below_confidence=settings.review_below_confidence,
            safety_min_confidence=settings.safety_min_confidence,
            risk_threshold=settings.risk_threshold,
            injection_threshold=settings.injection_threshold,
            malicious_threshold=settings.malicious_threshold,
            default_candidates=default_candidates(),
        )


def mount_dashboard(app: FastAPI) -> None:
    root = files("decision_router") / "static"
    assets = {name: (root / name).read_bytes() for name in _ASSETS}

    def asset(name: str) -> Response:
        return Response(assets[name], media_type=_ASSETS[name], headers=_HEADERS)

    @app.get("/", include_in_schema=False)
    async def home() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard_redirect() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    @app.get("/dashboard/", include_in_schema=False)
    async def dashboard() -> Response:
        return asset("index.html")

    @app.get("/dashboard/{name}", include_in_schema=False)
    async def dashboard_asset(name: str) -> Response:
        if name not in assets:
            return Response(status_code=404)
        return asset(name)
