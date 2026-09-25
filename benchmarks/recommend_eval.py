"""Measure how well a classifier reads requests for model recommendation.

Runs the recommend questions (task, complexity, injection, malicious) on a labeled
JSONL set and reports task accuracy, complexity accuracy and safety errors. Items
list every acceptable answer, because some requests fairly fit two readings.

    python benchmarks/recommend_eval.py --config config/ollama.yaml
    python benchmarks/recommend_eval.py --config config/ollama.yaml --questions task,complexity
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from time import perf_counter

from decision_router.config import load_settings
from decision_router.domain import DecisionRequest
from decision_router.policy import DeterministicPolicy
from decision_router.recommend import UNCERTAIN_BELOW, recommend_questions
from decision_router.runtime import runtime

ROOT = Path(__file__).parent


async def evaluate(args: argparse.Namespace) -> dict[str, object]:
    settings = load_settings(args.config)
    policy = DeterministicPolicy(settings)
    wanted = set(args.questions.split(",")) if args.questions else None
    questions = [q for q in recommend_questions() if wanted is None or q.id in wanted]
    items = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines()]
    if args.limit:
        items = items[: args.limit]
    rows: list[dict[str, object]] = []
    async with runtime(settings) as engine:
        for item in items:
            if "blocked" in item and not any(q.id in ("injection", "malicious") for q in questions):
                continue
            start = perf_counter()
            result = await engine.decide(
                DecisionRequest(prompt=item["prompt"], questions=questions)
            )
            row: dict[str, object] = {
                "prompt": item["prompt"],
                "seconds": round(perf_counter() - start, 1),
            }
            decision = result.decision
            if decision is None:
                row["error"] = [a.error for a in result.attempts]
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
                continue
            answers = decision.answers
            if "task" in answers and "task" in item:
                task = answers["task"]
                row["task"] = task.selected
                row["task_confidence"] = round(task.confidence, 2)
                row["task_ok"] = task.selected in item["task"]
            if "complexity" in answers and "complexity" in item:
                complexity = answers["complexity"]
                level = int(complexity.selected)
                # The router plans one level higher when complexity is uncertain.
                effective = (
                    level + 1 if complexity.confidence < UNCERTAIN_BELOW and level < 2 else level
                )
                row["complexity"] = level
                row["complexity_confidence"] = round(complexity.confidence, 2)
                row["complexity_ok"] = effective in item["complexity"]
            if "injection" in answers or "malicious" in answers:
                reasons = policy.safety_reasons(result, include_risk=False)
                row["blocked"] = bool(reasons)
                row["reasons"] = sorted(set(reasons))
                row["safety_ok"] = row["blocked"] == item.get("blocked", False)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

    def rate(key: str) -> str:
        graded = [r[key] for r in rows if key in r]
        return f"{sum(graded)}/{len(graded)}" if graded else "n/a"

    summary = {
        "config": str(args.config),
        "model": settings.ollama_model
        if settings.local_backend == "ollama"
        else settings.local_model,
        "backend": settings.local_backend,
        "task": rate("task_ok"),
        "complexity": rate("complexity_ok"),
        "safety": rate("safety_ok"),
        "errors": sum("error" in r for r in rows),
        "mean_seconds": round(sum(float(r["seconds"]) for r in rows) / max(len(rows), 1), 1),
    }
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return {"summary": summary, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, default=Path("config/ollama.yaml"))
    parser.add_argument("--data", type=Path, default=ROOT / "recommend-labeled.jsonl")
    parser.add_argument("--questions", help="comma-separated subset, e.g. task,complexity")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    report = asyncio.run(evaluate(args))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
