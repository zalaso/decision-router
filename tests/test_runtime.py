from decision_router.config import Settings
from decision_router.runtime import runtime


async def test_missing_local_weights_fail_closed(monkeypatch, request_data):
    from decision_router.providers.process import ProcessNliProvider

    async def broken(self):
        raise OSError("secret filesystem detail")

    monkeypatch.setattr(ProcessNliProvider, "start", broken)
    async with runtime(Settings(local_backend="nli")) as engine:
        result = await engine.decide(request_data)
    assert result.status == "human_review"
    assert result.decision is None
    assert "secret" not in result.model_dump_json()


def test_factory_serves_real_openapi(monkeypatch):
    from fastapi.testclient import TestClient

    from decision_router.api import app_factory

    monkeypatch.setenv("ROUTER_MODE", "local")
    monkeypatch.setenv("ROUTER_LOCAL_BACKEND", "fake")
    with TestClient(app_factory()) as client:
        response = client.get("/openapi.json")
        assert response.status_code == 200
        assert "/v1/route" in response.json()["paths"]
        assert client.get("/docs").status_code == 200
        assert client.post("/v1/route", json={"prompt": "Write Python code"}).status_code == 200
