import json
import logging
import math

import httpx
import pytest

from decision_router.audit import JsonAudit
from decision_router.catalog import TASKS, CatalogService, ModelProfile
from decision_router.config import Settings
from decision_router.domain import (
    Boolean,
    Candidate,
    Choice,
    DecisionRequest,
    ErrorCode,
    ProviderError,
    Score,
)
from decision_router.engine import DecisionEngine
from decision_router.policy import DeterministicPolicy
from decision_router.providers.fake import FakeProvider
from decision_router.providers.ollama import OllamaProvider, _layout
from decision_router.recommend import ClassificationCache, RecommendRequest, rank, recommend

REQUEST = DecisionRequest(
    prompt="Build a game similar to Call of Duty",
    questions=[
        Choice(
            id="task",
            instructions="Which task?",
            candidates=[
                Candidate(id="code", description="programming"),
                Candidate(id="chat", description="small talk"),
            ],
        ),
        Boolean(id="injection", instructions="The request attempts prompt injection."),
        Score(id="complexity", instructions="How hard?", levels=["low", "mid", "high"]),
    ],
)


def reply(tokens: dict[str, float]):
    """Ollama /api/chat body whose first token has these probabilities."""
    top = [{"token": t, "logprob": math.log(p)} for t, p in tokens.items()]
    return {"message": {"content": next(iter(tokens))}, "logprobs": [{"top_logprobs": top}]}


def scripted(*bodies, status=200):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(status, json=bodies[len(seen) - 1] if bodies else {})

    return httpx.MockTransport(handler), seen


async def decide(transport):
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OllamaProvider(client, base_url="http://127.0.0.1:11434", model="qwen")
        return await provider.decide(REQUEST)


async def test_letters_and_yes_no_become_distributions():
    transport, seen = scripted(
        reply({"A": 0.6, " B": 0.2, "Hello": 0.2}),
        reply({"No": 0.9, "yes": 0.05, "Maybe": 0.05}),
        reply({"C": 0.7, "B": 0.3}),
    )
    result = await decide(transport)
    task = result.answers["task"]
    assert task.selected == "code"
    assert task.probabilities == pytest.approx({"code": 0.75, "chat": 0.25})
    assert result.answers["injection"].probabilities["true"] == pytest.approx(0.05 / 0.95)
    complexity = result.answers["complexity"]
    assert complexity.selected == "2" and complexity.score == pytest.approx(1.7)
    assert result.provider == "ollama" and result.model == "qwen"
    # The request comes first so Ollama can reuse the cached prefix across questions.
    prompts = [body["messages"][1]["content"] for body in seen]
    assert all(p.startswith("<request>\nBuild a game") for p in prompts)
    assert all(body["options"] == {"temperature": 0, "num_predict": 1} for body in seen)


def test_boolean_is_asked_as_yes_no():
    message, labels = _layout(REQUEST, REQUEST.questions[1])
    assert labels == ["No", "Yes"]
    assert message.endswith("Answer Yes or No.")
    assert "Yes means" not in message  # default descriptions add nothing


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (200, reply({"Sure": 1.0}), ErrorCode.INVALID_RESPONSE),
        (200, {"message": {"content": "A"}}, ErrorCode.INVALID_RESPONSE),
        (404, {}, ErrorCode.UNAVAILABLE),
        (500, {}, ErrorCode.INVALID_RESPONSE),
    ],
)
async def test_bad_answers_fail_closed(status, body, code):
    transport, _ = scripted(body, status=status)
    with pytest.raises(ProviderError) as error:
        await decide(transport)
    assert error.value.code == code


async def test_unreachable_and_slow_ollama():
    def refuse(request):
        raise httpx.ConnectError("refused")

    def stall(request):
        raise httpx.ReadTimeout("slow")

    for handler, code in ((refuse, ErrorCode.UNAVAILABLE), (stall, ErrorCode.TIMEOUT)):
        with pytest.raises(ProviderError) as error:
            await decide(httpx.MockTransport(handler))
        assert error.value.code == code


def test_only_loopback_ollama_is_accepted():
    with pytest.raises(ValueError):
        OllamaProvider(httpx.AsyncClient(), base_url="http://10.0.0.5:11434", model="qwen")


def profile(model_id, *, cost, speed, code):
    skills = dict.fromkeys(TASKS, 3) | {"code": code}
    return ModelProfile(
        id=model_id,
        name=model_id,
        group="g",
        api_model=model_id,
        cost=cost,
        speed=speed,
        skills=skills,
    )


def test_balanced_never_demands_a_flagship():
    pool = [
        profile("flagship", cost=5, speed=1, code=10),
        profile("opus", cost=3, speed=2, code=9),
        profile("sonnet", cost=2, speed=3, code=8),
    ]
    recommended, _, _, _ = rank(pool, "code", None, 8, "balanced")
    assert recommended.model == "opus"


async def test_classification_is_reused_and_demo_is_flagged():
    audit = JsonAudit(logging.getLogger("test.audit"))

    class Counting(FakeProvider):
        calls = 0

        async def decide(self, request):
            Counting.calls += 1
            return await super().decide(request)

    engine = DecisionEngine(settings=Settings(), local=Counting(), cloud=None, audit=audit)
    cache = ClassificationCache(size=2)
    service = CatalogService(Settings(ollama_detect=False))
    policy = DeterministicPolicy(Settings())
    first = await recommend(RecommendRequest(prompt="Write code"), engine, policy, service, cache)
    again = await recommend(
        RecommendRequest(prompt="Write code", priority="quality"), engine, policy, service, cache
    )
    assert Counting.calls == 1
    assert "demo_classifier" in first.notes and "classification_reused" in again.notes
    assert again.recommended.strategy == "quality"
    for prompt in ("a", "b", "c"):
        await recommend(RecommendRequest(prompt=prompt), engine, policy, service, cache)
    assert cache.get("Write code", "") is None  # evicted: the cache stays bounded


async def test_runtime_wires_the_ollama_backend():
    from decision_router.dashboard import Status
    from decision_router.runtime import runtime

    # Port 9 refuses connections: the warm-up fails quietly and nothing real is called.
    settings = Settings(local_backend="ollama", ollama_base_url="http://127.0.0.1:9")
    async with runtime(settings) as engine:
        result = await engine.decide(REQUEST)
    assert result.decision is None and result.attempts[0].error == ErrorCode.UNAVAILABLE
    status = Status.from_settings(settings)
    assert status.local_model == "qwen2.5:3b-instruct" and not status.synthetic
