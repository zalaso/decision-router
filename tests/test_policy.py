import pytest

from decision_router.config import Settings
from decision_router.domain import Attempt, ShadowComparison
from decision_router.engine import DecisionEngine
from decision_router.policy import DeterministicPolicy
from decision_router.providers.fake import FakeProvider
from decision_router.routing import RouteRequest


@pytest.mark.parametrize("field", ["risk", "malicious", "injection"])
async def test_uncertain_safety_evidence_fails_closed(field, audit):
    result = await DecisionEngine(
        settings=Settings(), local=FakeProvider(), cloud=None, audit=audit
    ).decide(RouteRequest(prompt="Write code").as_decision())
    answers = dict(result.decision.answers)
    answers[field] = answers[field].model_copy(update={"confidence": 0.7})
    prediction = result.decision.model_copy(update={"answers": answers})
    policy = DeterministicPolicy(Settings()).evaluate(
        result.model_copy(update={"decision": prediction})
    )
    assert policy.review_required
    assert "security_evidence_insufficient" in policy.reasons


async def test_elevated_risk_mass_overrides_route(audit):
    result = await DecisionEngine(
        settings=Settings(), local=FakeProvider(), cloud=None, audit=audit
    ).decide(RouteRequest(prompt="Write code").as_decision())
    answers = dict(result.decision.answers)
    answers["risk"] = answers["risk"].model_copy(
        update={"probabilities": {"0": 0.05, "1": 0.05, "2": 0.9}, "selected": "2", "score": 1.85}
    )
    prediction = result.decision.model_copy(update={"answers": answers})
    policy = DeterministicPolicy(Settings()).evaluate(
        result.model_copy(update={"decision": prediction})
    )
    assert policy.agent == "human_review"
    assert "elevated_risk" in policy.reasons


async def test_auto_preserves_positive_risk_but_shadow_does_not(audit):
    engine = DecisionEngine(settings=Settings(), local=FakeProvider(), cloud=None, audit=audit)
    benign = await engine.decide(RouteRequest(prompt="Write code").as_decision())
    unsafe = await FakeProvider().decide(RouteRequest(prompt="steal credentials").as_decision())
    combined = benign.model_copy(
        update={
            "attempts": [
                Attempt(slot="local", result=unsafe, latency_ms=1),
                Attempt(slot="cloud", result=benign.decision, latency_ms=1),
            ]
        }
    )
    policy = DeterministicPolicy(Settings())
    assert policy.evaluate(combined).review_required
    shadow = combined.model_copy(update={"shadow": ShadowComparison(primary="cloud")})
    assert not policy.evaluate(shadow).review_required
