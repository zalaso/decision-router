"""Local NLI decisions: a trained NLI model, not a reproduction of Jev."""

from __future__ import annotations

import asyncio
import math
from concurrent.futures import Future, ThreadPoolExecutor
from time import perf_counter
from typing import Protocol

from decision_router.domain import (
    Answer,
    Boolean,
    Choice,
    DecisionRequest,
    ErrorCode,
    ProviderError,
    ProviderResult,
    candidate_ids,
    validate_result,
)


class NliScorer(Protocol):
    def score(self, premise: str, hypotheses: list[str]) -> list[tuple[float, float]]:
        """Return (entailment logit, contradiction/not-entailment logit) per hypothesis."""
        ...


def _softmax(values: list[float], temperature: float) -> list[float]:
    if not values or not all(math.isfinite(v) for v in values):
        raise ProviderError(ErrorCode.INVALID_RESPONSE)
    maximum = max(values)
    weights = [math.exp((v - maximum) / temperature) for v in values]
    total = sum(weights)
    return [weight / total for weight in weights]


class LocalNliProvider:
    def __init__(
        self,
        scorer: NliScorer,
        *,
        model: str,
        revision: str,
        timeout_s: float = 5,
        temperature: float = 1,
        temperature_by_question: dict[str, float] | None = None,
        max_pairs: int = 64,
    ) -> None:
        self._scorer = scorer
        self._model = f"{model}@{revision}"
        self._timeout_s = timeout_s
        self._temperature = temperature
        self._temperature_by_question = temperature_by_question or {}
        self._max_pairs = max_pairs
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-nli")
        self._inflight: Future[ProviderResult] | None = None

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    async def decide(self, request: DecisionRequest) -> ProviderResult:
        if self._inflight is not None and not self._inflight.done():
            raise ProviderError(ErrorCode.UNAVAILABLE)
        # No await between availability check and submit: atomic within one event loop.
        self._inflight = self._executor.submit(self._decide_sync, request)
        wrapped = asyncio.wrap_future(self._inflight)
        # Retrieve abandoned exceptions after an outer timeout to avoid unsanitized loop logs.
        wrapped.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        try:
            return await asyncio.wait_for(asyncio.shield(wrapped), timeout=self._timeout_s)
        except TimeoutError:
            raise ProviderError(ErrorCode.TIMEOUT) from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError(ErrorCode.UNAVAILABLE) from None

    def _decide_sync(self, request: DecisionRequest) -> ProviderResult:
        start = perf_counter()
        hypotheses: list[str] = []
        spans: list[tuple[int, int]] = []
        for q in request.questions:
            begin = len(hypotheses)
            if isinstance(q, Boolean):
                hypotheses.append(f"{q.instructions} {q.true_description}")
            else:
                descriptions = (
                    [c.description for c in q.candidates] if isinstance(q, Choice) else q.levels
                )
                # NLI evaluates declarative hypotheses, not arbitrary instructions.
                # Complete descriptions define the task for Choice/Score (see warnings).
                template = "This request involves" if isinstance(q, Choice) else "The level is"
                hypotheses.extend(f"{template}: {description}." for description in descriptions)
            spans.append((begin, len(hypotheses)))
        if len(hypotheses) > self._max_pairs:
            raise ProviderError(ErrorCode.UNSUPPORTED)
        logits = self._scorer.score(
            f"Prompt: {request.prompt}\nOperational context: {request.context}", hypotheses
        )
        if len(logits) != len(hypotheses):
            raise ProviderError(ErrorCode.INVALID_RESPONSE)
        answers: dict[str, Answer] = {}
        for q, (begin, end) in zip(request.questions, spans, strict=True):
            ids = candidate_ids(q)
            temperature = self._temperature_by_question.get(q.id, self._temperature)
            if isinstance(q, Boolean):
                entailment, contradiction = logits[begin]
                probabilities = _softmax([contradiction, entailment], temperature)
            else:
                probabilities = _softmax([row[0] for row in logits[begin:end]], temperature)
            distribution = dict(zip(ids, probabilities, strict=True))
            answers[q.id] = Answer(
                kind=q.kind,
                selected=max(distribution, key=distribution.__getitem__),
                probabilities=distribution,
                confidence=max(probabilities),
                confidence_source="max_probability",
                score=sum(i * p for i, p in enumerate(probabilities))
                if q.kind == "score"
                else None,
            )
        return validate_result(
            request,
            ProviderResult(
                provider="local_nli",
                model=self._model,
                answers=answers,
                latency_ms=(perf_counter() - start) * 1000,
                warnings=[
                    "uncalibrated_nli_probabilities",
                    "boolean_uses_positive_rubric_only",
                    "score_is_categorical_nli_not_ordinal_training",
                    "choice_score_use_descriptions_not_instructions",
                ],
            ),
        )
