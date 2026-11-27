"""MCP Events request handling for stdio and Streamable HTTP."""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar
from typing import ClassVar, Literal, Union

import mcp.types as types
from mcp.server.session import InitializationState, ServerSession
from mcp.shared.exceptions import McpError
from mcp.shared.message import ServerMessageMetadata, SessionMessage
from pydantic import ConfigDict, Field, model_serializer

from telegram_mcp.event_source import (
    cursor,
    position,
    read_page,
    replay_options,
    replay_start,
    require_scope,
    EVENT_NAME,
    EventError,
    event_allowed,
    event_definition,
    message_event,
    normalize_arguments,
)

HEARTBEAT_SECONDS = 30.0
EVENTS_EXTENSION = "io.modelcontextprotocol/events"
EVENT_TRANSPORT = ContextVar("telegram_mcp_event_transport", default="stdio")
CORRELATION_KEY = "io.modelcontextprotocol/subscriptionId"


class EventParams(types.RequestParams):
    model_config = ConfigDict(extra="allow")


class EventRequest(types.Request[EventParams, str]):
    method: Literal[
        "events/list",
        "events/poll",
        "events/stream",
        "events/subscribe",
        "events/unsubscribe",
        "server/discover",
    ]
    params: EventParams = Field(default_factory=EventParams)


class EventResult(types.EmptyResult):
    resultType: Literal["complete"] = "complete"

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
        params = responder.request.root.params
        meta = params.model_dump(by_alias=True).get("_meta", {}) if params else {}
        modern = (meta or {}).get("io.modelcontextprotocol/protocolVersion") == "2026-07-28"
        if (
            (
                isinstance(responder.request.root, EventRequest)
                and responder.request.root.method == "server/discover"
            )
            or modern
            and EVENT_TRANSPORT.get() != "sse"
        ):
            # Modern clients discover before sending ordinary requests, without initialize.
            self._initialization_state = InitializationState.Initialized
            self._telegram_modern = True
            return
        await original(self, responder)

    ServerSession._received_request = receive_request


async def _stream_arguments(params, mode="stream"):
    allowed = {"name", "arguments", "cursor", "maxAgeMs"}
    allowed.add("maxEvents" if mode == "poll" else "heartbeatMs")
    if set(params) - allowed:
        raise EventError("Invalid event stream parameters.")
    if not isinstance(params.get("name"), str):
        raise EventError("Select an event name.")
    if params["name"] != EVENT_NAME:
        raise EventError("Unknown event name.", -32023, data={"kind": "event"})
    position(params.get("cursor"))
    replay_options(params)
    if "heartbeatMs" in params and (
        type(params["heartbeatMs"]) is not int or params["heartbeatMs"] <= 0
    ):
        raise EventError("heartbeatMs must be a positive integer.")
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

    @property
    def owner(self):
        from telegram_mcp.transport.auth import principal

        try:
            request = self.server._mcp_server.request_context.request
        except LookupError:
            return None
        return principal(request)

    def catalog(self):
        from telegram_mcp.tools import events

        if events._EVENT_LOG_SIZE == 0 or EVENT_TRANSPORT.get() == "sse":
            return {"events": []}
        delivery = ["poll", "push"]
        if self.webhooks is not None and self.owner is not None:
            delivery.append("webhook")
        definition = event_definition(delivery)
        if "webhook" not in delivery:
            definition["description"] += (
                " Webhooks require authenticated Streamable HTTP;"
                " configure HTTP authentication on the server and client."
            )
        return {"events": [definition]}

    async def handle(self, request):
        params = request.params.model_dump(by_alias=True, exclude_unset=True)
        params.pop("_meta", None)
        method = request.method
        try:
            if EVENT_TRANSPORT.get() == "sse" and method.startswith("events/"):
                raise EventError(
                    "Events are unavailable on legacy SSE; use stdio or Streamable HTTP.", -32601
                )
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
                    raise EventError("Event unavailable.", -32023, data={"kind": "event"})
                if method == "events/poll":
                    result = await self.poll(params)
                elif method == "events/stream":
                    result = await self.stream(params)
                else:
                    result = await self.webhook(method, params)
            return EventResult(**result)
        except EventError as error:
            raise McpError(
                types.ErrorData(code=error.code, message=str(error), data=error.data)
            ) from None

    async def webhook(self, method, params):
        if self.owner is None:
            raise EventError(
                "Webhook subscriptions require authenticated HTTP. Configure HTTP authentication"
                " on the server and a matching credential in your MCP client.",
                -32024,
            )
        webhooks = self.webhooks
        if webhooks is None:
            raise EventError(
                "Webhook delivery is unavailable on this transport.",
                -32026,
                data={"feature": "deliveryMode", "value": "webhook"},
            )
        async with webhooks.lock:
            if not webhooks.running:
                await webhooks.start()
        operation = webhooks.subscribe if method == "events/subscribe" else webhooks.unsubscribe
        return await operation(params, self.owner)

    async def poll(self, params):
        args = await _stream_arguments(params, "poll")
        require_scope(args)
        after, gap = position(params.get("cursor"))
        from telegram_mcp.tools import events

        records, after, lost, more = read_page(
            args,
            after,
            params.get("maxEvents", 100),
            max_age=replay_options(params),
            replay_head=events._event_seq,
        )
        return {
            "events": [message_event(r) for r in records],
            "cursor": cursor(after),
            "truncated": gap or lost,
            "hasMore": more,
            "nextPollMs": 1000,
        }

    async def stream(self, params):  # noqa: C901 - cursor and heartbeat share one reader loop
        from telegram_mcp.tools import events

        args = await _stream_arguments(params)
        context = self.server._mcp_server.request_context
        after_seq, gap = position(params.get("cursor"))
        replay_head = events._event_seq
        max_age = replay_options(params)
        if params.get("cursor") is None:
            max_age = None
        replay_time = time.time()
        heartbeat_ms = max(1, int(self.heartbeat_seconds * 1000))
        heartbeat_ms = min(heartbeat_ms, max(1000, params.get("heartbeatMs", heartbeat_ms)))
        heartbeat_seconds = heartbeat_ms / 1000

        async def active(truncated, checked=None):
            await _notify(
                context,
                "active",
                {
                    "cursor": cursor(after_seq if checked is None else checked),
                    "truncated": truncated,
                    "heartbeatMs": heartbeat_ms,
                },
            )

        require_scope(args)
        after_seq, clipped = replay_start(
            args, after_seq, max_age=max_age, replay_head=replay_head, replay_time=replay_time
        )
        gap = gap or clipped
        await active(gap)
        heartbeat_at = time.monotonic() + heartbeat_seconds
        error = {"code": -32023, "message": "Event unavailable."}
        while events._EVENT_LOG_SIZE:
            signal = events._current_event_signal()
            try:
                require_scope(args)
            except EventError as exc:
                error = {"code": exc.code, "message": str(exc)}
                break
            records, scanned, gap, more = read_page(
                args, after_seq, max_age=max_age, replay_head=replay_head, replay_time=replay_time
            )
            if gap:
                await active(True, records[0]["seq"] - 1 if records else scanned)
            for record in records:
                await _notify(context, "event", message_event(record))
                heartbeat_at = time.monotonic() + heartbeat_seconds
            after_seq = scanned
            remaining = heartbeat_at - time.monotonic()
            if remaining <= 0:
                await _notify(context, "heartbeat", {"cursor": cursor(after_seq)})
                heartbeat_at = time.monotonic() + heartbeat_seconds
            elif not more:
                try:
                    await asyncio.wait_for(signal.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    pass
        await _notify(context, "terminated", {"error": error})
        return {"_meta": {CORRELATION_KEY: context.request_id}}


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
        if EVENT_TRANSPORT.get() == "sse":
            return result
        extensions = {**(getattr(result, "extensions", None) or {}), EVENTS_EXTENSION: {}}
        return result.model_copy(update={"extensions": extensions})

    low.get_capabilities = capabilities
    low.request_handlers[EventRequest] = protocol.handle
    return protocol


class EventTransport:
    """Keep legacy SSE out of Events without changing its ordinary SDK dispatcher."""

    def __init__(self, app, transport):
        self.app, self.transport = app, transport

    async def __call__(self, scope, receive, send):
        token = EVENT_TRANSPORT.set(self.transport)
        try:
            await self.app(scope, receive, send)
        finally:
            EVENT_TRANSPORT.reset(token)
