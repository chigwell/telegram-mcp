"""Credential-free server used by real stdio and HTTP transport tests."""

import asyncio
import os
import json
import sys
from collections import deque

os.environ.update(
    TELEGRAM_API_ID="12345",
    TELEGRAM_API_HASH="dummy_hash",
    TELEGRAM_SESSION_NAME=":memory:",
    PYTHON_DOTENV_DISABLED="1",
    TELEGRAM_EXPOSED_TOOLS="all",
    TELEGRAM_TOOL_TIMEOUT_SECONDS="0.2",
)

from telethon.tl.types import User
from telegram_mcp import runtime as rt, event_webhooks as hooks
from telegram_mcp.event_protocol import EventRequest
from telegram_mcp.tools import events
from telegram_mcp.transport.http import create_streamable_http_app
from telegram_mcp.transport.stdio import run_stdio_async

rt.clients = {"default": object()}
rt.get_client = lambda account=None: rt.clients["default"]
events._event_log = deque(maxlen=4)
protocol = rt.mcp._telegram_events
protocol.heartbeat_seconds = 0.05
active = set()
original_handler = rt.mcp._mcp_server.request_handlers[EventRequest]

webhook_events = []


async def fake_webhook(url, subscription_id, event_id, body, signing_secrets):
    payload = json.loads(body)
    if payload.get("type") == "verification":
        return 200, json.dumps({"challenge": payload["challenge"]}).encode()
    webhook_events.append(payload)
    return 200, b"{}"


hooks.post_webhook = fake_webhook


@rt.mcp.tool()
async def webhook_count():
    return {"delivered": len(webhook_events)}


async def resolve_entity(value, client=None):
    return User(id=int(value))


rt.resolve_entity = resolve_entity


async def handler(request):
    if request.method != "events/stream":
        return await original_handler(request)
    context = rt.mcp._mcp_server.request_context
    key = (id(context.session), context.request_id)
    active.add(key)
    try:
        return await original_handler(request)
    finally:
        active.remove(key)


rt.mcp._mcp_server.request_handlers[EventRequest] = handler


@rt.mcp.tool()
async def emit(chat_id: int = 123, message_id: int = 1, out: bool = False):
    events._append_event(
        {
            "account": "default",
            "chat_id": chat_id,
            "message_id": message_id,
            "date": "2026-11-26T10:00:00Z",
            "out": out,
        }
    )
    return {"emitted": message_id}


@rt.mcp.tool()
async def stream_count():
    return {"active": len(active)}


@rt.mcp.tool()
async def slow_tool():
    await asyncio.sleep(1)
    return "completed"


@rt.mcp.tool()
async def disable_log():
    events._EVENT_LOG_SIZE = 0
    return {"disabled": True}


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "stdio":
        asyncio.run(run_stdio_async(rt.mcp))
    else:
        import uvicorn

        app = create_streamable_http_app(rt.mcp) if mode == "http" else rt.mcp.sse_app()
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
