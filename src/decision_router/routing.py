from typing import Self

from pydantic import Field, model_validator

from decision_router.domain import Candidate, Choice, DecisionRequest, DecisionResult, Model
from decision_router.engine import DecisionEngine
from decision_router.policy import DeterministicPolicy, PolicyResult, risk_questions


def default_candidates() -> list[Candidate]:
    return [
        Candidate(
            id="coding_agent", description="writing Python code, programming or debugging software"
        ),
        Candidate(
            id="research_agent",
            description="research, searching papers or verifying facts and sources",
        ),
        Candidate(
            id="browser_agent",
            description="using a web browser to navigate pages, click or fill forms",
        ),
        Candidate(
            id="reasoning_agent", description="complex reasoning, math or logical problem solving"
        ),
        Candidate(
            id="fast_generalist",
            description="simple general questions, greetings or short summaries",
        ),
        Candidate(
            id="human_review", description="unsafe or ambiguous requests requiring human review"
        ),
    ]


class RouteRequest(Model):
    prompt: str = Field(min_length=1, max_length=32000)
    context: str = Field(default="", max_length=16000)
    candidates: list[Candidate] = Field(
        default_factory=default_candidates, min_length=2, max_length=255
    )

    @model_validator(mode="after")
    def valid_candidates(self) -> Self:
        Choice(id="route", instructions="route", candidates=self.candidates)
        if "human_review" not in {c.id for c in self.candidates}:
            raise ValueError("Routing candidates must include human_review for safe fallback")
        return self

    def as_decision(self) -> DecisionRequest:
        return DecisionRequest(
            prompt=self.prompt,
            context=self.context,
            questions=[
                Choice(
                    id="route",
                    instructions="Which agent best fits this request?",
                    candidates=self.candidates,
                ),
                *risk_questions(),
            ],
        )


class RouteResult(Model):
    policy: PolicyResult
    evaluation: DecisionResult


async def route(
    request: RouteRequest, engine: DecisionEngine, policy: DeterministicPolicy
) -> RouteResult:
    result = await engine.decide(request.as_decision())
    return RouteResult(policy=policy.evaluate(result), evaluation=result)
