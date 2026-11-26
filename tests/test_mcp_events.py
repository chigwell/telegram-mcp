"""MCP Events subscriptions, delivery and HTTP integration without Telegram access."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import socket
import time
from collections import deque
from unittest.mock import AsyncMock

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from starlette.testclient import TestClient
from telethon.tl.types import User

from telegram_mcp import event_webhooks as hooks, runtime as rt
from telegram_mcp.tools import events
from telegram_mcp.transport.http import create_streamable_http_app

SECRET = "whsec_" + base64.b64encode(b"s" * 32).decode()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in ("TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_BLOCKED_CHAT_IDS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(rt, "clients", {"default": object()})
    monkeypatch.setattr(events, "_event_log", deque(maxlen=2000))
    monkeypatch.setattr(events, "_event_seq", 0)
    monkeypatch.setattr(events, "_event_signal", None)
    monkeypatch.setattr(rt, "resolve_entity", AsyncMock(return_value=User(id=123)))
    monkeypatch.setattr(rt, "get_client", lambda account: object())


@pytest.fixture
def manager(tmp_path):
    return hooks.MessageSubscriptions(tmp_path / "private" / "subscriptions.json")


def params(**args):
    return {
        "name": hooks.EVENT_NAME,
        "arguments": {"chat_id": 123, **args},
        "delivery": {
            "mode": "webhook",
            "url": "https://receiver.example/callback",
            "secret": SECRET,
        },
    }


@pytest.fixture
def receiver(monkeypatch):
    received = []

    async def post(url, sub_id, event_id, body, secrets):
        data = json.loads(body)
        if data.get("type") == "verification":
            return 200, json.dumps({"challenge": data["challenge"]}).encode()
        received.append((sub_id, data, secrets))
        return 200, b"{}"

    monkeypatch.setattr(hooks, "post_webhook", post)
    return received


def record(message_id=1, **kwargs):
    return {
        "account": "default",
        "chat_id": 123,
        "message_id": message_id,
        "out": False,
        "date": "2026-11-25T10:00:00Z",
        **kwargs,
    }


async def eventually(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.005)
    assert predicate()


@pytest.mark.asyncio
async def test_defaults_identity_persistence_rotation_and_unsubscribe(manager, receiver):
    p = params()
    first = await manager.subscribe(p)
    second = await manager.subscribe(params(include_outgoing=False))
    assert first["id"] == second["id"]
    row = manager.store.rows[first["id"]]
    assert not row["arguments"]["include_outgoing"]
    assert not row["arguments"]["all_chats"]
    assert first["cursor"] is None and not first["truncated"]
    assert hooks.SubscriptionStore(manager.store.path).rows == manager.store.rows
    if os.name != "nt":
        assert manager.store.path.stat().st_mode & 0o777 == 0o600
    p["delivery"]["secret"] = "whsec_" + base64.b64encode(b"n" * 32).decode()
    assert (await manager.subscribe(p))["id"] == first["id"]
    assert manager.store.rows[first["id"]]["previous_secret"] == SECRET
    p["delivery"].pop("secret")
    await manager.unsubscribe(p)
    await manager.unsubscribe(p)
    assert not manager.store.rows


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [
        {},
        {"all_chats": False},
        {"all_chats": True, "chat_id": 123},
        {"chat_id": True},
        {"chat_id": 123, "include_outgoing": "false"},
        {"all_chats": True, "account": "missing"},
    ],
)
async def test_invalid_scope(manager, args):
    with pytest.raises(hooks.EventError):
        await hooks.normalize_arguments(args)


@pytest.mark.asyncio
async def test_explicit_account_wide_and_multi_account(manager, receiver, monkeypatch):
    p = params()
    p["arguments"] = {"all_chats": True}
    result = await manager.subscribe(p)
    row = manager.store.rows[result["id"]]
    assert manager.allowed(row, record(chat_id=999))
    assert not manager.allowed(row, record(out=True))
    assert not manager.allowed(row, record(account="other"))
    row["arguments"]["include_outgoing"] = True
    assert manager.allowed(row, record(out=True))
    monkeypatch.setattr(rt, "clients", {"default": object(), "other": object()})
    with pytest.raises(hooks.EventError):
        await hooks.normalize_arguments({"all_chats": True})


@pytest.mark.asyncio
async def test_rechecks_chat_topic_and_expiration(manager, receiver, monkeypatch):
    result = await manager.subscribe(params())
    row = manager.store.rows[result["id"]]
    assert manager.allowed(row, record())
    assert not manager.allowed(row, record(chat_id=124))
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "123")
    assert not manager.allowed(row, record())
    monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123:7")
    assert manager.allowed(row, record(topic=7))
    assert not manager.allowed(row, record(topic=8))
    row["expires"] = time.time() - 1
    assert not manager.allowed(row, record(topic=7))


@pytest.mark.asyncio
async def test_blocked_subscribe_and_bad_callback(manager, receiver, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "123")
    with pytest.raises(hooks.EventError, match="access denied"):
        await manager.subscribe(params())
    monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS")

    async def bad(*args):
        return 200, b'{"challenge":"wrong"}'

    monkeypatch.setattr(hooks, "post_webhook", bad)
    with pytest.raises(hooks.EventError) as error:
        await manager.subscribe(params())
    assert error.value.code == -32015
    assert not manager.store.rows


@pytest.mark.asyncio
async def test_independent_subscribers_and_arrivals_during_delivery(
    manager, receiver, monkeypatch
):
    first = await manager.subscribe(params())
    second_params = params()
    second_params["delivery"]["url"] += "-second"
    second = await manager.subscribe(second_params)
    original = hooks.post_webhook
    appended = False

    async def post(*args):
        nonlocal appended
        if not appended:
            appended = True
            events._append_event(record(2))
        await asyncio.sleep(0)
        return await original(*args)

    monkeypatch.setattr(hooks, "post_webhook", post)
    await manager.start()
    try:
        events._append_event(record(1))
        events._append_event(record(3, out=True))
        events._append_event(record(4, chat_id=999))
        await eventually(lambda: len(receiver) == 4)
        for sub_id in (first["id"], second["id"]):
            assert [d["data"]["message_id"] for s, d, _ in receiver if s == sub_id] == [1, 2]
        assert set(receiver[0][1]["data"]) == {"account", "chat_id", "message_id", "timestamp"}
        assert len(events._event_log) == 4  # Webhooks do not consume the polling log.
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_restart_expiration_and_refresh(manager, receiver):
    p = params()
    p["ttlMs"] = 30
    result = await manager.subscribe(p)
    restored = hooks.MessageSubscriptions(manager.store.path)
    await restored.start()
    try:
        await asyncio.sleep(0.05)
        events._append_event(record())
        await asyncio.sleep(0.02)
        assert not receiver
        p["ttlMs"] = 1000
        assert (await restored.subscribe(p))["id"] == result["id"]
        events._append_event(record(2))
        await eventually(lambda: len(receiver) == 1)
        assert receiver[0][1]["data"]["message_id"] == 2
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,attempts", [(500, 4), (429, 4), (410, 1), (413, 1), (302, 1), (400, 1)]
)
async def test_retry_policy(manager, receiver, monkeypatch, status, attempts):
    result = await manager.subscribe(params())
    calls = []

    async def post(*args):
        calls.append(args)
        return status, b"{}"

    monkeypatch.setattr(hooks, "post_webhook", post)
    monkeypatch.setattr(hooks.asyncio, "sleep", AsyncMock())
    await manager._deliver(result["id"], record())
    assert len(calls) == attempts
    assert len({call[2] for call in calls}) == 1
    assert len({call[3] for call in calls}) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:pass@example.com",
        "https://example.com/#x",
        "https://example.com:8443/",
        None,
        "https://example.com/\r\nx",
    ],
)
def test_callback_syntax(url):
    with pytest.raises(hooks.EventError):
        hooks.callback_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "::1", "224.0.0.1", "::ffff:8.8.8.8"]
)
async def test_non_public_destinations_are_blocked(monkeypatch, ip):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "getaddrinfo",
        AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 443))]),
    )
    with pytest.raises(hooks.EventError, match="not public"):
        await hooks.post_webhook("https://example.com/", "sub", "evt", b"{}", [SECRET])


@pytest.mark.asyncio
async def test_pinned_tls_and_standard_webhooks_signature(monkeypatch):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "getaddrinfo",
        AsyncMock(return_value=[(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("8.8.8.8", 443))]),
    )
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "receiver.example"
        assert request.extensions["sni_hostname"] == "receiver.example"
        stamp = request.headers["webhook-timestamp"]
        expected = base64.b64encode(
            hmac.digest(b"s" * 32, f"evt.{stamp}.".encode() + request.content, "sha256")
        ).decode()
        assert request.headers["webhook-signature"] == "v1," + expected
        return httpx.Response(200, stream=httpx.ByteStream(b"{}"))

    def transport(**kwargs):
        assert kwargs["trust_env"] is False
        return httpx.MockTransport(handler)

    monkeypatch.setattr(hooks.httpx, "AsyncHTTPTransport", transport)
    assert await hooks.post_webhook(
        "https://receiver.example/callback?q=1", "sub", "evt", b'{"value":1}', [SECRET]
    ) == (200, b"{}")
    assert len(requests) == 1


def response_json(response):
    text = response.text
    if text.startswith("event:") or text.startswith("data:"):
        text = next(line[6:] for line in text.splitlines() if line.startswith("data: "))
    return json.loads(text)


def enabled_app(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_MCP_EVENTS_FILE", str(tmp_path / "subscriptions.json"))
    server = FastMCP("events-test", stateless_http=True)

    @server.tool()
    async def ping():
        return "pong"

    return create_streamable_http_app(server)


def test_http_discovery_initialize_and_tools_without_extra_auth(monkeypatch, tmp_path):
    app = enabled_app(monkeypatch, tmp_path)
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="http://localhost:8765") as client:
        body = {"jsonrpc": "2.0", "id": 1, "method": "events/list"}
        response = client.post("/mcp", headers=headers, json=body)
        assert response_json(response)["result"]["events"][0] == hooks.event_definition(
            ("push", "webhook")
        )
        discovery = client.post(
            "/mcp", headers=headers, json={**body, "method": "server/discover"}
        )
        assert response_json(discovery)["result"]["capabilities"]["events"] == {}
        initialize = client.post(
            "/mcp",
            headers=headers,
            json={
                **body,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )
        assert '"events":{}' in initialize.text
        tool = client.post(
            "/mcp",
            headers=headers,
            json={**body, "method": "tools/call", "params": {"name": "ping"}},
        )
        assert "pong" in tool.text
        assert (
            client.post("/mcp", headers=headers, json={**body, "method": "tools/list"}).status_code
            == 200
        )
        denied = client.post(
            "/mcp", headers={**headers, "Origin": "https://evil.example"}, json=body
        )
        assert denied.status_code == 403


@pytest.mark.asyncio
async def test_unsubscribe_after_access_revoked_and_alias_changes(manager, receiver, monkeypatch):
    p = params(chat_id="@original")
    result = await manager.subscribe(p)
    monkeypatch.setattr(rt, "resolve_entity", AsyncMock(return_value=User(id=999)))
    renewed = await manager.subscribe(p)
    assert renewed["id"] == result["id"]
    # Refresh resolves the bound ID rather than the reassigned username.
    assert rt.resolve_entity.call_args.args[0] == 123
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "123")
    monkeypatch.setattr(
        rt,
        "resolve_entity",
        AsyncMock(side_effect=AssertionError("No Telegram lookup during unsubscribe")),
    )
    p["delivery"].pop("secret")
    await manager.unsubscribe(p)
    assert not manager.store.rows


@pytest.mark.asyncio
async def test_store_rejects_concurrent_processes(manager):
    from telegram_mcp.singleton import SessionLockError

    await manager.start()
    other = hooks.MessageSubscriptions(manager.store.path)
    try:
        with pytest.raises(SessionLockError):
            await other.start()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_expired_rows_release_quota_and_rotation_survives_refresh(
    manager, receiver, monkeypatch
):
    monkeypatch.setattr(hooks, "MAX_SUBSCRIPTIONS", 1)
    result = await manager.subscribe(params())
    manager.store.rows[result["id"]]["expires"] = time.time() - 1
    p = params()
    p["delivery"]["url"] += "new"
    result = await manager.subscribe(p)
    p["delivery"]["secret"] = "whsec_" + base64.b64encode(b"n" * 32).decode()
    await manager.subscribe(p)
    await manager.subscribe(p)
    assert manager.store.rows[result["id"]]["previous_secret"] == SECRET
    assert len(manager.store.rows) == 1


@pytest.mark.asyncio
async def test_unknown_entity_fails_closed_for_username_blocklist(manager, receiver, monkeypatch):
    result = await manager.subscribe(params())
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@blocked")
    assert not manager.allowed(manager.store.rows[result["id"]], record())


def test_mounted_host_checks_and_meta(monkeypatch, tmp_path):
    from starlette.applications import Starlette
    from starlette.routing import Mount

    app = enabled_app(monkeypatch, tmp_path)
    mounted = Starlette(routes=[Mount("/telegram", app)])
    headers = {"Accept": "application/json, text/event-stream"}
    with (
        TestClient(app, base_url="http://localhost:8765"),
        TestClient(mounted, base_url="http://localhost:8765") as client,
    ):
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "events/list",
            "params": {"_meta": {"progressToken": "test"}},
        }
        assert response_json(client.post("/telegram/mcp", headers=headers, json=body))["result"][
            "events"
        ]
        assert (
            client.post(
                "/telegram/mcp", headers={**headers, "Host": "evil.example"}, json=body
            ).status_code
            == 421
        )
        assert (
            client.post(
                "/telegram/mcp", headers=headers, json={**body, "method": "tools/list"}
            ).status_code
            == 200
        )


@pytest.mark.asyncio
async def test_subscription_does_not_block_normal_tool_request(monkeypatch, tmp_path):
    app = enabled_app(monkeypatch, tmp_path)
    service = app.app.state.telegram_webhooks
    entered = asyncio.Event()
    release = asyncio.Event()

    async def verification(*args):
        entered.set()
        await release.wait()

    monkeypatch.setattr(service, "_verify", verification)
    async with (
        app.app.router.lifespan_context(app.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765"
        ) as client,
    ):
        headers = {
            "Accept": "application/json, text/event-stream",
        }
        subscribe = asyncio.create_task(
            client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "events/subscribe", "params": params()},
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        response = await asyncio.wait_for(
            client.post(
                "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "events/list"}
            ),
            1,
        )
        assert response_json(response)["result"]["events"]
        release.set()
        assert response_json(await subscribe)["result"]["id"]
    await service.close()


@pytest.mark.asyncio
async def test_http_refuses_unstarted_subscription_service(monkeypatch, tmp_path):
    app = enabled_app(monkeypatch, tmp_path)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765"
    ) as client:
        with pytest.raises(RuntimeError, match="Task group is not initialized"):
            await client.post(
                "/mcp",
                headers={"Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "events/subscribe", "params": params()},
            )
    assert not (tmp_path / "subscriptions.json").exists()


@pytest.mark.asyncio
async def test_lifespan_startup_failure_is_explicit(monkeypatch, tmp_path):
    app = enabled_app(monkeypatch, tmp_path)
    (tmp_path / "subscriptions.json").write_text("{}")
    monkeypatch.setattr(
        app.app.state.telegram_webhooks, "start", AsyncMock(side_effect=OSError("locked"))
    )
    output = []
    with pytest.raises(OSError, match="locked"):
        await app(
            {"type": "lifespan"},
            AsyncMock(return_value={"type": "lifespan.startup"}),
            AsyncMock(side_effect=output.append),
        )
    assert output[0]["type"] == "lifespan.startup.failed"


@pytest.mark.asyncio
async def test_expiration_during_refresh_replaces_pending_worker(manager, receiver, monkeypatch):
    p = params()
    result = await manager.subscribe(p)
    await manager.start()
    try:
        original_worker = manager.tasks[result["id"]]
        original_worker.cancel()
        await asyncio.gather(original_worker, return_exceptions=True)
        pending = asyncio.create_task(asyncio.sleep(100))
        manager.tasks[result["id"]] = pending
        manager.store.rows[result["id"]]["expires"] = time.time() + 0.005
        p["delivery"]["secret"] = "whsec_" + base64.b64encode(b"n" * 32).decode()

        async def verify(*args):
            await asyncio.sleep(0.02)

        monkeypatch.setattr(manager, "_verify", verify)
        await manager.subscribe(p)
        assert manager.tasks[result["id"]] is not pending
        await asyncio.sleep(0)
        assert pending.cancelled()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_max_age_does_not_change_non_replay_subscription(manager, receiver):
    p = params()
    first = await manager.subscribe(p)
    p["maxAgeMs"] = 1000
    assert (await manager.subscribe(p))["id"] == first["id"]
    p["maxAgeMs"] = True
    with pytest.raises(hooks.EventError):
        await manager.subscribe(p)


def test_webhooks_belong_to_the_current_http_application(monkeypatch, tmp_path, receiver):
    from telegram_mcp.event_protocol import install_events
    from telegram_mcp.transport.stdio import run_stdio_async

    server = FastMCP("separate-apps", stateless_http=True)
    monkeypatch.setenv("TELEGRAM_MCP_EVENTS_FILE", str(tmp_path / "one.json"))
    first = create_streamable_http_app(server)
    monkeypatch.setenv("TELEGRAM_MCP_EVENTS_FILE", str(tmp_path / "two.json"))
    second = create_streamable_http_app(server)
    assert first.app.state.telegram_webhooks is not second.app.state.telegram_webhooks
    assert install_events(server).catalog()["events"][0]["delivery"] == ["push"]
    with TestClient(first, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "events/list"},
        )
        assert response_json(response)["result"]["events"][0]["delivery"] == ["push", "webhook"]
        assert not first.app.state.telegram_webhooks.running
        assert not (tmp_path / "one.json").exists()
        subscribed = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 2, "method": "events/subscribe", "params": params()},
        )
        assert "result" in response_json(subscribed)
        assert first.app.state.telegram_webhooks.running
        assert not second.app.state.telegram_webhooks.running
    assert install_events(server).catalog()["events"][0]["delivery"] == ["push"]


def test_disabled_log_keeps_http_tools_working(monkeypatch, tmp_path):
    monkeypatch.setattr(events, "_EVENT_LOG_SIZE", 0)
    app = enabled_app(monkeypatch, tmp_path)
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "events/list"}
        )
        assert response_json(response)["result"] == {"events": []}
        tool = client.post(
            "/mcp",
            headers=headers,
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "ping"}},
        )
        assert "pong" in tool.text
    assert not (tmp_path / "subscriptions.json").exists()


@pytest.mark.asyncio
async def test_failed_start_releases_store_and_workers(manager, receiver, monkeypatch):
    await manager.subscribe(params())
    save = hooks.SubscriptionStore.save
    failed = False

    def fail_once(store):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("write failed")
        save(store)

    monkeypatch.setattr(hooks.SubscriptionStore, "save", fail_once)
    with pytest.raises(OSError, match="write failed"):
        await manager.start()
    assert not manager.running
    assert not manager.tasks
    assert manager.store_lock is None
    replacement = hooks.MessageSubscriptions(manager.store.path)
    await replacement.start()
    await replacement.close()
