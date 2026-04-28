"""Compatibility entrypoint for the Telegram MCP server.

The implementation lives in the telegram_mcp package. This module keeps the
historic `main` import path and console script target working.
"""

from telegram_mcp import runtime as _runtime
from telegram_mcp.runtime import *
from telegram_mcp.runner import _main, main
from telegram_mcp.tools import *

# Backward-compatible alias for callers/tests that monkeypatch main.SERVER_ALLOWED_ROOTS.
SERVER_ALLOWED_ROOTS = _runtime.SERVER_ALLOWED_ROOTS


def _sync_runtime_roots() -> None:
    _runtime.SERVER_ALLOWED_ROOTS = SERVER_ALLOWED_ROOTS


async def _get_effective_allowed_roots(ctx):
    _sync_runtime_roots()
    return await _runtime._get_effective_allowed_roots(ctx)


async def _get_effective_allowed_roots_with_status(ctx):
    _sync_runtime_roots()
    return await _runtime._get_effective_allowed_roots_with_status(ctx)


async def _ensure_allowed_roots(ctx, tool_name):
    _sync_runtime_roots()
    return await _runtime._ensure_allowed_roots(ctx, tool_name)


async def _resolve_readable_file_path(*, raw_path, ctx, tool_name):
    _sync_runtime_roots()
    return await _runtime._resolve_readable_file_path(
        raw_path=raw_path,
        ctx=ctx,
        tool_name=tool_name,
    )


async def _resolve_writable_file_path(*, raw_path, default_filename, ctx, tool_name):
    _sync_runtime_roots()
    return await _runtime._resolve_writable_file_path(
        raw_path=raw_path,
        default_filename=default_filename,
        ctx=ctx,
        tool_name=tool_name,
    )


def _configure_allowed_roots_from_cli(argv=None) -> None:
    _runtime._configure_allowed_roots_from_cli(argv)
    globals()["SERVER_ALLOWED_ROOTS"] = _runtime.SERVER_ALLOWED_ROOTS


if __name__ == "__main__":
    main()
