"""Bounded live TypeSafe comparison. Requires an already configured API key."""

import argparse
import asyncio
import json
from pathlib import Path

from decision_router.config import load_settings
from decision_router.routing import RouteRequest
from decision_router.runtime import runtime


async def run(config: Path, data: Path, limit: int) -> dict[str, object]:
    settings = load_settings(config)
    if not settings.typesafe_api_key.get_secret_value():
        return {"status": "not_run", "reason": "TYPESAFE_API_KEY_missing"}
    settings = settings.model_copy(update={"mode": "shadow"})
    content = await asyncio.to_thread(data.read_text, encoding="utf-8")
    samples = [json.loads(line) for line in content.splitlines() if line]
    results = []
    async with runtime(settings) as engine:
        for sample in samples[:limit]:
            decision = await engine.decide(RouteRequest(prompt=sample["prompt"]).as_decision())
            results.append(
                {
                    "index": len(results),
                    "expected": sample["expected"],
                    "effective_status": decision.status,
                    "fallback_reason": decision.fallback_reason,
                    "agreement": decision.shadow.agreement if decision.shadow else None,
                    "attempts": [
                        {
                            "slot": attempt.slot,
                            "error": attempt.error.value if attempt.error else None,
                            "latency_ms": attempt.latency_ms,
                            "model": attempt.result.model if attempt.result else None,
                            "selected": attempt.result.answers["route"].selected
                            if attempt.result
                            else None,
                            "confidence": attempt.result.answers["route"].confidence
                            if attempt.result
                            else None,
                            "distribution": attempt.result.answers["route"].probabilities
                            if attempt.result
                            else None,
                        }
                        for attempt in decision.attempts
                    ],
                }
            )
    comparable = [row for row in results if row["agreement"] is not None]
    return {
        "status": "completed",
        "primary": settings.shadow_primary,
        "model": settings.cloud_model,
        "count": len(results),
        "comparable": len(comparable),
        "agreement_rate": sum(row["agreement"]["route"] for row in comparable) / len(comparable)
        if comparable
        else None,
        "results": results,
        "note": "Synthetic examples; cloud requests may incur provider charges.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/local.yaml"))
    parser.add_argument("--data", type=Path, default=Path("benchmarks/data.jsonl"))
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.limit <= 10:
        parser.error("--limit must be between 1 and 10")
    try:
        report = asyncio.run(run(args.config, args.data, args.limit))
    except Exception:
        parser.exit(1, "Shadow check failed; provider details were not logged.\n")
    output = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
