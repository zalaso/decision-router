import argparse
import asyncio
import logging
import os
import sys
import threading
import webbrowser
from ipaddress import ip_address
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


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _serve(args: argparse.Namespace) -> int:
    import uvicorn

    if args.config is not None:
        os.environ["ROUTER_CONFIG"] = str(args.config)
    try:
        settings = load_settings(args.config)
    except Exception:
        print('{"error":"startup_or_input_error"}', file=sys.stderr)
        return 1
    if not _is_loopback(args.host) and not settings.api_token.get_secret_value():
        print(
            "Refusing to listen beyond loopback without ROUTER_API_TOKEN; see docs/SECURITY.md.",
            file=sys.stderr,
        )
        return 1
    host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{host}:{args.port}/" + ("dashboard/" if settings.dashboard else "docs")
    print(f"Decision Router: {url}  (Ctrl+C to stop)", file=sys.stderr)
    if args.open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(
        "decision_router.api:app_factory",
        factory=True,
        host=args.host,
        port=args.port,
        log_level="warning",
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Typed AI decision router")
    parser.add_argument("--config", type=Path)
    # Also accept --config after the subcommand; SUPPRESS keeps an earlier value.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    routing = commands.add_parser("route", parents=[common], help="suggest an agent for a prompt")
    routing.add_argument("prompt")
    routing.add_argument("--context", default="")
    decision = commands.add_parser(
        "decide", parents=[common], help="evaluate a DecisionRequest JSON file"
    )
    decision.add_argument("file", type=Path)
    serve = commands.add_parser(
        "serve", parents=[common], help="start the HTTP API and the dashboard"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s", stream=sys.stderr)
    logging.getLogger("decision_router.audit").setLevel(logging.INFO)
    if args.command == "serve":
        raise SystemExit(_serve(args))
    try:
        code = asyncio.run(_run(args))
    except Exception:
        # Config/provider library exceptions can contain credentials or user input.
        print('{"error":"startup_or_input_error"}', file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
