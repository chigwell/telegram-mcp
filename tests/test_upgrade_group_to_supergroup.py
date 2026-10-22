"""Tests for upgrade_group_to_supergroup (messages.MigrateChat)."""

from types import SimpleNamespace

import pytest
import telethon
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import groups

BASIC_GROUP_ID = 5317263588  # Telegram's positive Chat.id
BOT_API_ID = -BASIC_GROUP_ID  # the spelling MCP callers typically pass
SUPERGROUP_ID = 2045678901
SUPERGROUP_MARKED_ID = -1000000000000 - SUPERGROUP_ID


class FakeClient:
    """Records every raw request and returns a canned result."""

    def __init__(self, result=None, error=None):
        self.requests = []
        self.result = result
        self.error = error

    async def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def _basic_group(**kwargs):
    return types.Chat(
        id=BASIC_GROUP_ID,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
        **kwargs,
    )


def _channel(*, megagroup):
    return types.Channel(
        id=SUPERGROUP_ID,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        date=None,
        megagroup=megagroup,
        broadcast=not megagroup,
        access_hash=67890,
    )


def _migrated_to():
    return types.InputChannel(channel_id=SUPERGROUP_ID, access_hash=67890)


def _patch(monkeypatch, client, entity):
    async def fake_resolve(identifier, cl):
        return entity

    async def fake_ensure_connected(cl):
        return None

    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "resolve_entity", fake_resolve)
    monkeypatch.setattr(groups, "ensure_connected", fake_ensure_connected)


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", [BOT_API_ID, str(BOT_API_ID)])
async def test_upgrade_sends_migrate_chat_with_resolved_id(monkeypatch, chat_id):
    result = SimpleNamespace(
        chats=[
            _basic_group(deactivated=True, migrated_to=_migrated_to()),
            _channel(megagroup=True),
        ]
    )
    client = FakeClient(result=result)
    _patch(monkeypatch, client, _basic_group(creator=True))

    message = await groups.upgrade_group_to_supergroup(chat_id=chat_id, account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.MigrateChatRequest)
    assert request.chat_id == BASIC_GROUP_ID
    assert message == (
        f"Group 'Basic Group' upgraded to supergroup with ID: {SUPERGROUP_MARKED_ID}"
    )


@pytest.mark.asyncio
async def test_upgrade_falls_back_to_migrated_to_when_channel_missing(monkeypatch):
    result = SimpleNamespace(chats=[_basic_group(deactivated=True, migrated_to=_migrated_to())])
    client = FakeClient(result=result)
    _patch(monkeypatch, client, _basic_group(creator=True))

    message = await groups.upgrade_group_to_supergroup(chat_id=BOT_API_ID, account=None)

    assert message.endswith(f"supergroup with ID: {SUPERGROUP_MARKED_ID}")


@pytest.mark.asyncio
async def test_upgrade_without_new_id_in_result_says_so(monkeypatch):
    client = FakeClient(result=SimpleNamespace(chats=[]))
    _patch(monkeypatch, client, _basic_group(creator=True))

    message = await groups.upgrade_group_to_supergroup(chat_id=BOT_API_ID, account=None)

    assert "did not return the new ID" in message


@pytest.mark.asyncio
async def test_upgrade_already_migrated_group_returns_supergroup_id(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group(deactivated=True, migrated_to=_migrated_to()))

    message = await groups.upgrade_group_to_supergroup(chat_id=BOT_API_ID, account=None)

    assert client.requests == []
    assert message == (
        f"Group 'Basic Group' was already upgraded to supergroup with ID: {SUPERGROUP_MARKED_ID}"
    )


@pytest.mark.asyncio
async def test_upgrade_supergroup_is_a_no_op(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _channel(megagroup=True))

    message = await groups.upgrade_group_to_supergroup(chat_id=SUPERGROUP_MARKED_ID, account=None)

    assert client.requests == []
    assert message == (
        f"Chat {SUPERGROUP_MARKED_ID} is already a supergroup (ID: {SUPERGROUP_MARKED_ID})."
    )


@pytest.mark.asyncio
async def test_upgrade_broadcast_channel_is_rejected(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _channel(megagroup=False))

    message = await groups.upgrade_group_to_supergroup(chat_id=SUPERGROUP_MARKED_ID, account=None)

    assert client.requests == []
    assert message.startswith("Error:")
    assert "broadcast channel" in message


@pytest.mark.asyncio
async def test_upgrade_user_is_rejected(monkeypatch):
    client = FakeClient()
    user = types.User(id=42, first_name="Alice")
    _patch(monkeypatch, client, user)

    message = await groups.upgrade_group_to_supergroup(chat_id=42, account=None)

    assert client.requests == []
    assert message == "Error: chat 42 is not a basic group."


@pytest.mark.asyncio
async def test_upgrade_requires_creator(monkeypatch):
    error = telethon.errors.rpcerrorlist.ChatAdminRequiredError(request=None)
    client = FakeClient(error=error)
    _patch(monkeypatch, client, _basic_group(creator=False))

    message = await groups.upgrade_group_to_supergroup(chat_id=BOT_API_ID, account=None)

    assert message == "Error: only the group creator can upgrade it to a supergroup."


def test_upgrade_is_a_destructive_write_tool():
    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    annotations = registered["upgrade_group_to_supergroup"].annotations
    assert not annotations.readOnlyHint  # hidden by TELEGRAM_EXPOSED_TOOLS=read-only
    assert annotations.destructiveHint is True
