"""Command-line launcher for guarded stdio and shared HTTP broker modes."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from telegram_mcp.connection_guard import exec_guarded, print_status


def _default_server_command() -> list[str]:
    project_main = Path(__file__).resolve().parents[1] / "main.py"
    if project_main.is_file():
        return [sys.executable, str(project_main)]
    return [sys.executable, "-m", "main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="telegram-mcp")
    parser.add_argument("--status", action="store_true", help="print lock owner status as JSON")
    parser.add_argument("--pretty", action="store_true", help="pretty-print status JSON")
    parser.add_argument("--broker", action="store_true", help="serve one shared HTTP broker")
    parser.add_argument("--client-app", help="owner label written to lock metadata")
    parser.add_argument("--thread-id", help="optional task/thread label written to metadata")
    parser.add_argument(
        "server_args",
        nargs=argparse.REMAINDER,
        help="arguments after -- are passed to the MCP server",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.status:
        return print_status(pretty=args.pretty)

    transport = "http" if args.broker else os.environ.get("MCP_TRANSPORT", "stdio")
    environment = os.environ.copy()
    if args.broker:
        environment["MCP_BROKER_MODE"] = "1"
    command = _default_server_command()
    server_args = list(args.server_args)
    if server_args and server_args[0] == "--":
        server_args.pop(0)
    command.extend(server_args)
    return exec_guarded(
        command,
        app=args.client_app or ("broker" if args.broker else None),
        transport=transport,
        thread=args.thread_id,
        environ=environment,
    )


if __name__ == "__main__":
    raise SystemExit(main())
