"""Model recommendation: the classifier describes the request, fixed rules pick the model.

The classifier never sees the catalog. It answers typed questions (task, complexity,
safety); a deterministic ranking over the models the user marked as available turns
those answers into one recommended model plus labelled alternatives.
"""

import hashlib
from collections import OrderedDict
from typing import Literal

from pydantic import Field

from decision_router.catalog import TASKS, CatalogService, ModelProfile, Task
from decision_router.domain import (
    Answer,
    Candidate,
    Choice,
    DecisionRequest,
    DecisionResult,
    Identifier,
    Model,
    Question,
    Score,
)
from decision_router.engine import DecisionEngine
from decision_router.policy import DeterministicPolicy, risk_questions

Strategy = Literal["balanced", "economy", "speed", "quality", "local"]
Priority = Literal["balanced", "economy", "speed", "quality"]

# Skill (0-10) a model needs for a task of each complexity level: simple, moderate, complex.
REQUIRED_SKILL = (4, 6, 8)
# "Balanced" asks for this much headroom above the minimum, at the lowest cost, but never
# more than BALANCED_CEILING: demanding a 10/10 flagship is what "quality" is for.
BALANCED_MARGIN = 2
BALANCED_CEILING = 9
# Below this confidence the router plans for the harder reading of the request.
UNCERTAIN_BELOW = 0.6

TASK_DESCRIPTIONS: dict[Task, str] = {
    "code": "building software such as apps, games or websites, or writing, reviewing or "
    "debugging code, scripts or programs",
    "reasoning": "math, logic puzzles, planning or complex analytical reasoning",
    "writing": "writing or editing prose such as emails, articles, stories or translations "
    "(not software)",
    "research": "researching a topic, finding sources, comparing facts or summarizing documents",
    "chat": "casual conversation, greetings like hello, or simple quick questions",
    "vision": "analyzing, describing or reading images, photos, screenshots or charts",
}


def recommend_questions() -> list[Question]:
    return [
        Choice(
            id="task",
            instructions="What kind of work does the request ask for?",
            candidates=[Candidate(id=t, description=TASK_DESCRIPTIONS[t]) for t in TASKS],
        ),
        Score(
            id="complexity",
            instructions="How demanding is the request for an AI model?",
            levels=[
                "Simple: a short, routine request that any basic assistant can handle.",
                "Moderate: several steps or some expertise, but a well-defined task.",
                "Complex: a large project such as a full app or game, or hard, long "
                "or ambiguous work needing expert-level skills.",
            ],
        ),
        # Execution risk is left out: choosing a model runs nothing, and on a local LLM
        # every extra question costs seconds.
        *(q for q in risk_questions() if q.id != "risk"),
    ]


class ClassificationCache:
    """Recent classifications by request text, so changing priority or models is instant.

    In memory only, bounded, and only successful classifications are kept.
    """

    def __init__(self, size: int = 64) -> None:
        self._size = size
        self._items: OrderedDict[str, DecisionResult] = OrderedDict()

    @staticmethod
    def _key(prompt: str, context: str) -> str:
        return hashlib.sha256(f"{prompt}\0{context}".encode()).hexdigest()

    def get(self, prompt: str, context: str) -> DecisionResult | None:
        key = self._key(prompt, context)
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        return None

    def put(self, prompt: str, context: str, result: DecisionResult) -> None:
        if result.decision is None:
            return
        self._items[self._key(prompt, context)] = result
        while len(self._items) > self._size:
            self._items.popitem(last=False)


class RecommendRequest(Model):
    prompt: str = Field(min_length=1, max_length=32000)
    context: str = Field(default="", max_length=16000)
    # Model IDs the user can use (subscriptions, API keys, downloaded models). None: all.
    available: list[Identifier] | None = Field(default=None, max_length=255)
    priority: Priority = "balanced"
    local_only: bool = False


class ModelOption(Model):
    strategy: Strategy
    model: str
    skill: int
    fits: bool


class CandidateRow(Model):
    model: str
    skill: int
    fits: bool
    excluded: Literal["unsupported_task", "not_local"] | None = None


class RecommendResult(Model):
    review_required: bool
    reasons: list[str]
    notes: list[str]
    task: Task | None
    second_task: Task | None
    complexity: int | None
    required_skill: int | None
    recommended: ModelOption | None
    alternatives: list[ModelOption]
    candidates: list[CandidateRow]
    evaluation: DecisionResult


def _task(answer: Answer | None) -> tuple[Task | None, Task | None, bool]:
    """Most likely task, runner-up, and whether the classifier was unsure between them."""
    if answer is None or answer.kind != "choice" or set(answer.probabilities) != set(TASKS):
        return None, None, False
    by_name: dict[str, Task] = {t: t for t in TASKS}
    runner_up = max(
        (t for t in TASKS if t != answer.selected), key=lambda t: answer.probabilities[t]
    )
    return by_name[answer.selected], runner_up, answer.confidence < UNCERTAIN_BELOW


def rank(
    pool: list[ModelProfile],
    task: Task,
    second_task: Task | None,
    required: int,
    priority: Priority,
) -> tuple[ModelOption | None, list[ModelOption], list[CandidateRow], list[str]]:
    """Pure ranking: same inputs, same answer. Ties break by cost, then speed, then name."""
    notes: list[str] = []

    def skill(m: ModelProfile) -> int:
        # When the task is ambiguous, the model must be good at both readings.
        return min(m.skills[task], m.skills[second_task]) if second_task else m.skills[task]

    rows = [
        CandidateRow(
            model=m.id,
            skill=skill(m),
            fits=skill(m) >= required,
            excluded="unsupported_task" if m.skills[task] == 0 else None,
        )
        for m in pool
    ]
    usable = [m for m in pool if m.skills[task] > 0]
    if not usable:
        return None, [], rows, ["no_suitable_models"]
    fits = [m for m in usable if skill(m) >= required]
    if not fits:
        notes.append("none_fits")

    def option(strategy: Strategy, m: ModelProfile) -> ModelOption:
        return ModelOption(strategy=strategy, model=m.id, skill=skill(m), fits=skill(m) >= required)

    best = min(usable, key=lambda m: (-skill(m), m.cost, -m.speed, m.id))
    economy = min(fits, key=lambda m: (m.cost, -skill(m), -m.speed, m.id)) if fits else best
    fastest = min(fits, key=lambda m: (-m.speed, m.cost, -skill(m), m.id)) if fits else best
    margin = [m for m in fits if skill(m) >= min(required + BALANCED_MARGIN, BALANCED_CEILING)]
    balanced = min(margin, key=lambda m: (m.cost, -m.speed, -skill(m), m.id)) if margin else economy
    choices: dict[Strategy, ModelProfile] = {
        "balanced": balanced,
        "economy": economy,
        "speed": fastest,
        "quality": best,
    }
    local = [m for m in usable if m.local]
    if local:
        local_fits = [m for m in local if skill(m) >= required]
        choices["local"] = (
            min(local_fits, key=lambda m: (-skill(m), -m.speed, m.id))
            if local_fits
            else min(local, key=lambda m: (-skill(m), -m.speed, m.id))
        )
    recommended = option(priority, choices[priority])
    alternatives = [option(s, m) for s, m in choices.items() if s != priority]
    return recommended, alternatives, rows, notes


async def recommend(
    request: RecommendRequest,
    engine: DecisionEngine,
    policy: DeterministicPolicy,
    catalog: CatalogService,
    cache: ClassificationCache | None = None,
) -> RecommendResult:
    notes: list[str] = []
    evaluation = cache.get(request.prompt, request.context) if cache else None
    if evaluation is not None:
        notes.append("classification_reused")
    else:
        evaluation = await engine.decide(
            DecisionRequest(
                prompt=request.prompt, context=request.context, questions=recommend_questions()
            )
        )
        if cache:
            cache.put(request.prompt, request.context, evaluation)
    view = await catalog.view()
    profiles = {m.id: m for m in view.models}
    # Default: every cloud model, plus local models only when Ollama reports them installed.
    wanted = (
        request.available
        if request.available is not None
        else [m.id for m in view.models if not m.local or m.installed]
    )
    if any(model_id not in profiles for model_id in wanted):
        notes.append("unknown_models_ignored")
    pool = [profiles[i] for i in dict.fromkeys(wanted) if i in profiles]
    excluded: list[CandidateRow] = []
    if request.local_only:
        excluded = [
            CandidateRow(model=m.id, skill=0, fits=False, excluded="not_local")
            for m in pool
            if not m.local
        ]
        pool = [m for m in pool if m.local]

    # Low task/complexity confidence escalates to a stronger model instead of blocking.
    # Choosing a model runs nothing, so execution risk informs but does not block;
    # injection and malicious intent (and missing evidence about them) still do.
    decision = evaluation.decision
    reasons = sorted(set(policy.safety_reasons(evaluation, include_risk=False)))
    if decision is None:
        reasons = ["decision_unavailable_or_uncertain", *reasons]
    task, second, task_uncertain = _task(decision.answers.get("task") if decision else None)
    complexity_answer = decision.answers.get("complexity") if decision else None
    complexity: int | None = None
    if complexity_answer is not None and complexity_answer.kind == "score":
        complexity = int(complexity_answer.selected)
        if complexity_answer.confidence < UNCERTAIN_BELOW and complexity < 2:
            complexity += 1
            notes.append("complexity_uncertain")
    if task_uncertain:
        notes.append("task_uncertain")
    if decision is not None and decision.provider == "fake":
        notes.append("demo_classifier")
    if decision is not None and (task is None or complexity is None):
        reasons.append("decision_unavailable_or_uncertain")

    empty = RecommendResult(
        review_required=bool(reasons),
        reasons=reasons,
        notes=notes,
        task=task,
        second_task=second if task_uncertain else None,
        complexity=complexity,
        required_skill=None,
        recommended=None,
        alternatives=[],
        candidates=excluded,
        evaluation=evaluation,
    )
    if task is None or complexity is None:
        return empty
    if not pool:
        return empty.model_copy(update={"notes": [*notes, "no_models_available"]})
    required = REQUIRED_SKILL[complexity]
    recommended, alternatives, rows, rank_notes = rank(
        pool, task, second if task_uncertain else None, required, request.priority
    )
    return empty.model_copy(
        update={
            "notes": [*notes, *rank_notes],
            "required_skill": required,
            # A flagged request gets no recommendation: a person must look first.
            "recommended": None if reasons else recommended,
            "alternatives": [] if reasons else alternatives,
            "candidates": rows + excluded,
        }
    )
