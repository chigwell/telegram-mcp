"""Streamable HTTP event headers, optional authentication and webhook lifecycle."""

import base64
import binascii
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path

from starlette.responses import JSONResponse

from telegram_mcp.event_protocol import install_events
from telegram_mcp.event_webhooks import MessageSubscriptions
from telegram_mcp.transport.auth import STATIC_PRINCIPAL, with_http_auth


def routing_error(request, headers):
    method = request.get("method") if isinstance(request, dict) else None
    if not isinstance(method, str) or not method.startswith("events/"):
        return None
    expected = {b"mcp-method": method.encode()}
    params = request.get("params")
    name = params.get("name") if isinstance(params, dict) else None
    if method != "events/list" and isinstance(name, str):
        expected[b"mcp-name"] = name.encode()
    for key, value in expected.items():
        actual = [v for k, v in headers if k.lower() == key]
        if (
            key == b"mcp-name"
            and len(actual) == 1
            and actual[0].startswith(b"=?base64?")
            and actual[0].endswith(b"?=")
        ):
            try:
                actual = [base64.b64decode(actual[0][9:-2], validate=True)]
                actual[0].decode("utf-8")
            except (ValueError, binascii.Error):
                actual = []
        if actual != [value]:
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {
                    "code": -32020,
                    "message": "MCP routing header mismatch.",
                    "data": {"header": key.decode(), "expected": value.decode()},
                },
            }
    return None


class EventHeadersMiddleware:
    def __init__(self, app, max_body):
        self.app, self.max_body = app, max_body

    async def read_body(self, receive):
        body, messages = bytearray(), []
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                return None, messages
            body.extend(message.get("body", b""))
            if len(body) > self.max_body:
                raise ValueError("Request body too large")
            if not message.get("more_body", False):
                return body, messages

    async def __call__(self, scope, receive, send):
        versions = [v for k, v in scope.get("headers", []) if k.lower() == b"mcp-protocol-version"]
        if scope["type"] == "http" and len(versions) > 1:
            await JSONResponse(
                {"error": "Duplicate MCP-Protocol-Version header"}, status_code=400
            )(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or headers.get(b"mcp-protocol-version") != b"2026-07-28"
        ):
            await self.app(scope, receive, send)
            return
        try:
            body, messages = await self.read_body(receive)
        except ValueError:
            await JSONResponse({"error": "Request body too large"}, status_code=413)(
                scope, receive, send
            )
            return
        if body is None:
            return
        try:
            request = json.loads(body)
        except (ValueError, UnicodeError):
            request = None
        error = routing_error(request, scope["headers"])
        if error:
            await JSONResponse(error, status_code=400)(scope, receive, send)
            return
        iterator = iter(messages)

        async def replay():
            return next(iterator, None) or await receive()

        await self.app(scope, replay, send)


def with_events(app, server):
    install_events(server)
    with_http_auth(app, server, [server.settings.streamable_http_path])
    # Preserve the middleware's HTTP 401 before validating event request bodies.
    for route in app.routes:
        if route.path == server.settings.streamable_http_path:
            from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware

            endpoint = route.app
            if isinstance(endpoint, RequireAuthMiddleware):
                endpoint.app = EventHeadersMiddleware(
                    endpoint.app, server.settings.max_request_body_size
                )
            else:
                route.app = EventHeadersMiddleware(endpoint, server.settings.max_request_body_size)
    from telegram_mcp.tools import events

    if events._EVENT_LOG_SIZE == 0:
        return app
    configured = os.getenv("TELEGRAM_MCP_HTTP_TOKEN") is not None
    if not configured:
        return app
    state = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = os.getenv("TELEGRAM_MCP_EVENTS_FILE") or state / "telegram-mcp" / "subscriptions.json"
    subscriptions = MessageSubscriptions(path, {STATIC_PRINCIPAL})
    app.state.telegram_webhooks = subscriptions
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        try:
            if subscriptions.store.path.exists():
                await subscriptions.start()
            async with original_lifespan(app) as context:
                yield context
        finally:
            await subscriptions.close()

    app.router.lifespan_context = lifespan
    return app
