"""A deterministic development double, deliberately not an AI/security classifier."""

import re
from time import perf_counter

from decision_router.domain import (
    Answer,
    Boolean,
    Choice,
    DecisionRequest,
    ProviderResult,
    candidate_ids,
)

_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or the this to with you your".split()
)


def _stems(text: str) -> set[str]:
    # Crude 5-letter prefixes so "describe" meets "describing"; still only keyword matching.
    return {w[:5] for w in re.findall(r"\w+", text.lower()) if w not in _STOPWORDS}


class FakeProvider:
    async def decide(self, request: DecisionRequest) -> ProviderResult:
        start = perf_counter()
        words = set(re.findall(r"\w+", (request.prompt + " " + request.context).lower()))
        stems = _stems(request.prompt + " " + request.context)
        answers: dict[str, Answer] = {}
        for question in request.questions:
            ids = candidate_ids(question)
            winner = ids[0]
            if isinstance(question, Choice):
                winner = max(
                    question.candidates,
                    key=lambda c: len(stems & _stems(c.description)),
                ).id
            elif isinstance(question, Boolean):
                winner = "true" if words & {"steal", "injection", "malware"} else "false"
            probabilities = {key: 0.1 / (len(ids) - 1) for key in ids}
            probabilities[winner] = 0.9
            answers[question.id] = Answer(
                kind=question.kind,
                selected=winner,
                probabilities=probabilities,
                confidence=0.9,
                confidence_source="synthetic",
                score=sum(int(k) * p for k, p in probabilities.items())
                if question.kind == "score"
                else None,
            )
        return ProviderResult(
            provider="fake",
            model="deterministic-v1",
            answers=answers,
            latency_ms=(perf_counter() - start) * 1000,
            warnings=["synthetic_probabilities_not_for_production"],
        )
