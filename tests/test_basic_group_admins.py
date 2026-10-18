"""Tests for promote_admin / demote_admin in basic groups (messages.EditChatAdmin)."""

import pytest
import telethon
from telethon.tl import functions, types

from telegram_mcp.tools import groups

BASIC_GROUP_ID = 5317263588  # Telegram's positive Chat.id
BOT_API_ID = -BASIC_GROUP_ID  # the spelling MCP callers typically pass
SUPERGROUP_ID = 2045678901
SUPERGROUP_MARKED_ID = -1000000000000 - SUPERGROUP_ID
USER_ID = 4242


class FakeClient:
    """Records every raw request and optionally raises an error."""

    def __init__(self, error=None):
        self.requests = []
        self.error = error

    async def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error


def _basic_group():
    return types.Chat(
        id=BASIC_GROUP_ID,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )


def _supergroup():
    return types.Channel(
        id=SUPERGROUP_ID,
        title="Super Group",
        photo=types.ChatPhotoEmpty(),
        date=None,
        megagroup=True,
        access_hash=67890,
    )


def _user():
    return types.User(id=USER_ID, first_name="Alice", access_hash=111)


def _patch(monkeypatch, client, chat):
    entities = {str(USER_ID): _user()}

    async def fake_resolve(identifier, cl):
        return entities.get(str(identifier), chat)

    async def fake_ensure_connected(cl):
        return None

    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "resolve_entity", fake_resolve)
    monkeypatch.setattr(groups, "ensure_connected", fake_ensure_connected)


@pytest.mark.asyncio
@pytest.mark.parametrize("group_id", [BOT_API_ID, str(BOT_API_ID)])
async def test_promote_in_basic_group_sends_edit_chat_admin(monkeypatch, group_id):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group())

    message = await groups.promote_admin(group_id=group_id, user_id=USER_ID, account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.EditChatAdminRequest)
    assert request.chat_id == BASIC_GROUP_ID
    assert request.user_id.id == USER_ID
    assert request.is_admin is True
    assert message == f"Successfully promoted user {USER_ID} to admin in Basic Group"


@pytest.mark.asyncio
async def test_promote_in_basic_group_with_custom_rights_adds_note(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group())

    message = await groups.promote_admin(
        group_id=BOT_API_ID, user_id=USER_ID, rights={"ban_users": False}, account=None
    )

    (request,) = client.requests
    assert isinstance(request, functions.messages.EditChatAdminRequest)
    assert request.is_admin is True
    assert message.startswith(f"Successfully promoted user {USER_ID} to admin in Basic Group")
    assert "basic groups only support full admin" in message
    assert "upgrade_group_to_supergroup" in message


@pytest.mark.asyncio
async def test_demote_in_basic_group_sends_edit_chat_admin(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group())

    message = await groups.demote_admin(group_id=BOT_API_ID, user_id=USER_ID, account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.EditChatAdminRequest)
    assert request.chat_id == BASIC_GROUP_ID
    assert request.user_id.id == USER_ID
    assert request.is_admin is False
    assert message == f"Successfully demoted user {USER_ID} from admin in Basic Group"


@pytest.mark.asyncio
async def test_promote_in_supergroup_still_sends_channels_edit_admin(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _supergroup())

    message = await groups.promote_admin(
        group_id=SUPERGROUP_MARKED_ID, user_id=USER_ID, rights={"ban_users": False}, account=None
    )

    (request,) = client.requests
    assert isinstance(request, functions.channels.EditAdminRequest)
    assert request.admin_rights.ban_users is False
    assert request.admin_rights.delete_messages is True
    assert request.rank == "Admin"
    assert message == f"Successfully promoted user {USER_ID} to admin in Super Group"


@pytest.mark.asyncio
async def test_demote_in_supergroup_still_sends_channels_edit_admin(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _supergroup())

    message = await groups.demote_admin(
        group_id=SUPERGROUP_MARKED_ID, user_id=USER_ID, account=None
    )

    (request,) = client.requests
    assert isinstance(request, functions.channels.EditAdminRequest)
    assert request.admin_rights.delete_messages is False
    assert request.rank == ""
    assert message == f"Successfully demoted user {USER_ID} from admin in Super Group"


@pytest.mark.asyncio
async def test_basic_group_not_mutual_contact_is_explained(monkeypatch):
    error = telethon.errors.rpcerrorlist.UserNotMutualContactError(request=None)
    client = FakeClient(error=error)
    _patch(monkeypatch, client, _basic_group())

    promoted = await groups.promote_admin(group_id=BOT_API_ID, user_id=USER_ID, account=None)
    demoted = await groups.demote_admin(group_id=BOT_API_ID, user_id=USER_ID, account=None)

    assert promoted.startswith("Error: Cannot promote users who are not mutual contacts")
    assert demoted.startswith("Error: Cannot modify admin status of users who are not mutual")


@pytest.mark.asyncio
async def test_basic_group_errors_use_log_and_format_error(monkeypatch):
    error = telethon.errors.rpcerrorlist.ChatAdminRequiredError(request=None)
    client = FakeClient(error=error)
    _patch(monkeypatch, client, _basic_group())
    calls = []

    def fake_format(function_name, exc, **kwargs):
        calls.append((function_name, exc, kwargs))
        return f"formatted {function_name}"

    monkeypatch.setattr(groups, "log_and_format_error", fake_format)

    promoted = await groups.promote_admin(group_id=BOT_API_ID, user_id=USER_ID, account=None)
    demoted = await groups.demote_admin(group_id=BOT_API_ID, user_id=USER_ID, account=None)

    assert promoted == "formatted promote_admin"
    assert demoted == "formatted demote_admin"
    assert [(name, exc) for name, exc, _ in calls] == [
        ("promote_admin", error),
        ("demote_admin", error),
    ]
    assert calls[0][2] == {"group_id": BOT_API_ID, "user_id": USER_ID}
