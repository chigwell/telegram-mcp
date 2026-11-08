"""Streamable HTTP transport with MCP 2026-07-28 protocol negotiation support."""

from __future__ import annotations

import logging
from typing import Any, Optional

from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from telegram_mcp.media_links import MEDIA_ROUTE_PREFIX, UPLOAD_ROUTE_PREFIX
from telegram_mcp.transport.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    LATEST_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    SUPPORTED_PROTOCOL_VERSIONS,
    install_protocol_compatibility,
)

logger = logging.getLogger(__name__)

# Browser origins accepted when the FastMCP instance carries no explicit
# transport security settings: the same local-only defaults FastMCP itself uses.
_DEFAULT_ALLOWED_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def _origin_matches(origin: str, allowed_origins: Any) -> bool:
    """Match an Origin header like FastMCP does: exact, or ``scheme://host:*``."""
    for allowed in allowed_origins or ():
        if origin == allowed:
            return True
        if allowed.endswith(":*") and origin.startswith(allowed[:-2] + ":"):
            port = origin[len(allowed) - 1 :]
            if port.isdigit():
                return True
    return False


def is_origin_allowed(origin: Optional[str], mcp_instance: Any = None) -> bool:
    """Return True when a request with this Origin header may reach the server.

    Requests without an Origin header come from non-browser MCP clients and are
    allowed. A browser always sends Origin on cross-origin requests, so any other
    web page is refused unless MCP_ALLOWED_ORIGINS (or FastMCP's own settings)
    names it, or the operator turned DNS-rebinding protection off explicitly.
    """
    if not origin:
        return True
    settings = getattr(getattr(mcp_instance, "settings", None), "transport_security", None)
    if settings is None:
        return _origin_matches(origin, _DEFAULT_ALLOWED_ORIGINS)
    if not getattr(settings, "enable_dns_rebinding_protection", True):
        return True
    return _origin_matches(origin, getattr(settings, "allowed_origins", ()))


class ProtocolNegotiationMiddleware:
    """ASGI middleware that enforces MCP protocol negotiation and response headers.

    Features:
    - Validates MCP-Protocol-Version request header against supported versions.
    - Rejects unsupported versions with a standard JSON-RPC 400 Bad Request error.
    - Injects negotiated MCP-Protocol-Version into every HTTP response.
    - Refuses requests whose Origin is not allowed (cross-origin request forgery),
      including CORS preflights and the signed media/upload routes.
    - Handles CORS and OPTIONS preflight requests, echoing only allowed origins.
    - Provides a JSON discovery response for plain GET probes (non-SSE) on the MCP endpoint.
    """

    def __init__(self, app: ASGIApp, mcp_instance: Any = None):
        self.app = app
        self.mcp_instance = mcp_instance

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_origin = headers.get(b"origin")
        origin = raw_origin.decode("latin-1").strip() if raw_origin else None
        if not is_origin_allowed(origin, self.mcp_instance):
            logger.warning("Rejected request from disallowed Origin: %s", origin)
            response = Response("Invalid Origin header", status_code=403)
            await response(scope, receive, send)
            return
        cors_origin_headers = (
            {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}
        )

        # The signed file routes are plain HTTP, not MCP: no negotiation, no discovery probe.
        if scope.get("path", "").startswith((MEDIA_ROUTE_PREFIX, UPLOAD_ROUTE_PREFIX)):
            await self.app(scope, receive, send)
            return

        await self._negotiate(scope, receive, send, headers, cors_origin_headers)

    async def _negotiate(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        headers: dict,
        cors_origin_headers: dict,
    ) -> None:
        raw_version_bytes = headers.get(MCP_PROTOCOL_VERSION_HEADER.encode("latin-1"))
        requested_version = (
            raw_version_bytes.decode("latin-1").strip() if raw_version_bytes else None
        )

        # 1. Handle OPTIONS discovery / CORS preflight
        if scope.get("method") == "OPTIONS":
            cors_headers = (
                "Content-Type, Accept, MCP-Protocol-Version, MCP-Session-Id, Authorization"
            )
            response = Response(
                status_code=204,
                headers={
                    "Allow": "GET, POST, DELETE, OPTIONS, HEAD",
                    MCP_PROTOCOL_VERSION_HEADER: requested_version or LATEST_PROTOCOL_VERSION,
                    **cors_origin_headers,
                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS, HEAD",
                    "Access-Control-Allow-Headers": cors_headers,
                    "Access-Control-Expose-Headers": "MCP-Protocol-Version, MCP-Session-Id",
                },
            )
            await response(scope, receive, send)
            return

        # 2. Validate protocol version if provided
        if requested_version:
            if requested_version not in SUPPORTED_PROTOCOL_VERSIONS:
                supported_str = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
                error_body = {
                    "jsonrpc": "2.0",
                    "id": "server-error",
                    "error": {
                        "code": -32600,
                        "message": (
                            f"Bad Request: Unsupported protocol version: {requested_version}. "
                            f"Supported versions: {supported_str}"
                        ),
                    },
                }
                response = JSONResponse(
                    status_code=400,
                    content=error_body,
                    headers={
                        MCP_PROTOCOL_VERSION_HEADER: DEFAULT_PROTOCOL_VERSION,
                    },
                )
                await response(scope, receive, send)
                return
            negotiated_version = requested_version
        else:
            negotiated_version = DEFAULT_PROTOCOL_VERSION

        # 3. Handle plain GET discovery probe (clients querying without text/event-stream)
        accept_header = headers.get(b"accept", b"").decode("latin-1").lower()
        if scope.get("method") == "GET" and "text/event-stream" not in accept_header:
            discovery_data = {
                "name": "telegram",
                "version": "2.0.1",
                "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "latestProtocolVersion": LATEST_PROTOCOL_VERSION,
                "negotiatedProtocolVersion": negotiated_version,
            }
            response = JSONResponse(
                status_code=200,
                content=discovery_data,
                headers={
                    MCP_PROTOCOL_VERSION_HEADER: negotiated_version,
                    **cors_origin_headers,
                    "Access-Control-Expose-Headers": "MCP-Protocol-Version, MCP-Session-Id",
                },
            )
            await response(scope, receive, send)
            return

        # 4. Wrap send to inject MCP-Protocol-Version header on all response start messages
        async def send_with_protocol_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                resp_headers = list(message.get("headers", []))
                header_names = {h[0].lower() for h in resp_headers}

                if MCP_PROTOCOL_VERSION_HEADER.encode("latin-1") not in header_names:
                    resp_headers.append(
                        (
                            MCP_PROTOCOL_VERSION_HEADER.encode("latin-1"),
                            negotiated_version.encode("latin-1"),
                        )
                    )

                if b"access-control-expose-headers" not in header_names:
                    expose_val = b"*, mcp-protocol-version, mcp-session-id"
                    resp_headers.append((b"access-control-expose-headers", expose_val))

                message["headers"] = resp_headers
            await send(message)

        await self.app(scope, receive, send_with_protocol_header)


def patch_fastmcp_streamable_http() -> None:
    """Patch FastMCP.streamable_http_app to enforce MCP 2026-07-28 protocol negotiation."""
    try:
        from mcp.server.fastmcp import FastMCP

        if getattr(FastMCP, "_protocol_negotiation_patched", False):
            return

        original_streamable_http_app = FastMCP.streamable_http_app

        def wrapped_streamable_http_app(self: Any, *args: Any, **kwargs: Any) -> Any:
            app = original_streamable_http_app(self, *args, **kwargs)
            return ProtocolNegotiationMiddleware(app, self)

        FastMCP.streamable_http_app = wrapped_streamable_http_app  # type: ignore[assignment]
        FastMCP._protocol_negotiation_patched = True  # type: ignore[attr-defined]
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not monkey-patch FastMCP.streamable_http_app: %s", exc)


def create_streamable_http_app(mcp_instance: Any) -> Any:
    """Build and wrap the Starlette app from FastMCP with protocol negotiation."""
    install_protocol_compatibility()
    if not hasattr(mcp_instance, "streamable_http_app"):
        return mcp_instance

    # If the FastMCP session manager already ran and shut down, reset it to allow clean restart
    session_mgr = getattr(mcp_instance, "_session_manager", None)
    if session_mgr is not None and getattr(session_mgr, "_has_started", False):
        mcp_instance._session_manager = None

    app = mcp_instance.streamable_http_app()
    # If not already wrapped via monkeypatch
    if not isinstance(app, ProtocolNegotiationMiddleware):
        app = ProtocolNegotiationMiddleware(app, mcp_instance)
    return app


async def run_streamable_http_async(mcp_instance: Any) -> None:
    """Run FastMCP streamable HTTP transport with protocol negotiation middleware."""
    install_protocol_compatibility()
    patch_fastmcp_streamable_http()

    if not hasattr(mcp_instance, "streamable_http_app"):
        if hasattr(mcp_instance, "run_streamable_http_async"):
            await mcp_instance.run_streamable_http_async()
            return

    import uvicorn

    app = create_streamable_http_app(mcp_instance)

    config = uvicorn.Config(
        app,
        host=mcp_instance.settings.host,
        port=mcp_instance.settings.port,
        log_level=mcp_instance.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    await server.serve()
