"""The provider-independent decision vocabulary. No vendor types live here."""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
Text = Annotated[str, Field(min_length=1, max_length=4096)]
Probability = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class Candidate(Model):
    id: Identifier
    description: Text


class QuestionBase(Model):
    id: Identifier
    instructions: Text


class Choice(QuestionBase):
    kind: Literal["choice"] = "choice"
    candidates: list[Candidate] = Field(min_length=2, max_length=255)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError("Candidate IDs must be unique")
        return self


class Boolean(QuestionBase):
    kind: Literal["boolean"] = "boolean"
    true_description: Text = "The statement is true."
    false_description: Text = "The statement is false."


class Score(QuestionBase):
    kind: Literal["score"] = "score"
    levels: list[Text] = Field(min_length=2, max_length=10)


Question = Annotated[Choice | Boolean | Score, Field(discriminator="kind")]


class DecisionRequest(Model):
    prompt: str = Field(min_length=1, max_length=32000)
    context: str = Field(default="", max_length=16000)
    questions: list[Question] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("Question IDs must be unique")
        return self


def candidate_ids(question: Question) -> list[str]:
    if isinstance(question, Choice):
        return [c.id for c in question.candidates]
    if isinstance(question, Boolean):
        return ["false", "true"]
    return [str(i) for i in range(len(question.levels))]


class Answer(Model):
    kind: Literal["choice", "boolean", "score"]
    selected: str
    probabilities: dict[str, Probability]
    confidence: Probability
    confidence_source: Literal["provider", "max_probability", "synthetic"]
    score: float | None = None

    @model_validator(mode="after")
    def distribution(self) -> Self:
        if len(self.probabilities) < 2 or self.selected not in self.probabilities:
            raise ValueError("Missing candidates or selected candidate")
        if not math.isclose(sum(self.probabilities.values()), 1, abs_tol=1e-5):
            raise ValueError("Probabilities must sum to one")
        if self.probabilities[self.selected] < max(self.probabilities.values()) - 1e-8:
            raise ValueError("Selected candidate must maximize probability")
        if self.kind == "boolean" and set(self.probabilities) != {"false", "true"}:
            raise ValueError("Boolean requires false/true probabilities")
        if self.kind == "score":
            if set(self.probabilities) != {str(i) for i in range(len(self.probabilities))}:
                raise ValueError("Score requires contiguous ordered indices")
            mean = sum(int(k) * p for k, p in self.probabilities.items())
            if self.score is None or not math.isclose(self.score, mean, abs_tol=1e-5):
                raise ValueError("Score must be the expected ordinal index")
        elif self.score is not None:
            raise ValueError("Only Score answers have a score")
        return self


class ProviderResult(Model):
    provider: str
    model: str
    answers: dict[str, Answer]
    latency_ms: float = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


def validate_result(request: DecisionRequest, result: ProviderResult) -> ProviderResult:
    if set(result.answers) != {q.id for q in request.questions}:
        raise ValueError("Provider must answer every question exactly once")
    for q in request.questions:
        answer = result.answers[q.id]
        if answer.kind != q.kind or set(answer.probabilities) != set(candidate_ids(q)):
            raise ValueError("Provider answer does not match question")
    return result


class ErrorCode(StrEnum):
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    UNSUPPORTED = "unsupported_input"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(Exception):
    """Safe error: never stores response bodies, URLs, prompts or credentials."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class Attempt(Model):
    slot: Literal["local", "cloud"]
    result: ProviderResult | None = None
    error: ErrorCode | None = None
    latency_ms: float = Field(ge=0)


class ShadowComparison(Model):
    primary: Literal["local", "cloud"]
    agreement: dict[str, bool] | None = None


class DecisionResult(Model):
    status: Literal["decided", "human_review"]
    decision: ProviderResult | None
    attempts: list[Attempt]
    fallback_reason: str | None = None
    shadow: ShadowComparison | None = None
    latency_ms: float = Field(ge=0)
