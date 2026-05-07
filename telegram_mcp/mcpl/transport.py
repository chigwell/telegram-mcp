"""Intercepting stdio transport for MCPL.

Python port of discord-mcp's McplInterceptingTransport.java. Reads real
stdin line-by-line, classifies each JSON-RPC frame:

  - MCPL methods (anything in `MCPL_METHODS`) → MCPL dispatcher
  - JSON-RPC responses to our outbound requests → outbound queue
  - everything else (standard MCP) → forwarded to FastMCP via an in-process
    anyio MemoryObjectStream

`initialize` is also peeked at on the way through to extract the host's
declared MCPL capabilities (`params.capabilities.experimental.mcpl`),
then forwarded to FastMCP unchanged.

Outbound MCPL traffic (push events, host→server requests) shares the
same `write_stream` that FastMCP writes to, so the existing stdout writer
serializes everything in order under one anyio task — no manual stdout lock.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from io import TextIOWrapper
from typing import Any

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

from .capabilities import build_experimental_capabilities
from .dispatcher import MCPL_METHODS, McplDispatcher

OnReadyHook = Callable[["McplTransport"], Awaitable[None]]
"""Callback fired once the host has sent `notifications/initialized`.

Use it to send `channels/register` and any other post-handshake setup
that depends on the host being ready to receive server-initiated traffic.
"""

log = logging.getLogger("telegram_mcp.mcpl")


class McplTransport:
    """The MCPL side of the stdio transport.

    Owns the write end so handlers can emit notifications, requests, and
    responses. The host's MCPL capabilities are populated when `initialize`
    is observed and stay accessible to handlers.
    """

    def __init__(self, dispatcher: McplDispatcher) -> None:
        self.dispatcher = dispatcher
        self._write_stream: MemoryObjectSendStream[SessionMessage] | None = None
        self.host_capabilities: dict[str, Any] | None = None

    # -- internal: bound by the stdio context manager -----------------------

    def _bind_write_stream(self, write_stream: MemoryObjectSendStream[SessionMessage]) -> None:
        self._write_stream = write_stream

    # -- outbound primitives -------------------------------------------------

    async def _send(self, msg: types.JSONRPCMessage) -> None:
        if self._write_stream is None:
            raise RuntimeError("McplTransport not started — write stream unbound")
        await self._write_stream.send(SessionMessage(msg))

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        notif = types.JSONRPCNotification(
            jsonrpc="2.0",
            method=method,
            params=params or {},
        )
        await self._send(types.JSONRPCMessage(notif))

    async def send_request(self, msg_id: int, method: str, params: dict[str, Any]) -> None:
        req = types.JSONRPCRequest(
            jsonrpc="2.0",
            id=msg_id,
            method=method,
            params=params,
        )
        await self._send(types.JSONRPCMessage(req))

    async def send_response(self, msg_id: int | str, result: Any) -> None:
        resp = types.JSONRPCResponse(jsonrpc="2.0", id=msg_id, result=result)
        await self._send(types.JSONRPCMessage(resp))

    async def send_error(
        self,
        msg_id: int | str,
        code: int,
        message: str,
        data: Any = None,
    ) -> None:
        error_data = types.ErrorData(code=code, message=message, data=data)
        err = types.JSONRPCError(jsonrpc="2.0", id=msg_id, error=error_data)
        await self._send(types.JSONRPCMessage(err))

    # -- convenience for handlers ------------------------------------------

    async def request(self, method: str, params: dict[str, Any]) -> Any:
        """Send a request to the host and await its response."""
        return await self.dispatcher.send_request(method, params, self)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a fire-and-forget notification to the host."""
        await self.dispatcher.send_notification(method, params, self)


@asynccontextmanager
async def mcpl_stdio_server(
    transport: McplTransport,
    stdin: anyio.AsyncFile[str] | None = None,
    stdout: anyio.AsyncFile[str] | None = None,
    on_ready: OnReadyHook | None = None,
):
    """An stdio transport that intercepts MCPL methods.

    Same shape as `mcp.server.stdio.stdio_server` — yields a (read, write)
    pair of memory object streams that FastMCP plugs into. The difference
    is in the stdin reader: each JSON line is classified before being
    forwarded. `stdin`/`stdout` are accepted for tests; production passes
    nothing and the real process streams are used.
    """
    if stdin is None:
        stdin = anyio.wrap_file(
            TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
        )
    if stdout is None:
        stdout = anyio.wrap_file(TextIOWrapper(sys.stdout.buffer, encoding="utf-8"))

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    transport._bind_write_stream(write_stream)

    async def intercepting_stdin_reader() -> None:
        try:
            async with read_stream_writer:
                async for line in stdin:
                    stripped = line.strip()
                    if not stripped:
                        continue

                    try:
                        raw = json.loads(stripped)
                    except json.JSONDecodeError as exc:
                        await read_stream_writer.send(exc)
                        continue

                    if not isinstance(raw, dict):
                        # Malformed — send to FastMCP which will validate and
                        # surface the error via its session.
                        try:
                            msg = types.JSONRPCMessage.model_validate(raw)
                            await read_stream_writer.send(SessionMessage(msg))
                        except Exception as exc:  # noqa: BLE001
                            await read_stream_writer.send(exc)
                        continue

                    method = raw.get("method")

                    # Peek at initialize to remember host MCPL capabilities;
                    # then forward to FastMCP unchanged.
                    if method == "initialize":
                        try:
                            transport.host_capabilities = (
                                raw.get("params", {})
                                .get("capabilities", {})
                                .get("experimental", {})
                                .get("mcpl")
                            )
                            if transport.host_capabilities:
                                log.info(
                                    "Host MCPL detected: version=%s",
                                    transport.host_capabilities.get("version"),
                                )
                        except Exception:  # noqa: BLE001 — never fail handshake
                            log.warning(
                                "Failed to extract host MCPL capabilities", exc_info=True
                            )

                    # Host is done initializing — fire on_ready, then forward.
                    # Only fire if the host actually advertised MCPL; vanilla
                    # MCP hosts ignore our server-initiated traffic anyway.
                    if (
                        method == "notifications/initialized"
                        and on_ready is not None
                        and transport.host_capabilities is not None
                    ):
                        log.info("Host signaled initialized — firing on_ready hook")
                        tg.start_soon(_safe_on_ready, on_ready, transport)

                    # MCPL inbound method → dispatch, do not forward to FastMCP.
                    if method in MCPL_METHODS:
                        await transport.dispatcher.handle_inbound(raw, transport)
                        continue

                    # JSON-RPC response (no method field, has id + result/error)
                    # — match against pending outbound requests.
                    if method is None and ("result" in raw or "error" in raw):
                        msg_id = raw.get("id")
                        if transport.dispatcher.is_pending_outbound(msg_id):
                            transport.dispatcher.resolve_outbound(msg_id, raw)
                            continue

                    # Standard MCP — forward to FastMCP.
                    try:
                        msg = types.JSONRPCMessage.model_validate(raw)
                        await read_stream_writer.send(SessionMessage(msg))
                    except Exception as exc:  # noqa: BLE001
                        await read_stream_writer.send(exc)
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async def stdout_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    json_str = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                    await stdout.write(json_str + "\n")
                    await stdout.flush()
        except anyio.ClosedResourceError:  # pragma: no cover
            await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        tg.start_soon(intercepting_stdin_reader)
        tg.start_soon(stdout_writer)
        try:
            yield read_stream, write_stream
        finally:
            transport._write_stream = None


async def _safe_on_ready(hook: OnReadyHook, transport: McplTransport) -> None:
    """Run the on_ready hook, swallowing exceptions so they don't kill the
    transport task group. Errors are logged."""
    try:
        await hook(transport)
    except Exception:  # noqa: BLE001
        log.exception("on_ready hook raised")


async def run_stdio_with_mcpl(
    mcp: FastMCP,
    dispatcher: McplDispatcher | None = None,
    on_ready: OnReadyHook | None = None,
) -> McplTransport:
    """Run FastMCP over the MCPL-intercepting stdio transport.

    `on_ready`, if provided, runs as a background task once the host's
    `notifications/initialized` arrives. That's the right moment to send
    `channels/register` and similar server-initiated traffic.

    Returns the McplTransport once the server exits — callers can inspect
    final state in tests. In production the coroutine never returns until
    the host disconnects.
    """
    if dispatcher is None:
        dispatcher = McplDispatcher()
    transport = McplTransport(dispatcher)
    async with mcpl_stdio_server(transport, on_ready=on_ready) as (
        read_stream,
        write_stream,
    ):
        await mcp._mcp_server.run(
            read_stream,
            write_stream,
            mcp._mcp_server.create_initialization_options(
                experimental_capabilities=build_experimental_capabilities(),
            ),
        )
    return transport
