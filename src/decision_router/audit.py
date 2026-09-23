"""Allowlist-only JSON logging. Arbitrary candidate/question names never enter logs."""

import json
import logging
from typing import Protocol

from decision_router.domain import DecisionResult


class AuditSink(Protocol):
    def record(self, result: DecisionResult) -> None: ...


class JsonAudit:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def record(self, result: DecisionResult) -> None:
        # Ordinals preserve distributions and comparisons without logging caller-defined strings.
        attempts: list[dict[str, object]] = []
        for attempt in result.attempts:
            entry: dict[str, object] = {
                "slot": attempt.slot,
                "latency_ms": attempt.latency_ms,
                "error": attempt.error.value if attempt.error else None,
            }
            if attempt.result:
                entry["answers"] = [
                    {
                        "question_index": i,
                        "selected_index": sorted(a.probabilities).index(a.selected),
                        "probabilities": [a.probabilities[k] for k in sorted(a.probabilities)],
                        "confidence": a.confidence,
                        "confidence_source": a.confidence_source,
                    }
                    for i, (_, a) in enumerate(sorted(attempt.result.answers.items()))
                ]
            attempts.append(entry)
        self._logger.info(
            json.dumps(
                {
                    "event": "decision",
                    "status": result.status,
                    "latency_ms": result.latency_ms,
                    "attempts": attempts,
                    "fallback_reason": result.fallback_reason,
                    "agreement": [
                        result.shadow.agreement[k] for k in sorted(result.shadow.agreement)
                    ]
                    if result.shadow and result.shadow.agreement is not None
                    else None,
                },
                allow_nan=False,
            )
        )
