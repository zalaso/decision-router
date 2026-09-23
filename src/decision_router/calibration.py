"""Offline calibration on labeled distributions; never changes runtime policy itself."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Literal, Self

from pydantic import Field, model_validator

from decision_router.domain import Model, Probability


class LabeledPrediction(Model):
    sample_id: str = Field(min_length=1, max_length=128)
    split: Literal["calibration", "test"]
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    kind: Literal["choice", "boolean", "score"]
    expected: str
    probabilities: dict[str, Probability] = Field(min_length=2)
    source_temperature: Literal[1] = 1

    @model_validator(mode="after")
    def valid_distribution(self) -> Self:
        if self.expected not in self.probabilities:
            raise ValueError("Expected label absent from distribution")
        if not math.isclose(sum(self.probabilities.values()), 1, abs_tol=1e-5):
            raise ValueError("Probabilities must sum to one")
        if self.kind == "boolean" and set(self.probabilities) != {"false", "true"}:
            raise ValueError("Boolean labels must be false/true")
        if self.kind == "score" and set(self.probabilities) != {
            str(i) for i in range(len(self.probabilities))
        }:
            raise ValueError("Score labels must be ordered indices")
        return self


def rescale(probabilities: dict[str, float], temperature: float) -> dict[str, float]:
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("Temperature must be finite and positive")
    # Probabilities produced at T=1 are sufficient to reconstruct relative logits.
    logits = {
        key: math.log(max(value, 1e-15)) / temperature for key, value in probabilities.items()
    }
    maximum = max(logits.values())
    weights = {key: math.exp(value - maximum) for key, value in logits.items()}
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()}


def _nll(samples: list[LabeledPrediction], temperature: float) -> float:
    return sum(
        -math.log(max(rescale(s.probabilities, temperature)[s.expected], 1e-15)) for s in samples
    ) / len(samples)


def fit_temperature(samples: list[LabeledPrediction]) -> float:
    if not samples or any(s.split != "calibration" for s in samples):
        raise ValueError("Temperature fitting requires calibration samples only")
    # Deterministic one-dimensional search in log temperature, no SciPy dependency.
    left, right = math.log(0.1), math.log(20.0)
    for _ in range(70):
        first = left + (right - left) / 3
        second = right - (right - left) / 3
        if _nll(samples, math.exp(first)) <= _nll(samples, math.exp(second)):
            right = second
        else:
            left = first
    return math.exp((left + right) / 2)


def _metrics(
    samples: list[LabeledPrediction], temperature: float, positive_labels: set[str]
) -> dict[str, object]:
    rows = []
    for sample in samples:
        probs = rescale(sample.probabilities, temperature)
        selected = max(probs, key=probs.__getitem__)
        confidence = probs[selected]
        rows.append((sample.expected, selected, confidence, probs))
    n = len(rows)
    bins = []
    for index in range(10):
        group = [row for row in rows if min(int(row[2] * 10), 9) == index]
        if group:
            bins.append(
                (
                    len(group),
                    sum(row[2] for row in group) / len(group),
                    sum(row[0] == row[1] for row in group) / len(group),
                )
            )
    coverage = []
    for threshold in (0.55, 0.8, 0.9):
        accepted = [row for row in rows if row[2] >= threshold]
        coverage.append(
            {
                "threshold": threshold,
                "count": len(accepted),
                "coverage": len(accepted) / n,
                "error_rate": sum(row[0] != row[1] for row in accepted) / len(accepted)
                if accepted
                else None,
            }
        )
    positive = None
    if positive_labels:
        positive = []
        for threshold in (0.1, 0.2, 0.3, 0.5):
            tp = fn = fp = tn = 0
            for expected, _, _, probs in rows:
                predicted_positive = sum(probs.get(key, 0) for key in positive_labels) >= threshold
                actual_positive = expected in positive_labels
                tp += predicted_positive and actual_positive
                fn += (not predicted_positive) and actual_positive
                fp += predicted_positive and (not actual_positive)
                tn += (not predicted_positive) and (not actual_positive)
            positive.append(
                {
                    "threshold": threshold,
                    "tp": tp,
                    "fn": fn,
                    "fp": fp,
                    "tn": tn,
                    "recall": tp / (tp + fn) if tp + fn else None,
                }
            )
    return {
        "count": n,
        "accuracy": sum(row[0] == row[1] for row in rows) / n,
        "nll": _nll(samples, temperature),
        "brier": sum(
            sum((value - int(key == expected)) ** 2 for key, value in probs.items())
            for expected, _, _, probs in rows
        )
        / n,
        "ece_10_bins": sum(count * abs(acc - prob) for count, prob, acc in bins) / n,
        "selective": coverage,
        "positive_detection": positive,
    }


def calibration_report(
    samples: Iterable[LabeledPrediction], *, positive_labels: set[str] | None = None
) -> dict[str, object]:
    rows = list(samples)
    calibration = [sample for sample in rows if sample.split == "calibration"]
    test = [sample for sample in rows if sample.split == "test"]
    if not calibration or not test:
        raise ValueError("Both calibration and test splits are required")
    if len({sample.sample_id for sample in rows}) != len(rows):
        raise ValueError("Sample IDs must be unique across splits")
    if len({(s.model, s.revision, s.question_id, s.kind, len(s.probabilities)) for s in rows}) != 1:
        raise ValueError("Use one model, revision, question and candidate count per report")
    positives = positive_labels or set()
    if positives and not positives.issubset(set(rows[0].probabilities)):
        raise ValueError("Positive labels must be candidates")
    temperature = fit_temperature(calibration)
    return {
        "model": rows[0].model,
        "revision": rows[0].revision,
        "question_id": rows[0].question_id,
        "kind": rows[0].kind,
        "candidate_count": len(rows[0].probabilities),
        "fitted_temperature": temperature,
        "calibration_count": len(calibration),
        "test_count": len(test),
        "test_before": _metrics(test, 1.0, positives),
        "test_after": _metrics(test, temperature, positives),
        "insufficient_sample_size": len(calibration) < 50 or len(test) < 50,
        "production_ready": False,
        "note": "Representativeness, risk tolerance and operating thresholds require human review.",
    }
