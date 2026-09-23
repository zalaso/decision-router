import argparse
import asyncio
import logging
import sys
from pathlib import Path

from decision_router.config import load_settings
from decision_router.domain import DecisionRequest
from decision_router.policy import DeterministicPolicy
from decision_router.routing import RouteRequest, route
from decision_router.runtime import runtime


async def _run(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    async with runtime(settings) as engine:
        if args.command == "route":
            result = await route(
                RouteRequest(prompt=args.prompt, context=args.context),
                engine,
                DeterministicPolicy(settings),
            )
            print(result.model_dump_json(indent=2))
            return 2 if result.policy.review_required else 0
        request = DecisionRequest.model_validate_json(args.file.read_text(encoding="utf-8"))
        evaluation = await engine.decide(request)
        print(evaluation.model_dump_json(indent=2))
        return 2 if evaluation.status == "human_review" else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Typed AI decision router")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    routing = commands.add_parser("route")
    routing.add_argument("prompt")
    routing.add_argument("--context", default="")
    decision = commands.add_parser("decide")
    decision.add_argument("file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    logging.getLogger("decision_router.audit").setLevel(logging.INFO)
    try:
        code = asyncio.run(_run(args))
    except Exception:
        # Config/provider library exceptions can contain credentials or user input.
        print('{"error":"startup_or_input_error"}', file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
