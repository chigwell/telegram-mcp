"""HTTP event methods using the existing MCP configured-access model."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from starlette.requests import Request
from starlette._utils import get_route_path
from mcp.server.transport_security import TransportSecurityMiddleware
from starlette.responses import JSONResponse, Response

from telegram_mcp.event_webhooks import EventError, MessageSubscriptions, event_definition

logger = logging.getLogger(__name__)


class EventsHTTPMiddleware:
    def __init__(self, app, server, path):
        self.app = app
        self.server = server
        self.endpoint = server.settings.streamable_http_path
        self.subscriptions = MessageSubscriptions(path)
        self._advertise()

    def _advertise(self):
        low = self.server._mcp_server
        if getattr(low, "_events_advertised", False):
            return
        original = low.get_capabilities

        def capabilities(*args, **kwargs):
            return original(*args, **kwargs).model_copy(update={"events": {}})

        low.get_capabilities = capabilities
        low._events_advertised = True

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._lifespan(scope, receive, send)
            return
        if scope["type"] != "http" or get_route_path(scope).rstrip("/") != self.endpoint.rstrip(
            "/"
        ):
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        security = TransportSecurityMiddleware(self.server.settings.transport_security)
        rejected = await security.validate_request(request, is_post=request.method == "POST")
        if rejected is not None:
            await rejected(scope, receive, send)
            return
        if request.method != "POST":
            await self.app(scope, receive, send)
            return
        await self._post(request, scope, receive, send)

    async def _post(self, request, scope, receive, send):
        body_parts = []
        size = 0
        async for part in request.stream():
            size += len(part)
            if size > 4 * 1024 * 1024:
                await Response("Request body too large", status_code=413)(scope, receive, send)
                return
            body_parts.append(part)
        body = b"".join(body_parts)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        method = payload.get("method") if isinstance(payload, dict) else None
        if isinstance(method, str) and method in {
            "events/list",
            "events/subscribe",
            "events/unsubscribe",
            "server/discover",
        }:
            await self.handle(payload, scope, receive, send)
            return
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _lifespan(self, scope, receive, send):
        initial = await receive()
        try:
            await self.subscriptions.start()
        except Exception:
            await self.subscriptions.close()
            await send(
                {
                    "type": "lifespan.startup.failed",
                    "message": "MCP Events store startup failed.",
                }
            )
            return
        replayed = False

        async def lifespan_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return initial
            return await receive()

        try:
            await self.app(scope, lifespan_receive, send)
        finally:
            await self.subscriptions.close()

    async def handle(self, payload, scope, receive, send):
        request_id = payload.get("id")
        try:
            if (
                payload.get("jsonrpc") != "2.0"
                or "id" not in payload
                or isinstance(request_id, (dict, list, bool))
            ):
                raise EventError("Invalid JSON-RPC request.", -32600)
            params = payload.get("params", {})
            method = payload["method"]
            if not isinstance(params, dict):
                raise EventError("params must be an object.")
            params = {k: v for k, v in params.items() if k != "_meta"}
            result = await self._execute(method, params)
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except EventError as exc:
            error = {"code": exc.code, "message": str(exc)}
            if exc.reason:
                error["data"] = {"reason": exc.reason}
            response = {"jsonrpc": "2.0", "id": request_id, "error": error}
        except Exception:
            # Do not log params, callback URLs or signing secrets.
            logger.error("MCP Events request failed")
            response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Event subscription operation failed."},
            }
        await JSONResponse(response)(scope, receive, send)

    async def _execute(self, method, params):
        if method == "events/list":
            if set(params) - {"cursor"} or params.get("cursor") is not None:
                raise EventError("Invalid events catalog cursor.")
            return {"events": [event_definition()]}
        elif method == "events/subscribe":
            if not self.subscriptions.running:
                raise EventError("Event service has not started.", -32000)
            return await self.subscriptions.subscribe(params)
        elif method == "events/unsubscribe":
            if not self.subscriptions.running:
                raise EventError("Event service has not started.", -32000)
            return await self.subscriptions.unsubscribe(params)
        else:
            # Preserve ordinary SDK discovery when event support is disabled.
            return {
                "resultType": "complete",
                "supportedVersions": ["2026-07-28"],
                "capabilities": {"tools": {}, "events": {}},
                "serverInfo": {"name": self.server.name, "version": "2.0.1"},
            }


def with_events(app, server):
    from telegram_mcp.tools import events

    if events._EVENT_LOG_SIZE == 0:
        return app
    state = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = os.getenv("TELEGRAM_MCP_EVENTS_FILE") or state / "telegram-mcp" / "subscriptions.json"
    return EventsHTTPMiddleware(app, server, path)
