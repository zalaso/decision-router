"""Official TypeSafe HTTP contract, isolated from the domain vocabulary."""

from time import perf_counter
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError

from decision_router.domain import (
    Answer,
    Boolean,
    Choice,
    DecisionRequest,
    ErrorCode,
    Probability,
    ProviderError,
    ProviderResult,
    validate_result,
)


class _Choice(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability


class _Noul(BaseModel):
    type: Literal["noul"]
    noul: Probability


class _Score(BaseModel):
    type: Literal["score"]
    score: float = Field(allow_inf_nan=False)
    probabilities: dict[str, Probability]
    confidence: Probability
    legend: dict[str, str]


_WireAnswer = Annotated[_Choice | _Noul | _Score, Field(discriminator="type")]


class _Response(BaseModel):
    model: str = Field(min_length=1, max_length=256)
    answers: dict[str, _WireAnswer]


class JevCloudProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        model: str = "jev-latest",
        timeout_s: float = 5.0,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        start = perf_counter()
        if not self._api_key.get_secret_value():
            raise ProviderError(ErrorCode.AUTHENTICATION)
        questions: dict[str, object] = {}
        for q in request.questions:
            if isinstance(q, Choice):
                criteria: object = {c.id: c.description for c in q.candidates}
            elif isinstance(q, Boolean):
                criteria = {"true": q.true_description, "false": q.false_description}
            else:
                criteria = q.levels
            questions[q.id] = {
                "type": "noul" if q.kind == "boolean" else q.kind,
                "instructions": q.instructions,
                "criteria": criteria,
            }
        try:
            response = await self._client.post(
                "https://api.typesafe.ai/v1/systemone",
                headers={"Authorization": f"Bearer {self._api_key.get_secret_value()}"},
                json={
                    "model": self._model,
                    "state": {
                        "prompt": request.prompt,
                        "context": request.context,
                    },
                    "questions": questions,
                },
                timeout=self._timeout_s,
                follow_redirects=False,
            )
        except httpx.TimeoutException:
            raise ProviderError(ErrorCode.TIMEOUT) from None
        except httpx.HTTPError:
            raise ProviderError(ErrorCode.UNAVAILABLE) from None
        if response.status_code != 200:
            code = {
                401: ErrorCode.AUTHENTICATION,
                403: ErrorCode.AUTHENTICATION,
                422: ErrorCode.UNSUPPORTED,
                400: ErrorCode.UNSUPPORTED,
                429: ErrorCode.RATE_LIMIT,
            }.get(response.status_code, ErrorCode.UNAVAILABLE)
            raise ProviderError(code)
        try:
            wire = TypeAdapter(_Response).validate_json(response.content)
            answers: dict[str, Answer] = {}
            for q in request.questions:
                raw = wire.answers[q.id]
                if isinstance(raw, _Noul):
                    probabilities = {"false": 1 - raw.noul, "true": raw.noul}
                    answer = Answer(
                        kind="boolean",
                        selected=max(probabilities, key=probabilities.__getitem__),
                        probabilities=probabilities,
                        confidence=max(probabilities.values()),
                        confidence_source="max_probability",
                    )
                else:
                    if isinstance(raw, _Score):
                        if q.kind != "score" or raw.legend != {
                            str(i): level for i, level in enumerate(q.levels)
                        }:
                            raise ValueError("Unexpected score legend")
                    answer = Answer(
                        kind=raw.type,
                        selected=raw.choice
                        if isinstance(raw, _Choice)
                        else max(raw.probabilities, key=raw.probabilities.__getitem__),
                        probabilities=raw.probabilities,
                        confidence=raw.confidence,
                        confidence_source="provider",
                        score=raw.score if isinstance(raw, _Score) else None,
                    )
                answers[q.id] = answer
            if set(wire.answers) != set(answers):
                raise ValueError("Unexpected answers")
            return validate_result(
                request,
                ProviderResult(
                    provider="jev_cloud",
                    model=wire.model,
                    answers=answers,
                    latency_ms=(perf_counter() - start) * 1000,
                    warnings=["boolean_confidence_derived_from_max_probability"]
                    if any(isinstance(q, Boolean) for q in request.questions)
                    else [],
                ),
            )
        except (ValidationError, ValueError, KeyError, TypeError):
            raise ProviderError(ErrorCode.INVALID_RESPONSE) from None
