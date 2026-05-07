"""Phase 2 tests — McplTransport outbound message construction
and the intercepting stdin classifier.

For the stdin classifier we feed a fake AsyncFile through `mcpl_stdio_server`
and read both the FastMCP-bound forwards (from `read_stream`) and the
host-bound outbound (from `stdout`). FastMCP itself is never started.
"""

import asyncio
import json

import anyio
import pytest

from telegram_mcp.mcpl.dispatcher import MCPL_METHODS, McplDispatcher
from telegram_mcp.mcpl.transport import McplTransport, mcpl_stdio_server


# ---------------------------------------------------------------------------
# Outbound message construction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_notification_emits_jsonrpc_notification():
    transport = McplTransport(McplDispatcher())
    send_stream, receive_stream = anyio.create_memory_object_stream(4)
    transport._bind_write_stream(send_stream)

    await transport.send_notification("channels/changed", {"added": [{"id": "x"}]})
    session_message = receive_stream.receive_nowait()
    payload = json.loads(
        session_message.message.model_dump_json(by_alias=True, exclude_none=True)
    )
    assert payload == {
        "jsonrpc": "2.0",
        "method": "channels/changed",
        "params": {"added": [{"id": "x"}]},
    }


@pytest.mark.asyncio
async def test_send_request_emits_jsonrpc_request_with_id():
    transport = McplTransport(McplDispatcher())
    send_stream, receive_stream = anyio.create_memory_object_stream(4)
    transport._bind_write_stream(send_stream)

    await transport.send_request(7, "channels/incoming", {"messages": []})
    session_message = receive_stream.receive_nowait()
    payload = json.loads(
        session_message.message.model_dump_json(by_alias=True, exclude_none=True)
    )
    assert payload == {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "channels/incoming",
        "params": {"messages": []},
    }


@pytest.mark.asyncio
async def test_send_response_and_error():
    transport = McplTransport(McplDispatcher())
    send_stream, receive_stream = anyio.create_memory_object_stream(4)
    transport._bind_write_stream(send_stream)

    await transport.send_response(11, {"delivered": True, "messageId": "abc"})
    await transport.send_error(12, code=-32601, message="Method not found: foo")

    response = json.loads(
        receive_stream.receive_nowait().message.model_dump_json(
            by_alias=True, exclude_none=True
        )
    )
    error = json.loads(
        receive_stream.receive_nowait().message.model_dump_json(
            by_alias=True, exclude_none=True
        )
    )
    assert response == {
        "jsonrpc": "2.0",
        "id": 11,
        "result": {"delivered": True, "messageId": "abc"},
    }
    assert error == {
        "jsonrpc": "2.0",
        "id": 12,
        "error": {"code": -32601, "message": "Method not found: foo"},
    }


@pytest.mark.asyncio
async def test_send_before_bind_raises():
    transport = McplTransport(McplDispatcher())
    with pytest.raises(RuntimeError, match="not started"):
        await transport.send_notification("channels/changed", {})


# ---------------------------------------------------------------------------
# Intercepting classifier
# ---------------------------------------------------------------------------


class FakeAsyncFile:
    """Minimal AsyncFile-shaped fake for stdin/stdout in tests."""

    def __init__(self, content: str = "") -> None:
        # Preserve newlines so `async for line` yields one line at a time.
        self._lines = content.splitlines(keepends=True)
        self._idx = 0
        self.written: list[str] = []

    def __aiter__(self) -> "FakeAsyncFile":
        return self

    async def __anext__(self) -> str:
        if self._idx >= len(self._lines):
            raise StopAsyncIteration
        line = self._lines[self._idx]
        self._idx += 1
        return line

    async def write(self, data: str) -> None:
        self.written.append(data)

    async def flush(self) -> None:
        pass


async def _drain_lines(fake_stdout: FakeAsyncFile) -> list[dict]:
    return [json.loads(line.rstrip()) for line in fake_stdout.written if line.strip()]


@pytest.mark.asyncio
async def test_inbound_mcpl_request_routes_to_handler_not_to_fastmcp():
    """An MCPL method must be intercepted; FastMCP must never see it."""
    dispatcher = McplDispatcher()
    handler_calls: list[dict] = []

    async def publish_handler(params: dict) -> dict:
        handler_calls.append(params)
        return {"delivered": True, "messageId": "msg-1"}

    dispatcher.register("channels/publish", publish_handler)
    transport = McplTransport(dispatcher)

    line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "channels/publish",
            "params": {"channelId": "telegram:dm:1", "content": []},
        }
    )
    stdin = FakeAsyncFile(line + "\n")
    stdout = FakeAsyncFile()

    forwarded: list = []

    async def consume_forwarded(read_stream):
        async for item in read_stream:
            forwarded.append(item)

    with anyio.fail_after(2.0):
        async with mcpl_stdio_server(transport, stdin=stdin, stdout=stdout) as (
            read_stream,
            write_stream,
        ):
            consumer = asyncio.create_task(consume_forwarded(read_stream))
            # Give the reader/writer time to flow the message end-to-end.
            for _ in range(20):
                if stdout.written and not stdout.written[-1].endswith(""):
                    pass
                if stdout.written:
                    break
                await asyncio.sleep(0.01)
            await write_stream.aclose()
            await read_stream.aclose()
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, anyio.ClosedResourceError):
                pass

    assert handler_calls == [{"channelId": "telegram:dm:1", "content": []}]
    payloads = await _drain_lines(stdout)
    assert payloads == [
        {"jsonrpc": "2.0", "id": 99, "result": {"delivered": True, "messageId": "msg-1"}}
    ]
    # Critically, no MCPL traffic leaked into the FastMCP-bound stream.
    assert forwarded == []


@pytest.mark.asyncio
async def test_initialize_extracts_host_capabilities_and_forwards():
    """initialize must reach FastMCP; host MCPL caps must be captured."""
    dispatcher = McplDispatcher()
    transport = McplTransport(dispatcher)

    init_line = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "experimental": {
                        "mcpl": {"version": "0.4", "pushEvents": True}
                    }
                },
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
    )
    stdin = FakeAsyncFile(init_line + "\n")
    stdout = FakeAsyncFile()

    forwarded: list = []

    async def consume_forwarded(read_stream):
        async for item in read_stream:
            forwarded.append(item)

    with anyio.fail_after(2.0):
        async with mcpl_stdio_server(transport, stdin=stdin, stdout=stdout) as (
            read_stream,
            write_stream,
        ):
            consumer = asyncio.create_task(consume_forwarded(read_stream))
            for _ in range(20):
                if forwarded:
                    break
                await asyncio.sleep(0.01)
            await write_stream.aclose()
            await read_stream.aclose()
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, anyio.ClosedResourceError):
                pass

    # initialize must be forwarded — FastMCP owns it
    assert len(forwarded) == 1
    # And we captured the host's MCPL caps
    assert transport.host_capabilities == {"version": "0.4", "pushEvents": True}


@pytest.mark.asyncio
async def test_response_to_outbound_request_does_not_reach_fastmcp():
    """When we have a pending outbound, the matching response is consumed by us."""
    dispatcher = McplDispatcher()
    transport = McplTransport(dispatcher)

    # Pre-register a pending outbound request so id=42 is recognized.
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    dispatcher._pending_outbound[42] = fut

    response_line = json.dumps(
        {"jsonrpc": "2.0", "id": 42, "result": {"ok": True}}
    )
    stdin = FakeAsyncFile(response_line + "\n")
    stdout = FakeAsyncFile()

    forwarded: list = []

    async def consume_forwarded(read_stream):
        async for item in read_stream:
            forwarded.append(item)

    with anyio.fail_after(2.0):
        async with mcpl_stdio_server(transport, stdin=stdin, stdout=stdout) as (
            read_stream,
            write_stream,
        ):
            consumer = asyncio.create_task(consume_forwarded(read_stream))
            # Wait for the future to resolve
            for _ in range(50):
                if fut.done():
                    break
                await asyncio.sleep(0.01)
            await write_stream.aclose()
            await read_stream.aclose()
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, anyio.ClosedResourceError):
                pass

    assert fut.done()
    assert fut.result() == {"ok": True}
    assert forwarded == []  # not forwarded to FastMCP
    assert stdout.written == []  # no outbound response written


def test_method_set_covers_all_spec_methods_we_handle():
    """The dispatcher's method set must list every method the transport intercepts."""
    expected_subset = {
        "channels/incoming",
        "channels/publish",
        "channels/list",
        "channels/typing",
        "context/beforeInference",
    }
    assert expected_subset.issubset(MCPL_METHODS)


# ---------------------------------------------------------------------------
# on_ready hook
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ready_fires_after_initialized_when_host_supports_mcpl():
    """initialize (with mcpl caps) followed by notifications/initialized
    must trigger the on_ready callback."""
    dispatcher = McplDispatcher()
    transport = McplTransport(dispatcher)

    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "experimental": {"mcpl": {"version": "0.4", "pushEvents": True}}
                },
                "clientInfo": {"name": "test", "version": "1"},
            },
        }
    )
    initialized = json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    stdin = FakeAsyncFile(init + "\n" + initialized + "\n")
    stdout = FakeAsyncFile()

    fired_with: list[McplTransport] = []

    async def on_ready(t: McplTransport) -> None:
        fired_with.append(t)
        # Send a notification while the transport is alive
        await t.send_notification("channels/register", {"channels": []})

    forwarded: list = []

    async def consume_forwarded(read_stream):
        async for item in read_stream:
            forwarded.append(item)

    with anyio.fail_after(2.0):
        async with mcpl_stdio_server(
            transport, stdin=stdin, stdout=stdout, on_ready=on_ready
        ) as (read_stream, write_stream):
            consumer = asyncio.create_task(consume_forwarded(read_stream))
            for _ in range(50):
                if fired_with and stdout.written:
                    break
                await asyncio.sleep(0.01)
            await write_stream.aclose()
            await read_stream.aclose()
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, anyio.ClosedResourceError):
                pass

    assert fired_with == [transport]
    payloads = [json.loads(line.rstrip()) for line in stdout.written if line.strip()]
    assert payloads == [
        {"jsonrpc": "2.0", "method": "channels/register", "params": {"channels": []}}
    ]


@pytest.mark.asyncio
async def test_on_ready_does_not_fire_for_vanilla_mcp_host():
    """Hosts that don't advertise MCPL skip on_ready entirely."""
    dispatcher = McplDispatcher()
    transport = McplTransport(dispatcher)

    init = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},  # no experimental.mcpl
                "clientInfo": {"name": "vanilla-mcp", "version": "1"},
            },
        }
    )
    initialized = json.dumps(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    stdin = FakeAsyncFile(init + "\n" + initialized + "\n")
    stdout = FakeAsyncFile()

    fired = []

    async def on_ready(t: McplTransport) -> None:
        fired.append(True)

    forwarded: list = []

    async def consume_forwarded(read_stream):
        async for item in read_stream:
            forwarded.append(item)

    with anyio.fail_after(2.0):
        async with mcpl_stdio_server(
            transport, stdin=stdin, stdout=stdout, on_ready=on_ready
        ) as (read_stream, write_stream):
            consumer = asyncio.create_task(consume_forwarded(read_stream))
            # Both messages should be forwarded
            for _ in range(50):
                if len(forwarded) >= 2:
                    break
                await asyncio.sleep(0.01)
            await write_stream.aclose()
            await read_stream.aclose()
            consumer.cancel()
            try:
                await consumer
            except (asyncio.CancelledError, anyio.ClosedResourceError):
                pass

    assert fired == []  # vanilla MCP — on_ready intentionally skipped
    assert len(forwarded) == 2  # initialize + notifications/initialized both forwarded
