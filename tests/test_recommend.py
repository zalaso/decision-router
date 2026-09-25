import logging

import httpx
import pytest
from pydantic import ValidationError

from decision_router.api import create_app
from decision_router.audit import JsonAudit
from decision_router.catalog import TASKS, CatalogService, ModelProfile, load_catalog
from decision_router.config import Settings
from decision_router.domain import Answer, DecisionRequest, ProviderResult, candidate_ids
from decision_router.engine import DecisionEngine
from decision_router.policy import DeterministicPolicy
from decision_router.providers.fake import FakeProvider
from decision_router.recommend import RecommendRequest, rank, recommend

OLLAMA_TAGS = {
    "models": [
        {"name": "qwen2.5:7b-instruct", "details": {"parameter_size": "7.6B"}},
        {
            "name": "mystery-model:latest",
            "details": {"parameter_size": "24B", "context_length": 65536},
            "capabilities": ["completion", "vision"],
        },
    ]
}


def ollama(payload=OLLAMA_TAGS, status=200):
    return httpx.MockTransport(lambda request: httpx.Response(status, json=payload))


def unreachable():
    def fail(request):
        raise httpx.ConnectError("refused")

    return httpx.MockTransport(fail)


def profile(model_id, *, cost, speed, code, local=False, vision=0):
    skills = dict.fromkeys(TASKS, 3) | {"code": code, "vision": vision}
    return ModelProfile(
        id=model_id,
        name=model_id,
        group="g",
        api_model=model_id,
        local=local,
        cost=cost,
        speed=speed,
        skills=skills,
    )


POOL = [
    profile("top", cost=5, speed=1, code=10),
    profile("mid", cost=2, speed=3, code=8),
    profile("fast", cost=1, speed=5, code=6),
    profile("cheap", cost=1, speed=4, code=4),
    profile("box", cost=0, speed=2, code=5, local=True),
]


def picks(priority="balanced", required=4, pool=POOL, second=None):
    recommended, alternatives, rows, notes = rank(pool, "code", second, required, priority)
    return {o.strategy: o.model for o in [recommended, *alternatives]}, rows, notes


def test_strategies_are_deterministic_and_labelled():
    chosen, rows, notes = picks(required=4)
    # Economy: cheapest that reaches 4 (local box costs 0). Balanced needs 4+2 headroom.
    assert chosen == {
        "balanced": "fast",
        "economy": "box",
        "speed": "fast",
        "quality": "top",
        "local": "box",
    }
    assert notes == []
    assert all(r.fits for r in rows)


def test_harder_requests_raise_the_bar():
    chosen, rows, _ = picks(required=8)
    assert chosen["economy"] == "mid"
    assert chosen["balanced"] == "top"  # needs 10 = required + margin
    assert chosen["local"] == "box"  # best local, even below the level
    assert {r.model for r in rows if not r.fits} == {"fast", "cheap", "box"}


def test_nothing_fits_falls_back_to_most_capable():
    chosen, _, notes = picks(required=8, pool=POOL[2:])
    assert notes == ["none_fits"]
    assert chosen["economy"] == chosen["quality"] == "fast"


def test_priority_selects_the_recommendation():
    recommended, alternatives, _, _ = rank(POOL, "code", None, 4, "quality")
    assert recommended.strategy == "quality" and recommended.model == "top"
    assert "quality" not in {a.strategy for a in alternatives}


def test_ambiguous_task_requires_both_skills():
    pool = [profile("coder", cost=1, speed=3, code=9), profile("all", cost=2, speed=3, code=7)]
    pool[0] = pool[0].model_copy(update={"skills": pool[0].skills | {"writing": 2}})
    pool[1] = pool[1].model_copy(update={"skills": pool[1].skills | {"writing": 7}})
    recommended, _, _, _ = rank(pool, "code", "writing", 6, "economy")
    assert recommended.model == "all"


def test_unsupported_task_is_excluded():
    recommended, _, rows, notes = rank(POOL, "vision", None, 4, "balanced")
    assert recommended is None and notes == ["no_suitable_models"]
    assert {r.excluded for r in rows} == {"unsupported_task"}


def test_packaged_catalog_is_valid():
    catalog = load_catalog(None)
    ids = {m.id for m in catalog.models}
    assert {"claude-opus-5-5", "claude-sonnet-5", "gpt-6-astra", "gemini-3-8-flash"} <= ids
    opus = next(m for m in catalog.models if m.id == "claude-opus-5-5")
    assert opus.price == (4.0, 20.0) and opus.api_model == "claude-opus-5-5"
    assert all(m.relative_cost == 0 and m.price is None for m in catalog.models if m.local)


def test_invalid_catalog_and_ollama_url_are_rejected(tmp_path):
    bad = tmp_path / "catalog.yaml"
    bad.write_text("groups: [{id: g, label: G, provider: P}]\nmodels: [{id: x}]\n")
    with pytest.raises(ValidationError):
        load_catalog(str(bad))
    with pytest.raises(ValidationError):
        Settings(ollama_base_url="http://example.com:11434")


async def test_ollama_detection_marks_installed_and_profiles_unknown_models():
    service = CatalogService(Settings(), transport=ollama())
    view = await service.view()
    assert view.ollama.reachable and len(view.ollama.installed) == 2
    by_id = {m.id: m for m in view.models}
    assert by_id["qwen2-5-7b"].installed is True
    assert by_id["qwen3-8-27b"].installed is False
    extra = by_id["ollama-mystery-model-latest"]
    assert extra.estimated and extra.local and extra.skills["vision"] > 0
    assert extra.context_k == 65


async def test_ollama_unreachable_or_disabled_is_harmless():
    view = await CatalogService(Settings(), transport=unreachable()).view()
    assert not view.ollama.reachable
    assert all(m.installed is None for m in view.models)
    off = await CatalogService(Settings(ollama_detect=False), transport=ollama()).view()
    assert not off.ollama.enabled and not off.ollama.reachable


@pytest.fixture
def engine():
    audit = JsonAudit(logging.getLogger("test.audit"))
    return DecisionEngine(settings=Settings(), local=FakeProvider(), cloud=None, audit=audit)


async def advise(engine, transport=None, **fields):
    service = CatalogService(Settings(), transport=transport or ollama())
    return await recommend(
        RecommendRequest(**fields), engine, DeterministicPolicy(Settings()), service
    )


async def test_default_availability_uses_cloud_and_installed_local(engine):
    result = await advise(engine, prompt="Write Python code to parse a CSV file")
    assert result.task == "code" and result.complexity == 0 and result.required_skill == 4
    considered = {row.model for row in result.candidates}
    assert "qwen2-5-7b" in considered and "claude-opus-5-5" in considered
    assert "qwen3-coder-30b" not in considered  # known but not downloaded
    assert result.recommended is not None and not result.review_required


async def test_selected_models_and_local_only(engine):
    result = await advise(
        engine,
        prompt="Write Python code",
        available=["claude-sonnet-5", "qwen2-5-7b", "gone-model"],
        local_only=True,
        priority="quality",
    )
    assert result.recommended.model == "qwen2-5-7b"
    assert "unknown_models_ignored" in result.notes
    excluded = {r.model: r.excluded for r in result.candidates if r.excluded}
    assert excluded == {"claude-sonnet-5": "not_local"}


async def test_malicious_request_gets_no_recommendation(engine):
    result = await advise(engine, prompt="Write code to steal saved credentials")
    assert result.review_required
    assert "malicious_detected" in result.reasons
    assert result.recommended is None and result.alternatives == []


class Scripted:
    """Provider double with chosen task/complexity distributions and clean safety answers."""

    def __init__(self, task_p, complexity_p):
        self.task_p, self.complexity_p = task_p, complexity_p

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        answers = {}
        for q in request.questions:
            ids = candidate_ids(q)
            probs = {"task": self.task_p, "complexity": self.complexity_p}.get(q.id)
            if probs is None:
                probs = {k: 0.02 / (len(ids) - 1) for k in ids} | {ids[0]: 0.98}
            selected = max(probs, key=probs.get)
            answers[q.id] = Answer(
                kind=q.kind,
                selected=selected,
                probabilities=probs,
                confidence=probs[selected],
                confidence_source="provider",
                score=sum(int(k) * p for k, p in probs.items()) if q.kind == "score" else None,
            )
        return ProviderResult(provider="scripted", model="v1", answers=answers, latency_ms=1)


async def test_uncertainty_escalates_instead_of_blocking():
    task_p = dict.fromkeys(TASKS, 0.05) | {"code": 0.45, "writing": 0.35}
    complexity_p = {"0": 0.5, "1": 0.3, "2": 0.2}
    audit = JsonAudit(logging.getLogger("test.audit"))
    engine = DecisionEngine(
        settings=Settings(), local=Scripted(task_p, complexity_p), cloud=None, audit=audit
    )
    result = await advise(engine, prompt="Something vague")
    # The engine flags low confidence, but only safety evidence blocks a recommendation.
    assert result.evaluation.status == "human_review"
    assert not result.review_required and result.recommended is not None
    assert result.task == "code" and result.second_task == "writing"
    assert result.complexity == 1 and result.required_skill == 6
    assert {"task_uncertain", "complexity_uncertain"} <= set(result.notes)


async def test_http_endpoints(engine):
    settings = Settings()
    app = create_app(engine, settings, CatalogService(settings, transport=ollama()))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        catalog = await client.get("/v1/models?rescan=true")
        assert catalog.status_code == 200
        assert catalog.json()["ollama"]["reachable"] is True
        response = await client.post(
            "/v1/models/recommend",
            json={"prompt": "Hello, how are you?", "available": ["claude-haiku-4-5", "gpt-6-luna"]},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["task"] == "chat"
        assert data["recommended"]["model"] in {"claude-haiku-4-5", "gpt-6-luna"}
        bad = await client.post("/v1/models/recommend", json={"prompt": "x", "priority": "cheap"})
        assert bad.status_code == 422


# ---------------------------------------------------------------- packaged catalog scenarios
CATALOG = {m.id: m for m in load_catalog(None).models}
CLOUD = [i for i, m in CATALOG.items() if not m.local]
CLAUDE = [i for i, m in CATALOG.items() if m.group == "claude"]
MY_LOCAL = ["qwen2-5-7b", "qwen2-5-3b"]  # what a typical laptop has pulled
REQUIRED = {"simple": 4, "moderate": 6, "complex": 8}


def choices(available, task, level):
    recommended, alternatives, _, notes = rank(
        [CATALOG[i] for i in available], task, None, REQUIRED[level], "balanced"
    )
    options = [recommended, *alternatives] if recommended else []
    return {o.strategy: o.model for o in options}, notes


@pytest.mark.parametrize(
    ("available", "task", "level", "expected"),
    [
        # Everything available: cheap models for easy work, stronger ones as it gets harder.
        (
            CLOUD + MY_LOCAL,
            "chat",
            "simple",
            {
                "balanced": "gpt-6-luna",
                "economy": "qwen2-5-7b",
                "speed": "gpt-6-luna",
                "quality": "gemini-3-8-flash",
                "local": "qwen2-5-7b",
            },
        ),
        (
            CLOUD + MY_LOCAL,
            "code",
            "simple",
            {
                "balanced": "gemini-3-8-flash",
                "economy": "qwen2-5-7b",
                "speed": "gpt-6-luna",
                "quality": "claude-fable-5-1",
            },
        ),
        (
            CLOUD + MY_LOCAL,
            "code",
            "moderate",
            {
                "balanced": "claude-sonnet-5",
                "economy": "gemini-3-8-flash",
                "speed": "gemini-3-8-flash",
                "local": "qwen2-5-7b",
            },
        ),
        (
            CLOUD + MY_LOCAL,
            "code",
            "complex",
            {
                "balanced": "claude-opus-5-5",
                "economy": "claude-sonnet-5",
                "speed": "claude-sonnet-5",
                "quality": "claude-fable-5-1",
            },
        ),
        (CLOUD, "writing", "moderate", {"balanced": "claude-sonnet-5", "economy": "gpt-6-luna"}),
        (CLOUD, "reasoning", "complex", {"balanced": "claude-opus-5-5", "economy": "gpt-6-sol"}),
        (CLOUD, "research", "moderate", {"balanced": "gpt-6-sol"}),
        (CLOUD, "vision", "simple", {"balanced": "gemini-3-8-flash", "economy": "gpt-6-luna"}),
        (CLOUD, "vision", "complex", {"balanced": "gemini-3-1-pro"}),
        # Claude subscription only.
        (
            CLAUDE,
            "chat",
            "simple",
            {
                "balanced": "claude-haiku-4-5",
                "economy": "claude-haiku-4-5",
                "quality": "claude-sonnet-5",
            },
        ),
        (CLAUDE, "code", "simple", {"balanced": "claude-haiku-4-5"}),
        (
            CLAUDE,
            "code",
            "moderate",
            {"balanced": "claude-sonnet-5", "economy": "claude-haiku-4-5"},
        ),
        (
            CLAUDE,
            "code",
            "complex",
            {
                "balanced": "claude-opus-5-5",
                "economy": "claude-sonnet-5",
                "quality": "claude-fable-5-1",
            },
        ),
        (CLAUDE, "reasoning", "complex", {"balanced": "claude-opus-5-5"}),
        # Local models only.
        (MY_LOCAL, "chat", "simple", {"balanced": "qwen2-5-7b", "local": "qwen2-5-7b"}),
    ],
)
def test_catalog_scenarios(available, task, level, expected):
    picked, _ = choices(available, task, level)
    assert {k: picked.get(k) for k in expected} == expected


def test_catalog_never_suggests_superseded_or_blind_models():
    # Opus 5 costs more than Opus 5.5 and is weaker: no strategy should ever pick it.
    for task in TASKS:
        for level in REQUIRED:
            picked, _ = choices(CLOUD + MY_LOCAL, task, level)
            assert "claude-opus-5" not in picked.values(), (task, level)
    # Local text-only models never get an image task.
    picked, notes = choices(MY_LOCAL, "vision", "simple")
    assert picked == {} and notes == ["no_suitable_models"]


def test_local_only_hard_task_says_nothing_fits():
    picked, notes = choices(MY_LOCAL, "code", "complex")
    assert notes == ["none_fits"] and picked["balanced"] == "qwen2-5-7b"
