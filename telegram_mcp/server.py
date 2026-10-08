"""High-level server module and exports for Telegram MCP.

Provides access to the FastMCP instance, HTTP ASGI application wrapped with
protocol negotiation for MCP 2026-07-28, and transport serving routines.
"""

from __future__ import annotations

from typing import Any

from telegram_mcp.transport import (
    DEFAULT_PROTOCOL_VERSION,
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    create_streamable_http_app,
    install_protocol_compatibility,
    run_stdio_async,
    run_streamable_http_async,
)
from telegram_mcp.runtime import mcp
from telegram_mcp import runner as _runner

# Ensure protocol compatibility is installed upon server module import
install_protocol_compatibility()

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "LATEST_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "create_streamable_http_app",
    "get_http_app",
    "get_server",
    "main",
    "mcp",
    "run_stdio_async",
    "run_streamable_http_async",
    "serve",
]


def get_server() -> Any:
    """Return the underlying FastMCP server instance."""
    return mcp


def get_http_app() -> Any:
    """Return the Starlette ASGI application with protocol negotiation."""
    return create_streamable_http_app(mcp)


async def serve(transport: str = "stdio") -> None:
    """Serve the Telegram MCP server over the requested transport."""
    await _runner._serve(transport)


def main() -> None:
    """Server CLI entrypoint."""
    _runner.main()


if __name__ == "__main__":
    main()
