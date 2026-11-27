"""Current Events beta boundaries: cursors, filtering, HTTP auth and routing."""

import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel.server import request_ctx
from starlette.testclient import TestClient
from telethon.tl.types import User

from telegram_mcp import event_source as source, runtime as rt
from telegram_mcp.event_protocol import EventParams, EventRequest, install_events, EVENTS_EXTENSION
from telegram_mcp.tools import events
from telegram_mcp.transport.http import create_streamable_http_app
from test_mcp_events import params, response_json


@pytest.fixture(autouse=True)
def source_log(monkeypatch):
    monkeypatch.setattr(rt, "clients", {"default": object()})
    monkeypatch.setattr(rt, "get_client", lambda account=None: object())
    monkeypatch.setattr(rt, "resolve_entity", AsyncMock(return_value=User(id=123)))
    monkeypatch.setattr(events, "_event_boot", "a" * 16)
    monkeypatch.setattr(events, "_event_log", deque(maxlen=8))
    monkeypatch.setattr(events, "_event_seq", 0)
    monkeypatch.setattr(events, "_event_signal", None)
    monkeypatch.setattr(events, "_EVENT_LOG_SIZE", 8)
    for name in (
        "TELEGRAM_ALLOWED_CHAT_IDS",
        "TELEGRAM_BLOCKED_CHAT_IDS",
        "TELEGRAM_ALLOWED_TOPICS",
    ):
        monkeypatch.delenv(name, raising=False)


def append(message_id, **options):
    return events._append_event(
        {
            "account": "default",
            "chat_id": 123,
            "out": False,
            "message_id": message_id,
            "date": "2026-11-27T12:00:00Z",
            **options,
        }
    )


@pytest.mark.asyncio
async def test_poll_counts_matches_and_advances_over_unmatched_records():
    protocol = install_events(FastMCP("poll"))
    p = {"name": source.EVENT_NAME, "arguments": {"chat_id": 123}, "maxEvents": 1}
    first = await protocol.poll(p)
    assert first["events"] == [] and not first["truncated"]
    append(1, out=True)
    append(2)
    append(3, chat_id=999)
    append(4)
    append(5, out=True)
    second = await protocol.poll({**p, "cursor": first["cursor"]})
    assert [e["data"]["message_id"] for e in second["events"]] == [2]
    assert second["hasMore"] and source.position(second["cursor"])[0] == 3
    third = await protocol.poll({**p, "cursor": second["cursor"]})
    assert [e["data"]["message_id"] for e in third["events"]] == [4]
    assert not third["hasMore"] and source.position(third["cursor"])[0] == 5
    assert (await protocol.poll({**p, "cursor": third["cursor"]}))["events"] == []


@pytest.mark.asyncio
async def test_poll_recovery_restart_overrun_and_age(monkeypatch):
    protocol = install_events(FastMCP("recovery"))
    p = {"name": source.EVENT_NAME, "arguments": {"chat_id": 123}}
    initial = (await protocol.poll(p))["cursor"]
    for i in range(1, 11):
        append(i)
    recovered = await protocol.poll({**p, "cursor": initial})
    assert recovered["truncated"]
    assert [e["data"]["message_id"] for e in recovered["events"]] == list(range(3, 11))
    monkeypatch.setattr(events, "_event_boot", "b" * 16)
    assert (await protocol.poll({**p, "cursor": recovered["cursor"]}))["truncated"]
    monkeypatch.setattr(source.time, "time", lambda: 1_000)
    events._event_log.clear()
    events._event_seq = 0
    start = (await protocol.poll(p))["cursor"]
    append(1, date=source.utc_timestamp(999))
    append(2, date=source.utc_timestamp(1))
    append(3, date=source.utc_timestamp(999))
    result = await protocol.poll({**p, "cursor": start, "maxAgeMs": 2_000})
    assert [e["data"]["message_id"] for e in result["events"]] == [1, 3]
    assert result["truncated"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"maxEvents": 0},
        {"maxEvents": True},
        {"maxAgeMs": -1},
        {"cursor": 3},
        {"cursor": "broken"},
        {"heartbeatMs": 10},
    ],
)
async def test_poll_invalid_options(options):
    protocol = install_events(FastMCP("invalid-poll"))
    with pytest.raises(source.EventError):
        await protocol.poll({"name": source.EVENT_NAME, "arguments": {"chat_id": 123}, **options})


@pytest.mark.asyncio
async def test_stream_grants_are_per_request_and_live_old_dates_are_delivered(monkeypatch):
    protocol = install_events(FastMCP("heartbeat"))
    frames = []

    async def send(message):
        frames.append(message.message.model_dump(by_alias=True))

    context = SimpleNamespace(
        request=None,
        request_id="watch",
        session=SimpleNamespace(_write_stream=SimpleNamespace(send=send)),
    )
    token = request_ctx.set(context)
    try:
        for suggested, granted in [(1, 1000), (15000, 15000), (999999, 30000)]:
            task = asyncio.create_task(
                protocol.stream(
                    {
                        "name": source.EVENT_NAME,
                        "arguments": {"chat_id": 123},
                        "heartbeatMs": suggested,
                        "maxAgeMs": 1,
                    }
                )
            )
            for _ in range(50):
                if frames:
                    break
                await asyncio.sleep(0.001)
            assert frames.pop()["params"]["heartbeatMs"] == granted
            append(suggested, date="2000-01-01T00:00:00Z")
            for _ in range(50):
                if frames:
                    break
                await asyncio.sleep(0.001)
            assert frames.pop()["params"]["data"]["message_id"] == suggested
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    finally:
        request_ctx.reset(token)


def app():
    server = FastMCP("auth-test", stateless_http=True)

    @server.tool()
    def ping():
        return "pong"

    return create_streamable_http_app(server)


def rpc(client, method, arguments=None, headers=None):
    h = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": method,
    }
    if arguments and "name" in arguments:
        h["Mcp-Name"] = arguments["name"]
    h.update(headers or {})
    return client.post(
        "/mcp",
        headers=h,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": arguments or {}},
    )


def test_anonymous_catalog_and_webhook_failure_are_actionable(tmp_path):
    with TestClient(app(), base_url="http://localhost:8765") as client:
        catalog = response_json(rpc(client, "events/list"))["result"]
        assert catalog["resultType"] == "complete"
        assert catalog["events"][0]["delivery"] == ["poll", "push"]
        assert "authenticated" in catalog["events"][0]["description"]
        denied = response_json(rpc(client, "events/subscribe", params()))["error"]
        assert denied["code"] == -32024 and "Configure" in denied["message"]
        assert not (tmp_path / "state" / "telegram-mcp" / "subscriptions.json").exists()
        assert response_json(rpc(client, "tools/list"))["result"]["tools"]


def test_authenticated_http_checks_all_mcp_calls_and_discovery(monkeypatch):
    monkeypatch.setenv("TELEGRAM_MCP_HTTP_TOKEN", "s" * 32)
    with TestClient(app(), base_url="http://localhost:8765") as client:
        for method in ("server/discover", "tools/list", "events/list"):
            assert rpc(client, method).status_code == 401
            assert (
                rpc(client, method, headers={"Authorization": "Bearer wrong"}).status_code == 401
            )
        headers = {"Authorization": "Bearer " + "s" * 32}
        assert client.get("/mcp").status_code == 401
        discovered = response_json(rpc(client, "server/discover", headers=headers))["result"]
        assert discovered["capabilities"]["extensions"][EVENTS_EXTENSION] == {}
        assert response_json(rpc(client, "events/list", headers=headers))["result"]["events"][0][
            "delivery"
        ] == ["poll", "push", "webhook"]
        assert response_json(rpc(client, "tools/list", headers=headers))["result"]["tools"]
        assert client.get("/.well-known/oauth-authorization-server").status_code != 200


def test_modern_ordinary_results_have_discriminator_and_legacy_results_do_not():
    with TestClient(app(), base_url="http://localhost:8765") as client:
        for method, arguments in (("tools/list", {}), ("tools/call", {"name": "ping"})):
            assert (
                response_json(rpc(client, method, arguments))["result"]["resultType"] == "complete"
            )
        old = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        )
        assert "resultType" not in response_json(old)["result"]


@pytest.mark.parametrize("header,value", [("Mcp-Method", "events/poll"), ("Mcp-Name", "wrong")])
def test_event_header_mismatch_rejected_before_handler(header, value):
    with TestClient(app(), base_url="http://localhost:8765") as client:
        response = rpc(client, "events/stream", {"name": source.EVENT_NAME}, {header: value})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


def test_missing_event_headers_and_legacy_http_compatibility():
    with TestClient(app(), base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "events/poll",
                "params": {"name": source.EVENT_NAME},
            },
        )
        assert response.status_code == 400 and response.json()["error"]["code"] == -32020
        assert response_json(
            client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            )
        )["result"]["tools"]


@pytest.mark.parametrize("token", ["", "short", "x" * 31, "x" * 32 + "\n"])
def test_invalid_server_token_fails_at_configuration(monkeypatch, token):
    monkeypatch.setenv("TELEGRAM_MCP_HTTP_TOKEN", token)
    with pytest.raises(ValueError, match="TELEGRAM_MCP_HTTP_TOKEN"):
        app()


@pytest.mark.asyncio
async def test_replay_missing_timestamp_is_stable_and_unmatched_age_is_ignored():
    args = await source.normalize_arguments({"chat_id": 123})
    first = append(1, date=None)
    assert first["date"] and source.message_event(first) == source.message_event(first)
    append(2, chat_id=999, date="invalid-unmatched-date")
    records, _, _, _ = source.read_page(args, 0, max_age=999999, replay_head=2)
    assert [r["message_id"] for r in records] == [1]


@pytest.mark.asyncio
async def test_scope_revocation_checks_bound_username_and_allowlist(monkeypatch):
    monkeypatch.setattr(
        rt, "resolve_entity", AsyncMock(return_value=User(id=123, username="owner"))
    )
    args = await source.normalize_arguments({"chat_id": "@owner"})
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "@owner")
    source.require_scope(args)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "999")
    with pytest.raises(source.EventError, match="revoked"):
        source.require_scope(args)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS")
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@owner")
    with pytest.raises(source.EventError, match="revoked"):
        source.require_scope(args)


def test_encoded_event_headers_and_duplicate_protocol_version():
    import base64

    with TestClient(app(), base_url="http://localhost:8765") as client:
        encoded = "=?base64?" + base64.b64encode(source.EVENT_NAME.encode()).decode() + "?="
        result = response_json(
            rpc(
                client,
                "events/poll",
                {"name": source.EVENT_NAME, "arguments": {"chat_id": 123}},
                {"Mcp-Name": encoded},
            )
        )
        assert "result" in result
        assert (
            rpc(
                client, "events/poll", {"name": source.EVENT_NAME}, {"Mcp-Name": "=?base64?!!!?="}
            ).status_code
            == 400
        )
        assert (
            rpc(
                client, "events/list", headers={"Mcp-Method": "=?base64?ZXZlbnRzL2xpc3Q=?="}
            ).status_code
            == 400
        )
        encoded = "=?base64?" + base64.b64encode("未知".encode()).decode() + "?="
        assert (
            response_json(rpc(client, "events/poll", {"name": "未知"}, {"Mcp-Name": encoded}))[
                "error"
            ]["code"]
            == -32023
        )
        response = client.post(
            "/mcp",
            headers=[
                ("Accept", "application/json, text/event-stream"),
                ("MCP-Protocol-Version", "2026-07-28"),
                ("MCP-Protocol-Version", "2025-11-25"),
            ],
            json={"jsonrpc": "2.0", "id": 1, "method": "events/list"},
        )
        assert response.status_code == 400


@pytest.mark.asyncio
async def test_gap_active_advances_before_first_unread_event(monkeypatch):
    protocol = install_events(FastMCP("gap"))
    frames = []

    async def send(message):
        frame = message.message.model_dump(by_alias=True)
        frames.append(frame)
        if frame.get("method") == "notifications/events/active" and len(frames) == 1:
            for i in range(10):
                append(i)

    context = SimpleNamespace(
        request=None,
        request_id=1,
        session=SimpleNamespace(_write_stream=SimpleNamespace(send=send)),
    )
    token = request_ctx.set(context)
    task = asyncio.create_task(
        protocol.stream({"name": source.EVENT_NAME, "arguments": {"chat_id": 123}})
    )
    try:
        for _ in range(50):
            if len(frames) > 2:
                break
            await asyncio.sleep(0.001)
        gap = frames[1]["params"]
        assert gap["truncated"] and not source.position(gap["cursor"])[1]
        assert (
            source.position(gap["cursor"])[0] < source.position(frames[2]["params"]["cursor"])[0]
        )
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        request_ctx.reset(token)


def test_static_token_rotation_preserves_webhook_owner(monkeypatch, tmp_path):
    from telegram_mcp import event_webhooks as hooks
    from telegram_mcp.transport.auth import STATIC_PRINCIPAL
    import json

    async def callback(url, subscription, delivery_id, body, signing_secrets):
        data = json.loads(body)
        return 200, json.dumps({"challenge": data.get("challenge")}, ensure_ascii=False).encode()

    path = tmp_path / "telegram-mcp" / "subscriptions.json"
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(hooks, "post_webhook", callback)
    ids = []
    for token in ("a" * 32, "b" * 32):
        monkeypatch.setenv("TELEGRAM_MCP_HTTP_TOKEN", token)
        with TestClient(app(), base_url="http://localhost:8765") as client:
            result = response_json(
                rpc(client, "events/subscribe", params(), {"Authorization": "Bearer " + token})
            )["result"]
            ids.append(result["id"])
            assert (
                rpc(client, "tools/list", headers={"Authorization": "Bearer wrong"}).status_code
                == 401
            )
    assert ids[0] == ids[1]
    json.loads(path.read_text())
    assert STATIC_PRINCIPAL in path.read_text()


@pytest.mark.asyncio
async def test_replay_age_cutoff_does_not_move_between_pages(monkeypatch):
    args = await source.normalize_arguments({"chat_id": 123})
    append(1, date=source.utc_timestamp(999))
    append(2, date=source.utc_timestamp(999))
    first, after, _, more = source.read_page(
        args, 0, 1, max_age=2000, replay_head=2, replay_time=1000
    )
    assert more and first[0]["message_id"] == 1
    monkeypatch.setattr(source.time, "time", lambda: 5000)
    second, _, gap, _ = source.read_page(
        args, after, 1, max_age=2000, replay_head=2, replay_time=1000
    )
    assert not gap and second[0]["message_id"] == 2
