"""System One HTTP contract shared by Jev Cloud and compatible local servers."""

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


class _SystemOneHttpProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        model: str,
        timeout_s: float,
        *,
        endpoint: str,
        provider_name: str,
        require_api_key: bool,
        provider_warnings: list[str] | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._endpoint = endpoint
        self._provider_name = provider_name
        self._require_api_key = require_api_key
        self._provider_warnings = provider_warnings or []

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        start = perf_counter()
        key = self._api_key.get_secret_value()
        if self._require_api_key and not key:
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
                self._endpoint,
                headers={"Authorization": f"Bearer {key}"} if key else {},
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
                    provider=self._provider_name,
                    model=wire.model,
                    answers=answers,
                    latency_ms=(perf_counter() - start) * 1000,
                    warnings=self._provider_warnings
                    + (
                        ["boolean_confidence_derived_from_max_probability"]
                        if any(isinstance(q, Boolean) for q in request.questions)
                        else []
                    ),
                ),
            )
        except (ValidationError, ValueError, KeyError, TypeError):
            raise ProviderError(ErrorCode.INVALID_RESPONSE) from None


class JevCloudProvider(_SystemOneHttpProvider):
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: SecretStr,
        model: str = "jev-latest",
        timeout_s: float = 5.0,
    ) -> None:
        super().__init__(
            client,
            api_key,
            model,
            timeout_s,
            endpoint="https://api.typesafe.ai/v1/systemone",
            provider_name="jev_cloud",
            require_api_key=True,
        )
