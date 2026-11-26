"""Shared event semantics and SDK integration, using only synthetic messages."""

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anyio
import mcp.types as types
import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.exceptions import McpError
from mcp.shared.message import SessionMessage
from pydantic import ValidationError
from telethon.tl.types import User

from telegram_mcp import runtime as rt
from telegram_mcp.event_protocol import CORRELATION_KEY, EventParams, EventRequest, install_events
from telegram_mcp.tools import events


@pytest.fixture(autouse=True)
def fake_source(monkeypatch):
    monkeypatch.setattr(rt, "clients", {"default": object()})
    monkeypatch.setattr(rt, "get_client", lambda account: object())
    monkeypatch.setattr(rt, "resolve_entity", AsyncMock(return_value=User(id=123)))
    monkeypatch.setattr(events, "_EVENT_LOG_SIZE", 4)
    monkeypatch.setattr(events, "_event_log", deque(maxlen=4))
    monkeypatch.setattr(events, "_event_seq", 0)
    monkeypatch.setattr(events, "_event_signal", None)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS", raising=False)


def record(message_id):
    return {
        "account": "default",
        "chat_id": 123,
        "message_id": message_id,
        "out": False,
        "date": "2026-11-26T10:00:00Z",
    }


async def eventually(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.005)
    assert predicate()


def test_narrow_sdk_extension_preserves_normal_requests():
    server = FastMCP("types")
    assert install_events(server) is install_events(server)
    assert isinstance(
        types.ClientRequest.model_validate({"method": "ping"}).root, types.PingRequest
    )
    assert isinstance(
        types.ClientRequest.model_validate({"method": "events/list"}).root, EventRequest
    )
    with pytest.raises(ValidationError):
        types.ClientRequest.model_validate({"method": "unknown/method"})


@pytest.mark.asyncio
@pytest.mark.parametrize("request_id", [0, ""])
async def test_gap_heartbeat_null_cursor_and_routing(request_id, monkeypatch):
    protocol = install_events(FastMCP("stream"))
    protocol.heartbeat_seconds = 0.01
    frames = []
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def send(message):
        assert message.metadata.related_request_id == request_id
        frame = message.message.model_dump(by_alias=True, exclude_none=True)
        assert frame["params"]["_meta"][CORRELATION_KEY] == request_id
        assert frame["params"]["cursor"] is None
        frames.append(frame)
        if (
            frame["method"] == "notifications/events/event"
            and frame["params"]["data"]["message_id"] == 1
        ):
            blocked.set()
            await release.wait()

    context = SimpleNamespace(
        request=None,
        request_id=request_id,
        session=SimpleNamespace(_write_stream=SimpleNamespace(send=send)),
    )
    token = request_ctx.set(context)
    stream = asyncio.create_task(
        protocol.stream({"name": "telegram.message.created", "arguments": {"chat_id": 123}})
    )
    try:
        await eventually(lambda: len(frames) == 1)
        events._append_event(record(1))
        await asyncio.wait_for(blocked.wait(), 1)
        for message_id in range(2, 10):
            events._append_event(record(message_id))
        release.set()
        await eventually(lambda: any(f["params"].get("truncated") for f in frames))
        await eventually(
            lambda: any(f["method"] == "notifications/events/heartbeat" for f in frames)
        )
        delivered = [
            f["params"]["data"]["message_id"]
            for f in frames
            if f["method"] == "notifications/events/event"
        ]
        assert delivered == [1, 6, 7, 8, 9]
        monkeypatch.setattr(events, "_EVENT_LOG_SIZE", 0)
        assert await asyncio.wait_for(stream, 1) == {"_meta": {}}
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
        request_ctx.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,params",
    [
        ("events/stream", {"name": "telegram.message.created", "arguments": {"all_chats": True}}),
        ("events/stream", {"name": "telegram.message.created", "arguments": {}}),
        ("events/list", {}),
        ("ping", {}),
    ],
)
async def test_cancel_queued_request_before_dispatch(method, params):
    protocol = install_events(FastMCP("early-cancel"))
    low = protocol.server._mcp_server
    incoming, server_read = anyio.create_memory_object_stream(10)
    server_write, outgoing = anyio.create_memory_object_stream(10)
    messages = [
        {"jsonrpc": "2.0", "id": "early", "method": method, "params": params},
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": "early"}},
        {"jsonrpc": "2.0", "id": "ping", "method": "ping"},
    ]
    for message in messages:
        await incoming.send(SessionMessage(types.JSONRPCMessage.model_validate(message)))
    server_task = asyncio.create_task(
        low.run(server_read, server_write, low.create_initialization_options(), stateless=True)
    )
    try:
        frame = (await asyncio.wait_for(outgoing.receive(), 1)).message.model_dump()
        assert frame.get("id") == "ping", frame
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(outgoing.receive(), 0.05)
    finally:
        await incoming.aclose()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await outgoing.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"name": "unknown", "arguments": {"all_chats": True}},
        {"name": "telegram.message.created", "arguments": {"all_chats": True}, "cursor": "old"},
        {"name": "telegram.message.created", "arguments": {"all_chats": True}, "maxAgeMs": True},
    ],
)
async def test_invalid_stream_parameters_are_protocol_errors(params):
    protocol = install_events(FastMCP("invalid"))
    with pytest.raises(McpError) as error:
        await protocol.handle(EventRequest(method="events/stream", params=EventParams(**params)))
    assert error.value.error.code == -32602
