"""Small reproducible decision benchmark; no cloud call unless explicitly configured."""

import argparse
import asyncio
import json
import math
import platform
import statistics
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter

from decision_router.calibration import LabeledPrediction
from decision_router.config import load_settings
from decision_router.policy import DeterministicPolicy
from decision_router.routing import RouteRequest
from decision_router.runtime import runtime


def metrics(rows):
    successful = [r for r in rows if r["predicted"] is not None]
    accepted = [r for r in successful if r["status"] == "decided"]
    comparisons = [r["agreement"] for r in rows if r["agreement"] is not None]
    latencies = sorted(r["latency_ms"] for r in rows)
    bins = []
    for index in range(10):
        group = [r for r in successful if min(int(r["top_probability"] * 10), 9) == index]
        if group:
            bins.append(
                {
                    "bin": index,
                    "count": len(group),
                    "mean_probability": statistics.mean(r["top_probability"] for r in group),
                    "accuracy": statistics.mean(r["correct"] for r in group),
                }
            )
    return {
        "n": len(rows),
        "answered": len(successful),
        "accepted": len(accepted),
        "accuracy_all": sum(r["correct"] for r in rows) / len(rows),
        "accuracy_answered": statistics.mean(r["correct"] for r in successful)
        if successful
        else None,
        "accuracy_accepted": statistics.mean(r["correct"] for r in accepted) if accepted else None,
        "coverage": len(accepted) / len(rows),
        "agreement": statistics.mean(comparisons) if comparisons else None,
        "comparison_count": len(comparisons),
        "latency_ms_mean": statistics.mean(latencies),
        "latency_ms_p50": statistics.median(latencies),
        "latency_ms_p95": latencies[max(0, math.ceil(0.95 * len(latencies)) - 1)],
        "brier_multiclass": statistics.mean(r["brier"] for r in successful) if successful else None,
        "nll": statistics.mean(r["nll"] for r in successful) if successful else None,
        "ece_10_bins": sum(b["count"] * abs(b["accuracy"] - b["mean_probability"]) for b in bins)
        / len(successful)
        if successful
        else None,
        "reliability_bins": bins,
    }


async def run(args):
    settings = load_settings(args.config)
    samples = [
        json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line
    ]
    if not samples:
        raise ValueError("Empty benchmark")
    if args.export_labeled:
        if settings.mode != "local" or settings.local_backend != "nli":
            raise ValueError("Labeled export requires mode=local with the NLI backend")
        if settings.temperature_by_question.get(args.question_id, settings.temperature) != 1:
            raise ValueError("Labeled export requires temperature=1 for the selected question")
        if any(
            not all(key in sample for key in ("id", "split", args.label_field))
            for sample in samples
        ):
            raise ValueError("Every export sample needs id, split and the selected label field")
    rows = []
    labeled: list[LabeledPrediction] = []
    policy = DeterministicPolicy(settings)
    load_started = perf_counter()
    async with AsyncExitStack() as stack:
        engine = await stack.enter_async_context(runtime(settings))
        load_ms = (perf_counter() - load_started) * 1000
        comparison_engine = None
        if args.compare_config:
            comparison_engine = await stack.enter_async_context(
                runtime(load_settings(args.compare_config))
            )
        for sample in samples:
            route_input = {
                key: sample[key] for key in ("prompt", "context", "candidates") if key in sample
            }
            request = RouteRequest.model_validate(route_input).as_decision()
            result = await engine.decide(request)
            answer = result.decision.answers["route"] if result.decision else None
            if args.export_labeled:
                if result.decision is None:
                    raise ValueError("A decision was unavailable; labeled export would be biased")
                selected_answer = result.decision.answers[args.question_id]
                labeled.append(
                    LabeledPrediction(
                        sample_id=sample["id"],
                        split=sample["split"],
                        model=result.decision.model,
                        revision=settings.local_revision,
                        question_id=args.question_id,
                        kind=selected_answer.kind,
                        expected=sample[args.label_field],
                        probabilities=selected_answer.probabilities,
                    )
                )
            expected = sample["expected"]
            row = {
                "index": len(rows),
                "expected": expected,
                "predicted": answer.selected if answer else None,
                "status": result.status,
                "correct": bool(answer and answer.selected == expected),
                "latency_ms": result.latency_ms,
                "policy_review_required": policy.evaluate(result).review_required,
                "agreement": result.shadow.agreement["route"]
                if result.shadow and result.shadow.agreement
                else None,
            }
            if comparison_engine:
                other = await comparison_engine.decide(request)
                row["comparison_latency_ms"] = other.latency_ms
                if answer and other.decision:
                    row["agreement"] = answer.selected == other.decision.answers["route"].selected
                    row["comparison_predicted"] = other.decision.answers["route"].selected
            if answer:
                row.update(
                    {
                        "top_probability": max(answer.probabilities.values()),
                        "confidence": answer.confidence,
                        "brier": sum(
                            (p - int(k == expected)) ** 2 for k, p in answer.probabilities.items()
                        ),
                        "nll": -math.log(max(answer.probabilities.get(expected, 0), 1e-15)),
                    }
                )
            rows.append(row)
    report = {
        "notice": (
            "Representativeness is not established by this tool; verify the data source and split."
        ),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "mode": settings.mode,
        "backend": settings.local_backend,
        "model": settings.local_model if settings.local_backend == "nli" else "deterministic-v1",
        "revision": settings.local_revision if settings.local_backend == "nli" else None,
        "temperature": settings.temperature,
        "startup_ms": load_ms,
        "comparison_config": str(args.compare_config) if args.compare_config else None,
        "first_request_ms": rows[0]["latency_ms"],
        "warm_latency_ms_mean": statistics.mean(r["latency_ms"] for r in rows[1:])
        if len(rows) > 1
        else None,
        "metrics": metrics(rows),
        "policy_coverage": sum(not r["policy_review_required"] for r in rows) / len(rows),
        "samples": rows,
    }
    output = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    if args.export_labeled:
        args.export_labeled.write_text(
            "\n".join(record.model_dump_json() for record in labeled) + "\n",
            encoding="utf-8",
        )
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/example.yaml"))
    parser.add_argument("--data", type=Path, default=Path(__file__).with_name("data.jsonl"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--compare-config", type=Path)
    parser.add_argument("--export-labeled", type=Path)
    parser.add_argument("--question-id", default="route")
    parser.add_argument("--label-field", default="expected")
    asyncio.run(run(parser.parse_args()))
