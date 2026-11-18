"""Cancelled requests must not get a response (issue #359)."""

import anyio
import mcp.types as types
import pytest
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.shared.message import SessionMessage

from telegram_mcp.transport import install_protocol_compatibility


def _request(id_, method, params=None):
    return SessionMessage(
        types.JSONRPCMessage(
            types.JSONRPCRequest(jsonrpc="2.0", id=id_, method=method, params=params)
        )
    )


def _notification(method, params=None):
    return SessionMessage(
        types.JSONRPCMessage(
            types.JSONRPCNotification(jsonrpc="2.0", method=method, params=params)
        )
    )


@pytest.mark.asyncio
async def test_cancelled_request_gets_no_response():
    install_protocol_compatibility()

    server = Server("cancel-test")
    handler_started = anyio.Event()

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name="slow", inputSchema={"type": "object"})]

    @server.call_tool()
    async def call_tool(name, arguments):
        handler_started.set()
        await anyio.sleep_forever()

    to_server, server_in = anyio.create_memory_object_stream(10)
    server_out, from_server = anyio.create_memory_object_stream(10)
    init_options = InitializationOptions(
        server_name="cancel-test",
        server_version="0",
        capabilities=server.get_capabilities(NotificationOptions(), {}),
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(server.run, server_in, server_out, init_options)

        with anyio.fail_after(5):
            await to_server.send(
                _request(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                )
            )
            await from_server.receive()
            await to_server.send(_notification("notifications/initialized"))

            await to_server.send(_request(2, "tools/call", {"name": "slow", "arguments": {}}))
            await handler_started.wait()
            await to_server.send(_notification("notifications/cancelled", {"requestId": 2}))
            # The ping reply is the next thing we expect; anything for id 2 first is a bug.
            await to_server.send(_request(3, "ping"))

            ids = []
            while 3 not in ids:
                message = await from_server.receive()
                ids.append(getattr(message.message.root, "id", None))

        assert 2 not in ids
        tg.cancel_scope.cancel()
