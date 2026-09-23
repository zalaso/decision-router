import asyncio

import pytest

from decision_router.config import Settings
from decision_router.domain import ErrorCode, ProviderError
from decision_router.engine import DecisionEngine
from decision_router.providers.fake import FakeProvider


class ControlledProvider:
    def __init__(self, confidence=0.9, error=None, delay=0, winner=None):
        self.confidence, self.error, self.delay = confidence, error, delay
        self.winner = winner
        self.calls = 0

    async def decide(self, request):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise ProviderError(self.error)
        result = await FakeProvider().decide(request)
        answers = {
            key: a.model_copy(update={"confidence": self.confidence})
            for key, a in result.answers.items()
        }
        if self.winner:
            a = answers["route"]
            probabilities = {key: 0.1 / (len(a.probabilities) - 1) for key in a.probabilities}
            probabilities[self.winner] = 0.9
            answers["route"] = a.model_copy(
                update={"selected": self.winner, "probabilities": probabilities}
            )
        return result.model_copy(update={"answers": answers})


@pytest.mark.parametrize(
    "confidence,cloud_calls,status",
    [
        (0.8, 0, "decided"),
        (0.79, 1, "decided"),
        (0.55, 1, "decided"),
        (0.54, 0, "human_review"),
    ],
)
async def test_auto_threshold_boundaries(confidence, cloud_calls, status, request_data, audit):
    local, cloud = ControlledProvider(confidence), ControlledProvider()
    engine = DecisionEngine(settings=Settings(mode="auto"), local=local, cloud=cloud, audit=audit)
    result = await engine.decide(request_data)
    assert result.status == status
    assert cloud.calls == cloud_calls


@pytest.mark.parametrize("code", list(ErrorCode))
async def test_auto_error_fallback(code, request_data, audit):
    cloud = ControlledProvider()
    result = await DecisionEngine(
        settings=Settings(mode="auto"),
        local=ControlledProvider(error=code),
        cloud=cloud,
        audit=audit,
    ).decide(request_data)
    assert result.status == "decided" and cloud.calls == 1
    assert result.fallback_reason == code.value


async def test_cloud_failure_abstains(request_data, audit):
    result = await DecisionEngine(
        settings=Settings(mode="auto"), local=ControlledProvider(0.7), cloud=None, audit=audit
    ).decide(request_data)
    assert result.status == "human_review"
    assert result.decision is None  # Don't reuse the uncertain local answer.
    assert result.fallback_reason == "low_confidence:cloud_unavailable"


@pytest.mark.parametrize("mode,local_calls,cloud_calls", [("local", 1, 0), ("cloud", 0, 1)])
async def test_modes_are_isolated(mode, local_calls, cloud_calls, request_data, audit):
    local, cloud = ControlledProvider(), ControlledProvider()
    await DecisionEngine(
        settings=Settings(mode=mode), local=local, cloud=cloud, audit=audit
    ).decide(request_data)
    assert (local.calls, cloud.calls) == (local_calls, cloud_calls)


async def test_timeout_then_cloud(request_data, audit):
    result = await DecisionEngine(
        settings=Settings(mode="auto", timeout_s=0.01),
        local=ControlledProvider(delay=1),
        cloud=ControlledProvider(),
        audit=audit,
    ).decide(request_data)
    assert result.status == "decided"
    assert result.attempts[0].error == ErrorCode.TIMEOUT


@pytest.mark.parametrize("primary", ["local", "cloud"])
async def test_shadow_disagreement_and_primary_isolation(primary, request_data, audit):
    result = await DecisionEngine(
        settings=Settings(mode="shadow", shadow_primary=primary),
        local=ControlledProvider(winner="coder"),
        cloud=ControlledProvider(winner="researcher"),
        audit=audit,
    ).decide(request_data)
    assert result.shadow.agreement["route"] is False
    assert result.decision.answers["route"].selected == (
        "coder" if primary == "local" else "researcher"
    )
    assert len(result.attempts) == 2


async def test_shadow_never_promotes_secondary(request_data, audit):
    result = await DecisionEngine(
        settings=Settings(mode="shadow"),
        local=ControlledProvider(error=ErrorCode.UNAVAILABLE),
        cloud=ControlledProvider(),
        audit=audit,
    ).decide(request_data)
    assert result.status == "human_review" and result.decision is None
    assert result.shadow.agreement is None


async def test_secondary_failure_does_not_change_primary(request_data, audit):
    result = await DecisionEngine(
        settings=Settings(mode="shadow", timeout_s=0.01),
        local=ControlledProvider(),
        cloud=ControlledProvider(delay=1),
        audit=audit,
    ).decide(request_data)
    assert result.status == "decided"
    assert result.attempts[1].error == ErrorCode.TIMEOUT


async def test_unexpected_exception_sanitized(request_data, audit, caplog):
    class Broken:
        async def decide(self, request):
            raise RuntimeError("secret-key-and-prompt")

    with caplog.at_level("INFO"):
        result = await DecisionEngine(
            settings=Settings(), local=Broken(), cloud=None, audit=audit
        ).decide(request_data)
    assert result.status == "human_review"
    assert "secret-key-and-prompt" not in caplog.text + result.model_dump_json()


async def test_cancellation_is_not_swallowed(request_data, audit):
    task = asyncio.create_task(
        DecisionEngine(
            settings=Settings(), local=ControlledProvider(delay=3), cloud=None, audit=audit
        ).decide(request_data)
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
