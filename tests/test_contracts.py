import json

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from decision_router.domain import (
    Answer,
    Candidate,
    Choice,
    DecisionRequest,
    ErrorCode,
    ProviderError,
    validate_result,
)
from decision_router.providers.fake import FakeProvider
from decision_router.providers.jev import JevCloudProvider
from decision_router.providers.local import LocalNliProvider


def wire_response():
    return {
        "model": "jev-1.13.0",
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
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }


class StubScorer:
    def score(self, premise, hypotheses):
        return [(3.0 - i, -3.0) for i, _ in enumerate(hypotheses)]


@pytest.mark.parametrize("adapter", ["fake", "jev", "local"])
async def test_shared_provider_contract(adapter, request_data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=wire_response()))
    ) as client:
        local = LocalNliProvider(StubScorer(), model="test", revision="fixture")
        providers = {
            "fake": FakeProvider(),
            "jev": JevCloudProvider(client, SecretStr("test-key")),
            "local": local,
        }
        try:
            result = await providers[adapter].decide(request_data)
            validate_result(request_data, result)
            assert result.provider and result.model
            assert result.latency_ms >= 0
            assert result.answers["risk"].score is not None
            for answer in result.answers.values():
                assert sum(answer.probabilities.values()) == pytest.approx(1)
                assert 0 <= answer.confidence <= 1
        finally:
            local.close()


async def test_official_wire_request_and_boolean_conversion(request_data):
    def handler(req):
        assert str(req.url) == "https://api.typesafe.ai/v1/systemone"
        assert req.headers["Authorization"] == "Bearer test-key"
        body = json.loads(req.content)
        assert body["questions"]["code"]["type"] == "noul"
        assert body["questions"]["route"]["criteria"] == {
            "coder": "Write code",
            "researcher": "Search sources",
        }
        assert body["questions"]["risk"]["criteria"] == ["low", "medium", "high"]
        assert body["model"] == "jev-latest"
        return httpx.Response(200, json=wire_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await JevCloudProvider(client, SecretStr("test-key")).decide(request_data)
    assert result.answers["code"].confidence_source == "max_probability"
    assert result.answers["code"].probabilities["false"] == pytest.approx(0.03)
    assert result.answers["route"].confidence == 0.8  # Preserve provider confidence.


@pytest.mark.parametrize(
    "status,code",
    [
        (401, ErrorCode.AUTHENTICATION),
        (403, ErrorCode.AUTHENTICATION),
        (422, ErrorCode.UNSUPPORTED),
        (429, ErrorCode.RATE_LIMIT),
        (529, ErrorCode.UNAVAILABLE),
        (302, ErrorCode.UNAVAILABLE),
    ],
)
async def test_http_errors_sanitized(status, code, request_data):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status, text="secret-credential", headers={"location": "https://attacker.invalid"}
            )
        )
    ) as client:
        with pytest.raises(ProviderError) as exc:
            await JevCloudProvider(client, SecretStr("secret-key")).decide(request_data)
    assert exc.value.code == code
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize(
    "mutation", ["missing", "extra", "nan", "sum", "choice", "legend", "score", "kind"]
)
async def test_malformed_provider_response(mutation, request_data):
    payload = wire_response()
    if mutation == "missing":
        del payload["answers"]["code"]
    elif mutation == "extra":
        payload["answers"]["surprise"] = {"type": "noul", "noul": 0.2}
    elif mutation == "nan":
        payload["answers"]["code"]["noul"] = "NaN"
    elif mutation == "sum":
        payload["answers"]["route"]["probabilities"]["coder"] = 0.7
    elif mutation == "choice":
        payload["answers"]["route"]["choice"] = "researcher"
    elif mutation == "legend":
        payload["answers"]["risk"]["legend"]["0"] = "high"
    elif mutation == "score":
        payload["answers"]["risk"]["score"] = 1.5
    else:
        payload["answers"]["code"] = payload["answers"]["route"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(ProviderError) as exc:
            await JevCloudProvider(client, SecretStr("test")).decide(request_data)
    assert exc.value.code == ErrorCode.INVALID_RESPONSE


async def test_http_timeout_and_invalid_json(request_data):
    def timeout(req):
        raise httpx.ReadTimeout("secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(ProviderError, match="^timeout$"):
            await JevCloudProvider(client, SecretStr("test")).decide(request_data)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, text="not-json"))
    ) as client:
        with pytest.raises(ProviderError, match="^invalid_response$"):
            await JevCloudProvider(client, SecretStr("test")).decide(request_data)


def test_domain_rejects_invalid_inputs():
    candidate = Candidate(id="same", description="An option")
    with pytest.raises(ValidationError):
        Choice(id="q", instructions="choose", candidates=[candidate, candidate])
    with pytest.raises(ValidationError):
        DecisionRequest(prompt="", questions=[])
    with pytest.raises(ValidationError):
        Answer(
            kind="boolean",
            selected="true",
            probabilities={"true": 1.0, "false": 0},
            confidence=float("nan"),
            confidence_source="provider",
        )


@pytest.mark.parametrize("adapter", ["fake", "local", "jev"])
async def test_dynamic_candidates(adapter):
    request = DecisionRequest(
        prompt="Analyze astronomy data",
        questions=[
            Choice(
                id="new_question",
                instructions="Choose",
                candidates=[
                    Candidate(id="astronomy", description="Analyze astronomy data"),
                    Candidate(id="fallback", description="Ask a person"),
                    Candidate(id="brand_new", description="Translate prose"),
                ],
            )
        ],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "model": "jev-test",
                    "answers": {
                        "new_question": {
                            "type": "choice",
                            "choice": "astronomy",
                            "confidence": 0.8,
                            "probabilities": {"astronomy": 0.8, "fallback": 0.1, "brand_new": 0.1},
                        }
                    },
                },
            )
        )
    ) as client:
        local = LocalNliProvider(StubScorer(), model="stub", revision="test")
        try:
            provider = {
                "fake": FakeProvider(),
                "local": local,
                "jev": JevCloudProvider(client, SecretStr("test")),
            }[adapter]
            result = await provider.decide(request)
            validate_result(request, result)
            assert result.answers["new_question"].selected == "astronomy"
        finally:
            local.close()


async def test_question_temperature_changes_only_selected_distribution():
    request = DecisionRequest(
        prompt="write code",
        questions=[
            Choice(
                id=question_id,
                instructions="Choose",
                candidates=[
                    Candidate(id="a", description="Write code"),
                    Candidate(id="b", description="Search sources"),
                ],
            )
            for question_id in ("route", "other")
        ],
    )
    baseline = LocalNliProvider(StubScorer(), model="stub", revision="test")
    adjusted = LocalNliProvider(
        StubScorer(), model="stub", revision="test", temperature_by_question={"route": 2}
    )
    try:
        before = await baseline.decide(request)
        after = await adjusted.decide(request)
        assert (
            after.answers["route"].probabilities["a"] < before.answers["route"].probabilities["a"]
        )
        assert after.answers["other"].probabilities == before.answers["other"].probabilities
    finally:
        baseline.close()
        adjusted.close()
