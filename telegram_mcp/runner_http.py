"""HTTP (streamable-http + OAuth) entrypoint for the Telegram MCP server.

Selected when ``TELEGRAM_MCP_TRANSPORT=http``. Mirrors ``runner.py`` but
serves the MCP endpoint over HTTP through uvicorn, with the OAuth 2.1
provider wired into FastMCP. The Telegram clients are connected and their
entity caches warmed once at startup, exactly like the stdio path.
"""

from __future__ import annotations

import asyncio
import os
import sys

import nest_asyncio
import uvicorn

from telegram_mcp.runtime import (
    _configure_allowed_roots_from_cli,
    clients,
    mcp,
)
from telegram_mcp.runner import connect_clients


async def _serve_http() -> None:
    """Connect Telegram clients, warm caches, then serve the ASGI app."""
    labels = ", ".join(clients.keys())
    print(
        f"Starting {len(clients)} Telegram client(s) ({labels})...",
        file=sys.stderr,
    )
    await connect_clients()

    # Build the Starlette app and attach the OAuth login routes. We import
    # here (after the package is fully initialized) so that the FastMCP
    # session manager is created in the right order.
    from telegram_mcp.auth import SingleUserOAuthProvider

    app = mcp.streamable_http_app()
    provider = mcp._auth_server_provider  # type: ignore[attr-defined]
    if isinstance(provider, SingleUserOAuthProvider):
        for route in provider.routes():
            app.routes.append(route)

    host = os.getenv("TELEGRAM_MCP_HOST", "0.0.0.0")
    port = int(os.getenv("TELEGRAM_MCP_PORT", "8000"))
    public_url = os.getenv("TELEGRAM_MCP_PUBLIC_URL", "(unset)")

    print(
        f"Telegram client(s) ready ({labels}). Serving MCP over HTTP on "
        f"{host}:{port}  (public URL: {public_url})",
        file=sys.stderr,
    )

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=os.getenv("TELEGRAM_MCP_LOG_LEVEL", "info"),
        access_log=False,
        # Trust X-Forwarded-* headers from the reverse proxy / Cloudflare
        # tunnel so issued redirect URIs stay on HTTPS.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        await asyncio.gather(
            *(cl.disconnect() for cl in clients.values()),
            return_exceptions=True,
        )


def main() -> None:
    _configure_allowed_roots_from_cli(sys.argv[1:])
    nest_asyncio.apply()
    asyncio.run(_serve_http())


if __name__ == "__main__":
    main()
