import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from decision_router.config import Settings, load_settings
from decision_router.domain import Candidate, Choice, DecisionRequest, ErrorCode, ProviderError
from decision_router.providers.rizzo_flow import RizzoFlowProvider
from decision_router.runtime import runtime


def _response():
    return {
        "model": "rizzo-spark-x2.5-4b-q8_0",
        "answers": {
            "route": {
                "type": "choice",
                "choice": "coder",
                "confidence": 0.8,
                "probabilities": {"coder": 0.9, "researcher": 0.1},
            },
            "code": {"type": "noul", "noul": 0.97},
            "risk": {
                "type": "score",
                "score": 0.3,
                "confidence": 0.85,
                "legend": {"0": "low", "1": "medium", "2": "high"},
                "probabilities": {"0": 0.8, "1": 0.1, "2": 0.1},
            },
        },
    }


async def test_rizzo_wire_contract_and_key_separation(request_data):
    def handler(request):
        assert str(request.url) == "http://127.0.0.1:8017/v1/systemone"
        assert request.headers.get("Authorization") == "Bearer local-only"
        payload = json.loads(request.content)
        assert payload["model"] == "rizzo-latest"
        assert payload["state"] == {"prompt": "Write Python code", "context": ""}
        assert payload["questions"]["route"]["criteria"] == {
            "coder": "Write code",
            "researcher": "Search sources",
        }
        assert payload["questions"]["code"]["type"] == "noul"
        assert payload["questions"]["risk"]["criteria"] == ["low", "medium", "high"]
        return httpx.Response(200, json=_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False) as client:
        result = await RizzoFlowProvider(
            client, base_url="http://127.0.0.1:8017", api_key=SecretStr("local-only")
        ).decide(request_data)
    assert result.provider == "rizzo_flow"
    assert result.model == "rizzo-spark-x2.5-4b-q8_0"
    assert result.answers["route"].selected == "coder"
    assert result.answers["route"].confidence_source == "provider"
    assert result.answers["code"].confidence_source == "max_probability"
    assert result.answers["risk"].score == pytest.approx(0.3)
    assert "rizzo_flow_confidence_not_calibrated_for_routing" in result.warnings


async def test_runtime_uses_local_rizzo_without_cloud_credentials(monkeypatch, request_data):
    real_client = httpx.AsyncClient

    def handler(request):
        assert request.headers.get("Authorization") is None
        assert str(request.url) == "http://127.0.0.1:8017/v1/systemone"
        return httpx.Response(200, json=_response())

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    async with runtime(
        Settings(
            mode="local",
            local_backend="rizzo_flow",
            typesafe_api_key=SecretStr("cloud-secret"),
        )
    ) as engine:
        result = await engine.decide(request_data)
    assert result.status == "decided"
    assert result.decision is not None
    assert result.decision.provider == "rizzo_flow"
    assert [attempt.slot for attempt in result.attempts] == ["local"]


async def test_rizzo_connection_failure_abstains_instead_of_using_fake(monkeypatch, request_data):
    real_client = httpx.AsyncClient

    def unavailable(request):
        raise httpx.ConnectError("private connection detail")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(unavailable), **kwargs),
    )
    async with runtime(Settings(mode="local", local_backend="rizzo_flow")) as engine:
        result = await engine.decide(request_data)
    assert result.status == "human_review"
    assert result.decision is None
    assert result.attempts[0].error == ErrorCode.UNAVAILABLE
    assert "private connection detail" not in result.model_dump_json()


@pytest.mark.parametrize(
    "base_url",
    [
        "https://127.0.0.1:8017",
        "http://localhost:8017",
        "http://192.168.1.5:8017",
        "http://example.com:8017",
        "http://127.0.0.1",
        "http://127.0.0.1:0",
        "http://user:secret@127.0.0.1:8017",
        "http://127.0.0.1:8017/other",
        "http://127.0.0.1:8017?next=example.com",
    ],
)
async def test_rizzo_origin_rejects_non_loopback_or_ambiguous_url(base_url):
    with pytest.raises(ValidationError):
        Settings(rizzo_base_url=base_url)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            RizzoFlowProvider(client, base_url=base_url, api_key=SecretStr(""))


def test_rizzo_key_is_environment_only(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("local_backend: rizzo_flow\nrizzo_api_key: leaked\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Credentials belong in environment"):
        load_settings(config)
    monkeypatch.setenv("RIZZO_API_KEY", "local-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "cloud-secret")
    settings = load_settings()
    assert settings.rizzo_api_key.get_secret_value() == "local-secret"
    assert settings.typesafe_api_key.get_secret_value() == "cloud-secret"


def test_rizzo_example_config_loads():
    settings = load_settings(Path("config/rizzo-flow.yaml"))
    assert settings.mode == "local"
    assert settings.local_backend == "rizzo_flow"
    assert settings.rizzo_base_url == "http://127.0.0.1:8017"


@pytest.mark.parametrize(
    "status,expected",
    [(401, ErrorCode.AUTHENTICATION), (422, ErrorCode.UNSUPPORTED), (302, ErrorCode.UNAVAILABLE)],
)
async def test_rizzo_http_error_does_not_follow_redirect(status, expected, request_data):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(status, headers={"Location": "https://example.com/steal"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RizzoFlowProvider(
            client, base_url="http://127.0.0.1:8017", api_key=SecretStr("local-secret")
        )
        with pytest.raises(ProviderError) as exc:
            await provider.decide(request_data)
    assert exc.value.code == expected
    assert seen == ["http://127.0.0.1:8017/v1/systemone"]


async def test_rizzo_rejects_invalid_response(request_data):
    response = _response()
    response["answers"]["route"]["choice"] = "researcher"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    ) as client:
        with pytest.raises(ProviderError, match="^invalid_response$"):
            await RizzoFlowProvider(
                client, base_url="http://127.0.0.1:8017", api_key=SecretStr("")
            ).decide(request_data)


async def test_rizzo_rejects_more_than_26_choices_before_http():
    request = DecisionRequest(
        prompt="Choose",
        questions=[
            Choice(
                id="route",
                instructions="Choose",
                candidates=[
                    Candidate(id=f"candidate_{index}", description=f"Option {index}")
                    for index in range(27)
                ],
            )
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: pytest.fail("Unsupported request must not reach Rizzo Flow")
        )
    ) as client:
        with pytest.raises(ProviderError, match="^unsupported_input$"):
            await RizzoFlowProvider(
                client, base_url="http://127.0.0.1:8017", api_key=SecretStr("")
            ).decide(request)
