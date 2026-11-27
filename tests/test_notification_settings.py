"""Registered MCP tools expose notification state without reading message history."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from telethon import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import chats as main

NOW = 1800000000


@pytest.fixture
def api(monkeypatch):
    entity = types.Channel(
        id=123,
        title="Fixture",
        photo=types.ChatPhotoEmpty(),
        date=None,
        access_hash=456,
        megagroup=True,
    )
    client = AsyncMock()
    client.get_input_entity.return_value = types.InputPeerChannel(123, 456)
    monkeypatch.setattr(main, "get_client", lambda account=None: client)
    monkeypatch.setattr(main, "ensure_connected", AsyncMock())
    monkeypatch.setattr(runtime, "is_multi_mode", lambda: False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)
    monkeypatch.setattr(main, "resolve_entity", AsyncMock(return_value=entity))
    monkeypatch.setattr(main.time, "time", lambda: NOW)
    return NS(entity=entity, client=client)


async def call(name, **args):
    return json.loads(await main.mcp._tool_manager.get_tool(name).run(args))["results"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "until,muted",
    [(0, False), (NOW - 1, False), (NOW, False), (NOW + 1, True), (2147483647, True)],
)
async def test_explicit_mute_and_expiry_without_history(api, until, muted):
    api.client.return_value = types.PeerNotifySettings(
        mute_until=datetime.fromtimestamp(until, timezone.utc)
    )
    result = (await call("get_chat_notification_settings", chat_id=-1000000000123))[0]
    assert result == dict(
        chat_id=-1000000000123, muted=muted, mute_until=until, peer_mute_until=until, source="chat"
    )
    assert isinstance(api.client.call_args.args[0], functions.account.GetNotifySettingsRequest)
    api.client.assert_awaited_once()
    api.client.get_messages.assert_not_awaited()
    api.client.get_dialogs.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,peer_type",
    [
        ("user", types.InputNotifyUsers),
        ("group", types.InputNotifyChats),
        ("broadcast", types.InputNotifyBroadcasts),
    ],
)
async def test_inherited_mute_category(api, kind, peer_type):
    if kind == "user":
        api.entity = types.User(id=123, first_name="Fixture")
        main.resolve_entity.return_value = api.entity
    elif kind == "broadcast":
        api.entity.broadcast = True
        api.entity.megagroup = False
    api.client.side_effect = [
        types.PeerNotifySettings(),
        types.PeerNotifySettings(mute_until=NOW + 60),
    ]
    result = (await call("get_chat_notification_settings", chat_id="@fixture"))[0]
    assert result["muted"] is True and result["mute_until"] == NOW + 60
    assert result["peer_mute_until"] is None
    assert isinstance(api.client.call_args_list[1].args[0].peer, peer_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_missing_or_failed_settings_stay_unknown(api, failure):
    api.client.side_effect = [
        types.PeerNotifySettings(),
        RuntimeError("fixture") if failure else types.PeerNotifySettings(),
    ]
    result = (await call("get_chat_notification_settings", chat_id="@fixture"))[0]
    assert result["muted"] is None and result["mute_until"] is None
    assert result.get("error") == ("RuntimeError" if failure else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_chats", "list_chats"])
async def test_lists_expose_mute_reuse_defaults_and_respect_zero(api, tool):
    api.client.get_dialogs.return_value = [
        NS(
            entity=api.entity,
            unread_count=0,
            dialog=NS(notify_settings=types.PeerNotifySettings(mute_until=until)),
        )
        for until in [None, None, 0]
    ]
    api.client.return_value = types.PeerNotifySettings(mute_until=NOW + 60)
    result = await call(tool)
    assert [r["muted"] for r in result] == [True, True, False]
    assert [r["source"] for r in result] == ["default:groups", "default:groups", "chat"]
    api.client.assert_awaited_once()
    api.client.get_messages.assert_not_awaited()
    api.client.get_input_entity.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_chat_exposes_notification_state(api):
    api.client.return_value = types.PeerNotifySettings(mute_until=NOW + 60)
    api.client.get_participants.return_value = NS(total=1)
    api.client.get_dialogs.return_value = []
    api.client.get_messages.return_value = []
    result = json.loads(await main.get_chat(chat_id="@fixture"))
    assert result["muted"] is True and result["source"] == "chat"


@pytest.mark.asyncio
async def test_default_failure_is_cached_but_list_survives(api):
    api.client.get_dialogs.return_value = [
        NS(
            entity=api.entity,
            unread_count=0,
            dialog=NS(notify_settings=types.PeerNotifySettings()),
        )
        for _ in range(2)
    ]
    api.client.side_effect = RuntimeError("fixture")
    result = await call("list_chats")
    assert len(result) == 2
    assert all(r["muted"] is None for r in result)
    api.client.assert_awaited_once()


@pytest.mark.asyncio
async def test_unmuted_filter_excludes_inherited_mutes_and_unknown(api):
    api.client.get_dialogs.return_value = [
        NS(
            entity=api.entity,
            unread_count=0,
            dialog=NS(notify_settings=types.PeerNotifySettings(mute_until=until)),
        )
        for until in [None, 0]
    ]
    for default in [
        types.PeerNotifySettings(mute_until=NOW + 60),
        types.PeerNotifySettings(),
        RuntimeError("fixture"),
    ]:
        api.client.side_effect = None if not isinstance(default, Exception) else default
        api.client.return_value = default
        result = await call("list_chats", unmuted_only=True)
        assert len(result) == 1 and result[0]["muted"] is False
        assert result[0]["source"] == "chat"


@pytest.mark.asyncio
async def test_notification_tool_respects_chat_allowlist(api, monkeypatch):
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", {999})
    result = await main.get_chat_notification_settings(chat_id="@fixture")
    assert "restricted by privacy policy" in result
    api.client.assert_not_awaited()
    api.client.get_input_entity.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_account_routes_notification_requests(api, monkeypatch):
    other = AsyncMock()
    other.get_input_entity.return_value = types.InputPeerChannel(123, 456)
    other.return_value = types.PeerNotifySettings(mute_until=NOW + 60)
    monkeypatch.setattr(
        main, "get_client", lambda account=None: other if account == "work" else api.client
    )
    result = (await call("get_chat_notification_settings", chat_id="@fixture", account="work"))[0]
    assert result["muted"] is True
    main.resolve_entity.assert_awaited_once_with("@fixture", other)
    api.client.assert_not_awaited()
