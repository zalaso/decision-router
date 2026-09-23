"""Optional adapter for GPT-AGI/OpenJev's local Hugging Face backend."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from time import perf_counter
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

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

OPENJEV_REVISION = "0e7bb990211df139da13dc67aa2a7aceca311af7"
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_MAX_REQUEST_CHARS = 12_000


class _Backend(Protocol):
    def load(self) -> None: ...

    def _first_token_id(self, label: str) -> int: ...

    def decide(self, request: Any, temperature: float = 1.0) -> Any: ...


class _ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability


class _NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: Probability


class _ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float = Field(allow_inf_nan=False)
    legend: dict[str, str]
    probabilities: dict[str, Probability]
    confidence: Probability


_WireAnswer = Annotated[_ChoiceAnswer | _NoulAnswer | _ScoreAnswer, Field(discriminator="type")]


class _Response(BaseModel):
    answers: dict[str, _WireAnswer]


def _prepare(
    request: DecisionRequest, max_pairs: int
) -> tuple[dict[str, object], dict[str, dict[str, str]], dict[str, list[str]]]:
    questions: dict[str, object] = {}
    label_maps: dict[str, dict[str, str]] = {}
    labels_by_question: dict[str, list[str]] = {}
    count = 0
    for question in request.questions:
        if isinstance(question, Choice):
            if len(question.candidates) > len(_LABELS):
                raise ProviderError(ErrorCode.UNSUPPORTED)
            labels = list(_LABELS[: len(question.candidates)])
            label_maps[question.id] = {
                label: candidate.id
                for label, candidate in zip(labels, question.candidates, strict=True)
            }
            criteria = "\n".join(
                f"{label}: {candidate.description}"
                for label, candidate in zip(labels, question.candidates, strict=True)
            )
            questions[question.id] = {
                "type": "choice",
                "instructions": f"{question.instructions}\nChoose the matching label:\n{criteria}",
                "options": labels,
            }
        elif isinstance(question, Boolean):
            labels = ["yes", "no"]
            questions[question.id] = {
                "type": "noul",
                "instructions": (
                    f"{question.instructions}\n"
                    f"yes: {question.true_description}\nno: {question.false_description}"
                ),
            }
        else:
            labels = [f"{index} ({level})" for index, level in enumerate(question.levels)]
            questions[question.id] = {
                "type": "score",
                "instructions": question.instructions,
                "criteria": question.levels,
            }
        labels_by_question[question.id] = labels
        count += len(labels)
    if count > max_pairs:
        raise ProviderError(ErrorCode.UNSUPPORTED)
    payload = {
        "state": {"prompt": request.prompt, "context": request.context},
        "questions": questions,
        "model": "openjev-latest",
    }
    if len(json.dumps(payload, ensure_ascii=False)) > _MAX_REQUEST_CHARS:
        raise ProviderError(ErrorCode.UNSUPPORTED)
    return payload, label_maps, labels_by_question


def _normalized(values: Mapping[str, float], expected: set[str]) -> dict[str, float]:
    if set(values) != expected:
        raise ValueError("Candidate mismatch")
    total = sum(values.values())
    # OpenJev rounds each class to 4 decimals; restore a valid distribution.
    if total <= 0 or abs(total - 1) > len(values) * 0.00005 + 1e-5:
        raise ValueError("Invalid rounded distribution")
    return {key: value / total for key, value in values.items()}


def _convert(
    request: DecisionRequest,
    wire_data: object,
    label_maps: dict[str, dict[str, str]],
    model: str,
    latency_ms: float,
) -> ProviderResult:
    try:
        wire = TypeAdapter(_Response).validate_python(wire_data)
        if set(wire.answers) != {question.id for question in request.questions}:
            raise ValueError("Question mismatch")
        answers: dict[str, Answer] = {}
        for question in request.questions:
            raw = wire.answers[question.id]
            if isinstance(question, Choice) and isinstance(raw, _ChoiceAnswer):
                label_map = label_maps[question.id]
                distribution = _normalized(raw.probabilities, set(label_map))
                probabilities = {label_map[key]: value for key, value in distribution.items()}
                answers[question.id] = Answer(
                    kind="choice",
                    selected=label_map[raw.choice],
                    probabilities=probabilities,
                    confidence=raw.confidence,
                    confidence_source="provider",
                )
            elif isinstance(question, Boolean) and isinstance(raw, _NoulAnswer):
                probabilities = {"false": 1 - raw.noul, "true": raw.noul}
                answers[question.id] = Answer(
                    kind="boolean",
                    selected=max(probabilities, key=probabilities.__getitem__),
                    probabilities=probabilities,
                    confidence=max(probabilities.values()),
                    confidence_source="max_probability",
                )
            elif not isinstance(question, (Choice, Boolean)) and isinstance(raw, _ScoreAnswer):
                expected_legend = {str(i): level for i, level in enumerate(question.levels)}
                if raw.legend != expected_legend:
                    raise ValueError("Score legend mismatch")
                probabilities = _normalized(raw.probabilities, set(expected_legend))
                score = sum(int(key) * value for key, value in probabilities.items())
                if not math.isclose(raw.score, score, abs_tol=0.002):
                    raise ValueError("Score mismatch")
                answers[question.id] = Answer(
                    kind="score",
                    selected=max(probabilities, key=probabilities.__getitem__),
                    probabilities=probabilities,
                    confidence=raw.confidence,
                    confidence_source="provider",
                    score=score,
                )
            else:
                raise ValueError("Question kind mismatch")
        return validate_result(
            request,
            ProviderResult(
                provider="openjev_hf",
                model=model,
                answers=answers,
                latency_ms=latency_ms,
                warnings=[
                    "openjev_first_token_logits_not_calibrated_for_routing",
                    "openjev_wrapper_not_typesafe_jev_weights",
                    "choice_limited_to_26_unique_labels",
                ],
            ),
        )
    except (ValidationError, ValueError, KeyError, TypeError):
        raise ProviderError(ErrorCode.INVALID_RESPONSE) from None


class OpenJevProvider:
    def __init__(
        self,
        *,
        model: str,
        revision: str,
        local_files_only: bool,
        temperature: float,
        max_pairs: int,
    ) -> None:
        self._model = model
        self._revision = revision
        self._local_files_only = local_files_only
        self._temperature = temperature
        self._max_pairs = max_pairs
        self._backend: _Backend | None = None

    def load(self) -> None:
        from huggingface_hub import snapshot_download
        from openjev.backends.hf import HFBackend

        snapshot = snapshot_download(
            repo_id=self._model,
            revision=self._revision,
            local_files_only=self._local_files_only,
            token=False,
            allow_patterns=["*.json", "*.safetensors", "*.model", "vocab.txt", "merges.txt"],
        )
        self._backend = HFBackend(model_id=snapshot)
        self._backend.load()

    def close(self) -> None:
        self._backend = None

    def _decide_sync(self, request: DecisionRequest) -> ProviderResult:
        start = perf_counter()
        if self._backend is None:
            raise ProviderError(ErrorCode.UNAVAILABLE)
        payload, label_maps, labels_by_question = _prepare(request, self._max_pairs)
        for labels in labels_by_question.values():
            token_ids = [self._backend._first_token_id(label) for label in labels]
            if len(set(token_ids)) != len(labels):
                raise ProviderError(ErrorCode.UNSUPPORTED)
        from openjev.core.primitives import SystemOneRequest

        response = self._backend.decide(
            SystemOneRequest.model_validate(payload), temperature=self._temperature
        )
        return _convert(
            request,
            response.model_dump(),
            label_maps,
            f"GPT-AGI/OpenJev@{OPENJEV_REVISION}+{self._model}@{self._revision}",
            (perf_counter() - start) * 1000,
        )
