"""Wire-level Events tests: real subprocesses, framing, streams and cancellation."""

import asyncio
import contextlib
import json
import os
import socket
import sys
from collections import defaultdict
from contextlib import AsyncExitStack
from pathlib import Path

import httpx
import mcp.types as types
import pytest
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.message import SessionMessage

from telegram_mcp.event_protocol import CORRELATION_KEY

SERVER = Path(__file__).parent / "fixtures" / "event_server.py"
EVENT = "telegram.message.created"


class WireClient:
    def __init__(self, mode, read=None, write=None, url=None):
        self.mode, self.read, self.write, self.url = mode, read, write, url
        self.frames = defaultdict(asyncio.Queue)
        self.requests = {}
        self.counter = 100
        self.client = (
            httpx.AsyncClient(
                trust_env=False,
                timeout=httpx.Timeout(5, read=None),
                headers={
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                },
            )
            if url
            else None
        )
        self.reader = asyncio.create_task(self._read()) if read else None

    def route(self, message):
        request_id = message.get("id")
        if "method" in message:
            request_id = message.get("params", {}).get("_meta", {}).get(CORRELATION_KEY)
        self.frames[request_id].put_nowait(message)

    async def _read(self):
        async for message in self.read:
            if isinstance(message, Exception):
                raise message
            self.route(message.message.model_dump(by_alias=True, exclude_none=False))

    async def _post(self, payload):
        async with self.client.stream("POST", self.url, json=payload) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                if response.status_code != 202:
                    self.route(json.loads(await response.aread()))
                return
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    self.route(json.loads(line[6:]))

    async def send(self, request_id, method, params=None):
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if request_id is not None:
            payload["id"] = request_id
        if self.mode == "http":
            self.requests[request_id] = asyncio.create_task(self._post(payload))
        else:
            await self.write.send(SessionMessage(types.JSONRPCMessage.model_validate(payload)))

    async def next(self, request_id, method=None):
        async def receive():
            while True:
                message = await self.frames[request_id].get()
                if method is None or message.get("method") == method:
                    return message

        return await asyncio.wait_for(receive(), 3)

    async def request(self, method, params=None):
        self.counter += 1
        await self.send(self.counter, method, params)
        return await self.next(self.counter)

    async def tool(self, name, arguments=None):
        response = await self.request("tools/call", {"name": name, "arguments": arguments or {}})
        assert "error" not in response, response
        return response["result"]

    async def stream(self, request_id, arguments=None):
        await self.send(
            request_id,
            "events/stream",
            {
                "name": EVENT,
                "arguments": arguments or {"chat_id": 123},
                "cursor": None,
                "_meta": {"test": "metadata"},
            },
        )
        message = await self.next(request_id)
        assert message["method"] == "notifications/events/active", message
        assert message["params"]["_meta"][CORRELATION_KEY] == request_id
        assert message["params"]["cursor"] is None

    async def cancel(self, request_id):
        if self.mode == "http":
            self.requests[request_id].cancel()
            await asyncio.gather(self.requests[request_id], return_exceptions=True)
        else:
            await self.send(None, "notifications/cancelled", {"requestId": request_id})

    async def count(self):
        result = await self.tool("stream_count")
        return (result.get("structuredContent") or json.loads(result["content"][0]["text"]))[
            "active"
        ]

    async def wait_count(self, expected):
        for _ in range(50):
            if await self.count() == expected:
                return
            await asyncio.sleep(0.01)
        assert await self.count() == expected

    async def close(self):
        tasks = list(self.requests.values()) + ([self.reader] if self.reader else [])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.client:
            await self.client.aclose()


def server_env(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    env.update(
        PYTHONPATH=str(SERVER.parents[2]),
        PYTHON_DOTENV_DISABLED="1",
        TELEGRAM_MCP_EVENTS_FILE=str(tmp_path / "subscriptions.json"),
    )
    return env


async def stop_process(process):
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()


@contextlib.asynccontextmanager
async def connect(mode, tmp_path):
    async with AsyncExitStack() as stack:
        if mode == "stdio":
            read, write = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=sys.executable,
                        args=[str(SERVER), "stdio"],
                        env=server_env(tmp_path),
                        cwd=str(tmp_path),
                    )
                )
            )
            client = WireClient(mode, read, write)
        else:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(SERVER),
                mode,
                str(port),
                env=server_env(tmp_path),
                cwd=str(tmp_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )

            stack.push_async_callback(stop_process, process)
            for _ in range(200):
                if process.returncode is not None:
                    raise AssertionError((await process.stderr.read()).decode())
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", port)
                    writer.close()
                    await writer.wait_closed()
                    break
                except OSError:
                    await asyncio.sleep(0.02)
            else:
                raise AssertionError("Test server did not start")
            if mode == "http":
                client = WireClient(mode, url=f"http://127.0.0.1:{port}/mcp")
            else:
                read, write = await stack.enter_async_context(
                    sse_client(f"http://127.0.0.1:{port}/sse")
                )
                client = WireClient(mode, read, write)
                client.url = f"http://127.0.0.1:{port}/sse"
        stack.push_async_callback(client.close)
        yield client


@contextlib.asynccontextmanager
async def initialized(mode, tmp_path):
    async with connect(mode, tmp_path) as client:
        initialized = await client.request(
            "initialize",
            {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "event-test", "version": "1"},
            },
        )
        assert initialized["result"]["capabilities"]["events"] == {}
        await client.send(None, "notifications/initialized")
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http", "sse"])
async def test_independent_streams_filter_cancel_and_tools(mode, tmp_path):
    async with initialized(mode, tmp_path) as wire:
        catalog = await wire.request("events/list")
        modes = catalog["result"]["events"][0]["delivery"]
        assert modes == (["push", "webhook"] if wire.mode == "http" else ["push"])
        await wire.stream(0)
        await wire.stream("second")
        await wire.wait_count(2)
        await wire.tool("emit", {"message_id": 1})
        for request_id in (0, "second"):
            frame = await wire.next(request_id, "notifications/events/event")
            assert frame["params"]["data"] == {
                "account": "default",
                "chat_id": 123,
                "message_id": 1,
                "timestamp": "2026-11-26T10:00:00Z",
            }
            assert frame["params"]["cursor"] is None
            assert frame["params"]["_meta"][CORRELATION_KEY] == request_id
        await wire.tool("emit", {"message_id": 2, "out": True})
        await wire.tool("emit", {"message_id": 3, "chat_id": 999})
        # A subsequent matching event proves the excluded ones did not enter either stream.
        await wire.tool("emit", {"message_id": 4})
        for request_id in (0, "second"):
            frame = await wire.next(request_id, "notifications/events/event")
            assert frame["params"]["data"]["message_id"] == 4
        await wire.cancel(0)
        await wire.wait_count(1)
        await wire.tool("emit", {"message_id": 5})
        assert (await wire.next("second", "notifications/events/event"))["params"]["data"][
            "message_id"
        ] == 5
        await wire.cancel("second")
        await wire.wait_count(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http", "sse"])
async def test_heartbeat_outlives_tool_timeout_and_no_replay(mode, tmp_path):
    async with initialized(mode, tmp_path) as wire:
        await wire.tool("emit", {"message_id": 9})
        await wire.stream("live")
        frame = await wire.next("live", "notifications/events/heartbeat")
        assert frame["params"]["cursor"] is None
        assert frame["params"]["_meta"][CORRELATION_KEY] == "live"
        result = await wire.tool("slow_tool")
        assert result["isError"] is True
        assert "GEN-TIMEOUT" in result["content"][0]["text"]
        await wire.tool("emit", {"message_id": 10})
        assert (await wire.next("live", "notifications/events/event"))["params"]["data"][
            "message_id"
        ] == 10
        await wire.cancel("live")
        await wire.wait_count(0)
        await wire.tool("emit", {"message_id": 11})
        await wire.stream("new")
        await wire.tool("emit", {"message_id": 12})
        assert (await wire.next("new", "notifications/events/event"))["params"]["data"][
            "message_id"
        ] == 12
        await wire.cancel("new")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http", "sse"])
async def test_stream_errors_and_server_close(mode, tmp_path):
    async with initialized(mode, tmp_path) as wire:
        invalid = await wire.request("events/stream", {"name": EVENT, "arguments": {}})
        assert invalid["error"]["code"] == -32602
        unknown = await wire.request("unrelated/unknown")
        assert "error" in unknown
        await wire.stream("closing", {"all_chats": True, "include_outgoing": True})
        await wire.tool("emit", {"chat_id": 999, "out": True})
        assert (await wire.next("closing", "notifications/events/event"))["params"]["data"][
            "chat_id"
        ] == 999
        await wire.tool("disable_log")
        closed = await wire.next("closing")
        while "method" in closed:
            closed = await wire.next("closing")
        assert closed["result"] == {"_meta": {}}
        assert (await wire.request("events/list"))["result"] == {"events": []}
        assert not (await wire.tool("stream_count")).get("isError")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stdio", "http", "sse"])
async def test_empty_request_id_and_immediate_cancellation(mode, tmp_path):
    async with initialized(mode, tmp_path) as wire:
        await wire.stream("")
        await wire.tool("emit")
        frame = await wire.next("", "notifications/events/event")
        assert frame["params"]["_meta"][CORRELATION_KEY] == ""
        await wire.cancel("")
        await wire.wait_count(0)
        await wire.send(
            "early", "events/stream", {"name": EVENT, "arguments": {"all_chats": True}}
        )
        await wire.cancel("early")
        await wire.wait_count(0)
        await wire.tool("emit", {"message_id": 2})
        assert not any("id" in frame for frame in list(wire.frames["early"]._queue))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["http", "sse"])
async def test_same_id_on_different_connections(mode, tmp_path):
    async with initialized(mode, tmp_path) as first:
        async with AsyncExitStack() as stack:
            if mode == "http":
                second = WireClient(mode, url=first.url)
            else:
                read, write = await stack.enter_async_context(sse_client(first.url))
                second = WireClient(mode, read, write)
            stack.push_async_callback(second.close)
            response = await second.request(
                "initialize",
                {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "second", "version": "1"},
                },
            )
            assert "error" not in response
            await second.send(None, "notifications/initialized")
            await first.stream("same", {"chat_id": 123})
            await second.stream("same", {"chat_id": 999})
            await first.wait_count(2)
            await first.tool("emit", {"chat_id": 123, "message_id": 1})
            await first.tool("emit", {"chat_id": 999, "message_id": 2})
            assert (await first.next("same", "notifications/events/event"))["params"]["data"][
                "message_id"
            ] == 1
            assert (await second.next("same", "notifications/events/event"))["params"]["data"][
                "message_id"
            ] == 2
            await first.cancel("same")
            await second.wait_count(1)
            await first.tool("emit", {"chat_id": 999, "message_id": 3})
            assert (await second.next("same", "notifications/events/event"))["params"]["data"][
                "message_id"
            ] == 3
            await second.cancel("same")
            await first.wait_count(0)


@pytest.mark.asyncio
async def test_webhook_and_stream_receive_independently(tmp_path):
    import base64

    async with initialized("http", tmp_path) as wire:
        params = {
            "name": EVENT,
            "arguments": {"chat_id": 123},
            "cursor": None,
            "maxAgeMs": 1000,
            "delivery": {
                "mode": "webhook",
                "url": "https://receiver.example/callback",
                "secret": "whsec_" + base64.b64encode(b"s" * 32).decode(),
            },
        }
        subscribed = await wire.request("events/subscribe", params)
        assert subscribed["result"]["cursor"] is None
        await wire.stream("stream")
        await wire.tool("emit")
        assert (await wire.next("stream", "notifications/events/event"))["params"]["data"][
            "message_id"
        ] == 1
        for _ in range(50):
            response = await wire.tool("webhook_count")
            count = json.loads(response["content"][0]["text"])["delivered"]
            if count == 1:
                break
            await asyncio.sleep(0.01)
        assert count == 1
        params["delivery"].pop("secret")
        assert (await wire.request("events/unsubscribe", params))["result"] == {}
        await wire.tool("emit", {"message_id": 2})
        assert (await wire.next("stream", "notifications/events/event"))["params"]["data"][
            "message_id"
        ] == 2
        assert (
            json.loads((await wire.tool("webhook_count"))["content"][0]["text"])["delivered"] == 1
        )
        await wire.cancel("stream")
