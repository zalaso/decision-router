import sys

import httpx
import pytest
from pydantic import SecretStr

from decision_router import cli
from decision_router.api import create_app
from decision_router.config import Settings
from decision_router.engine import DecisionEngine
from decision_router.providers.fake import FakeProvider


def client_for(settings, audit):
    engine = DecisionEngine(settings=settings, local=FakeProvider(), cloud=None, audit=audit)
    transport = httpx.ASGITransport(app=create_app(engine, settings))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def test_dashboard_is_served_with_restrictive_headers(audit):
    async with client_for(Settings(), audit) as client:
        home = await client.get("/")
        assert home.status_code == 307
        assert home.headers["location"] == "/dashboard/"
        page = await client.get("/dashboard/")
        assert page.status_code == 200
        assert page.headers["content-type"].startswith("text/html")
        csp = page.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp
        assert "frame-ancestors 'none'" in csp
        assert page.headers["x-content-type-options"] == "nosniff"
        for name, kind in (("app.js", "text/javascript"), ("style.css", "text/css")):
            asset = await client.get(f"/dashboard/{name}")
            assert asset.status_code == 200
            assert asset.headers["content-type"].startswith(kind)
        assert (await client.get("/dashboard/../api.py")).status_code == 404
        assert (await client.get("/dashboard/secret.txt")).status_code == 404


async def test_dashboard_can_be_disabled(audit):
    async with client_for(Settings(dashboard=False), audit) as client:
        assert (await client.get("/dashboard/")).status_code == 404
        assert (await client.get("/v1/status")).status_code == 200


async def test_status_reports_presence_of_secrets_never_values(audit):
    settings = Settings(
        mode="auto",
        typesafe_api_key=SecretStr("sk-cloud-secret"),
        api_token=SecretStr("dashboard-secret"),
    )
    async with client_for(settings, audit) as client:
        assert (await client.get("/v1/status")).status_code == 401
        response = await client.get(
            "/v1/status", headers={"Authorization": "Bearer dashboard-secret"}
        )
    assert response.status_code == 200
    assert "secret" not in response.text
    data = response.json()
    assert data["mode"] == "auto"
    assert data["cloud_configured"] is True
    assert data["auth_required"] is True
    assert data["synthetic"] is True
    assert "human_review" in {c["id"] for c in data["default_candidates"]}


async def test_status_marks_real_local_model(audit):
    async with client_for(Settings(local_backend="nli"), audit) as client:
        data = (await client.get("/v1/status")).json()
    assert data["synthetic"] is False
    assert data["cloud_configured"] is False
    assert data["local_model"] == Settings().local_model


@pytest.mark.parametrize(
    ("host", "expected"),
    [("127.0.0.1", True), ("::1", True), ("localhost", True), ("0.0.0.0", False), ("lan", False)],
)
def test_loopback_detection(host, expected):
    assert cli._is_loopback(host) is expected


def test_serve_refuses_public_bind_without_token(monkeypatch, capsys):
    monkeypatch.delenv("ROUTER_API_TOKEN", raising=False)
    monkeypatch.setattr(sys, "argv", ["decision-router", "serve", "--host", "0.0.0.0"])
    started = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(True))
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    assert not started
    assert "ROUTER_API_TOKEN" in capsys.readouterr().err


def test_serve_starts_uvicorn_factory_on_loopback(monkeypatch, capsys):
    monkeypatch.delenv("ROUTER_CONFIG", raising=False)
    monkeypatch.setattr(sys, "argv", ["decision-router", "serve", "--port", "8123"])
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: calls.append((a, k)))
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 0
    ((args, kwargs),) = calls
    assert args == ("decision_router.api:app_factory",)
    assert kwargs["factory"] is True
    assert (kwargs["host"], kwargs["port"]) == ("127.0.0.1", 8123)
    assert "http://127.0.0.1:8123/dashboard/" in capsys.readouterr().err
