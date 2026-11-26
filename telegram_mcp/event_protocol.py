"""MCP Events request handling shared by stdio and both HTTP transports."""

from __future__ import annotations

import asyncio
import time
from typing import ClassVar, Literal, Union

import mcp.types as types
from mcp.server.session import InitializationState, ServerSession
from mcp.shared.exceptions import McpError
from mcp.shared.message import ServerMessageMetadata, SessionMessage
from pydantic import ConfigDict, Field, model_serializer

from telegram_mcp.event_webhooks import (
    EVENT_NAME,
    EventError,
    event_allowed,
    event_definition,
    message_event,
    normalize_arguments,
)

HEARTBEAT_SECONDS = 30.0
CORRELATION_KEY = "io.modelcontextprotocol/subscriptionId"


class EventParams(types.RequestParams):
    model_config = ConfigDict(extra="allow")


class EventRequest(types.Request[EventParams, str]):
    method: Literal[
        "events/list", "events/stream", "events/subscribe", "events/unsubscribe", "server/discover"
    ]
    params: EventParams = Field(default_factory=EventParams)


class EventResult(types.EmptyResult):
    @model_serializer(mode="wrap")
    def keep_cursor(self, serialize):
        result = serialize(self)
        # The SDK excludes None fields; an explicit null cursor declares no replay.
        if "cursor" in self.model_extra:
            result["cursor"] = self.model_extra["cursor"]
        return result


def _install_types():
    """Extend the SDK 1.x closed request union without replacing its dispatcher."""
    if getattr(types.ClientRequest, "_telegram_events", False):
        return

    class ClientRequest(types.ClientRequest):
        root: Union[types.ClientRequestType, EventRequest]
        _telegram_events: ClassVar[bool] = True

    types.ClientRequest = ClientRequest
    original = ServerSession._received_request

    async def receive_request(self, responder):
        if (
            isinstance(responder.request.root, EventRequest)
            and responder.request.root.method == "server/discover"
        ):
            # Modern clients discover before sending ordinary requests, without initialize.
            self._initialization_state = InitializationState.Initialized
            return
        await original(self, responder)

    ServerSession._received_request = receive_request


async def _stream_arguments(params):
    if (
        set(params) - {"name", "arguments", "cursor", "maxAgeMs"}
        or params.get("name") != EVENT_NAME
    ):
        raise EventError("Invalid event stream parameters.")
    if params.get("cursor") is not None:
        raise EventError("This event does not support replay; cursor must be null.")
    max_age = params.get("maxAgeMs")
    if max_age is not None and (type(max_age) is not int or max_age < 0):
        raise EventError("maxAgeMs must be a non-negative integer.")
    return await normalize_arguments(params.get("arguments", {}))


async def _notify(context, kind, data):
    # SDK 1.x send_notification drops routing metadata for falsey IDs (0 and "").
    message = types.JSONRPCNotification(
        jsonrpc="2.0",
        method="notifications/events/" + kind,
        params={**data, "_meta": {CORRELATION_KEY: context.request_id}},
    )
    await context.session._write_stream.send(
        SessionMessage(
            types.JSONRPCMessage(message),
            metadata=ServerMessageMetadata(related_request_id=context.request_id),
        )
    )


class EventProtocol:
    def __init__(self, server):
        self.server = server
        self.heartbeat_seconds = HEARTBEAT_SECONDS

    @property
    def webhooks(self):
        try:
            request = self.server._mcp_server.request_context.request
        except LookupError:
            return None
        return getattr(request.app.state, "telegram_webhooks", None) if request else None

    def catalog(self):
        from telegram_mcp.tools import events

        if events._EVENT_LOG_SIZE == 0:
            return {"events": []}
        delivery = ["push"]
        if self.webhooks is not None:
            delivery.append("webhook")
        return {"events": [event_definition(delivery)]}

    async def handle(self, request):
        params = request.params.model_dump(by_alias=True, exclude_unset=True)
        params.pop("_meta", None)
        method = request.method
        try:
            if method == "events/list":
                if set(params) - {"cursor"} or params.get("cursor") is not None:
                    raise EventError("Invalid events catalog cursor.")
                result = self.catalog()
            elif method == "server/discover":
                from telegram_mcp.transport.protocol import LATEST_PROTOCOL_VERSION
                from mcp.server.lowlevel.server import NotificationOptions

                result = {
                    "resultType": "complete",
                    "supportedVersions": [LATEST_PROTOCOL_VERSION],
                    "capabilities": self.server._mcp_server.get_capabilities(
                        NotificationOptions(), {}
                    ).model_dump(exclude_none=True),
                    "serverInfo": {"name": self.server.name, "version": "2.0.1"},
                }
            else:
                if not self.catalog()["events"]:
                    raise EventError("The Telegram event log is disabled.", -32000)
                if method == "events/stream":
                    result = await self.stream(params)
                else:
                    webhooks = self.webhooks
                    if webhooks is None:
                        raise EventError(
                            "Webhook delivery is unavailable on this transport.", -32000
                        )
                    async with webhooks.lock:
                        if not webhooks.running:
                            await webhooks.start()
                    operation = (
                        webhooks.subscribe
                        if method == "events/subscribe"
                        else webhooks.unsubscribe
                    )
                    result = await operation(params)
            return EventResult(**result)
        except EventError as error:
            data = {"reason": error.reason} if error.reason else None
            raise McpError(
                types.ErrorData(code=error.code, message=str(error), data=data)
            ) from None

    async def stream(self, params):
        from telegram_mcp.tools import events

        args = await _stream_arguments(params)
        context = self.server._mcp_server.request_context
        after_seq = events._event_seq

        await _notify(context, "active", {"cursor": None, "truncated": False})
        heartbeat_at = time.monotonic() + self.heartbeat_seconds
        while events._EVENT_LOG_SIZE:
            signal = events._current_event_signal()
            records, gap = events._select_events(
                after_seq, None, args["account"], 100, public=False
            )
            scanned_through = events._event_seq
            if gap:
                await _notify(context, "active", {"cursor": None, "truncated": True})
            for record in records:
                if event_allowed(args, record):
                    await _notify(context, "event", message_event(record))
                after_seq = record["seq"]
            if len(records) < 100:
                after_seq = scanned_through
            remaining = heartbeat_at - time.monotonic()
            if remaining <= 0:
                await _notify(context, "heartbeat", {"cursor": None})
                heartbeat_at = time.monotonic() + self.heartbeat_seconds
            elif len(records) < 100:
                try:
                    await asyncio.wait_for(signal.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
        return {"_meta": {}}


def install_events(server):
    existing = getattr(server, "_telegram_events", None)
    if existing is not None:
        return existing
    _install_types()
    protocol = EventProtocol(server)
    server._telegram_events = protocol
    low = server._mcp_server
    original = low.get_capabilities

    def capabilities(*args, **kwargs):
        result = original(*args, **kwargs)
        if protocol.catalog()["events"]:
            result = result.model_copy(update={"events": {}})
        return result

    low.get_capabilities = capabilities
    low.request_handlers[EventRequest] = protocol.handle
    return protocol
