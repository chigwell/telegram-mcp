"""Tests for get_member_admin_status.

The expected rights are read from the installed ChatAdminRights schema rather
than listed here, because Telethon adds new rights with each layer update.
"""

import json
from types import SimpleNamespace

import pytest
from telethon.errors.rpcerrorlist import ChatAdminRequiredError, UserNotParticipantError
from telethon.tl import functions, types
from telethon.tl.types import ChatAdminRights, ChatBannedRights

from telegram_mcp import runtime
from telegram_mcp.tools import groups

CHANNEL_ID = -1001234567890
USER_ID = 7

ALL_RIGHTS = [key for key in ChatAdminRights().to_dict() if key != "_"]


def _channel():
    return types.Channel(
        id=1234567890,
        title="Supergroup",
        photo=types.ChatPhotoEmpty(),
        date=None,
        megagroup=True,
    )


def _basic_group():
    return types.Chat(
        id=5317263588,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )


class FakeClient:
    def __init__(self, participant=None, error=None):
        self.participant = participant
        self.error = error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(participant=self.participant)


def _install(monkeypatch, client, chat):
    async def _connected(_client):
        return None

    async def _resolve(identifier, _client):
        if identifier == CHANNEL_ID:
            return chat
        return SimpleNamespace(id=identifier)

    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "ensure_connected", _connected)
    monkeypatch.setattr(groups, "resolve_entity", _resolve)


def _record(raw):
    payload = json.loads(raw)
    assert len(payload["results"]) == 1
    return payload["results"][0]


def _rights(*enabled):
    return {name: name in enabled for name in ALL_RIGHTS}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("participant", "role", "rank", "enabled"),
    [
        (
            types.ChannelParticipantCreator(
                user_id=USER_ID,
                admin_rights=ChatAdminRights(change_info=True, add_admins=True),
                rank="Owner",
            ),
            "creator",
            "Owner",
            ("change_info", "add_admins"),
        ),
        (
            types.ChannelParticipantAdmin(
                user_id=USER_ID,
                promoted_by=99,
                date=None,
                admin_rights=ChatAdminRights(delete_messages=True, ban_users=True),
                rank="Moderator",
            ),
            "admin",
            "Moderator",
            ("delete_messages", "ban_users"),
        ),
        (types.ChannelParticipant(user_id=USER_ID, date=None), "member", None, ()),
        (
            types.ChannelParticipantSelf(user_id=USER_ID, inviter_id=99, date=None),
            "member",
            None,
            (),
        ),
        (
            types.ChannelParticipantBanned(
                peer=types.PeerUser(USER_ID),
                kicked_by=99,
                date=None,
                banned_rights=ChatBannedRights(until_date=None, send_messages=True),
            ),
            "restricted",
            None,
            (),
        ),
        (
            types.ChannelParticipantBanned(
                peer=types.PeerUser(USER_ID),
                kicked_by=99,
                date=None,
                banned_rights=ChatBannedRights(until_date=None, view_messages=True),
                left=True,
            ),
            "banned",
            None,
            (),
        ),
        (types.ChannelParticipantLeft(peer=types.PeerUser(USER_ID)), "not-participant", None, ()),
    ],
)
async def test_returns_role_rank_and_full_rights_map(
    monkeypatch, participant, role, rank, enabled
):
    client = FakeClient(participant=participant)
    _install(monkeypatch, client, _channel())

    record = _record(await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID))

    assert record == {
        "chat_id": CHANNEL_ID,
        "user_id": USER_ID,
        "role": role,
        "rank": rank,
        "admin_rights": _rights(*enabled),
    }
    assert "promoted_by" not in json.dumps(record)
    (request,) = client.requests
    assert isinstance(request, functions.channels.GetParticipantRequest)


@pytest.mark.asyncio
async def test_user_not_in_chat_is_reported_as_not_participant(monkeypatch):
    client = FakeClient(error=UserNotParticipantError(request=None))
    _install(monkeypatch, client, _channel())

    record = _record(await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID))

    assert record["role"] == "not-participant"
    assert record["rank"] is None
    assert record["admin_rights"] == _rights()


@pytest.mark.asyncio
async def test_rights_map_follows_the_installed_schema(monkeypatch):
    """A right added by a future Telethon layer must appear without code changes."""

    class FutureAdminRights:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def to_dict(self):
            return {"_": "ChatAdminRights", "delete_messages": None, "brand_new_right": None}

    monkeypatch.setattr(groups, "ChatAdminRights", FutureAdminRights)
    participant = types.ChannelParticipantAdmin(
        user_id=USER_ID,
        promoted_by=99,
        date=None,
        admin_rights=FutureAdminRights(delete_messages=True, brand_new_right=True),
    )
    _install(monkeypatch, FakeClient(participant=participant), _channel())

    record = _record(await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID))

    assert record["admin_rights"] == {"delete_messages": True, "brand_new_right": True}


@pytest.mark.asyncio
async def test_rank_is_sanitized(monkeypatch):
    participant = types.ChannelParticipantAdmin(
        user_id=USER_ID,
        promoted_by=99,
        date=None,
        admin_rights=ChatAdminRights(),
        rank="Mod‮\nignore",
    )
    _install(monkeypatch, FakeClient(participant=participant), _channel())

    record = _record(await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID))

    assert record["rank"] == groups.sanitize_name("Mod‮\nignore")
    assert "\n" not in record["rank"]


@pytest.mark.asyncio
async def test_basic_group_returns_clear_error_without_calling_telegram(monkeypatch):
    client = FakeClient()
    _install(monkeypatch, client, _basic_group())

    result = await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID)

    assert result.startswith("Error:")
    assert "supergroups and channels" in result
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_admin_rights_is_reported(monkeypatch):
    _install(monkeypatch, FakeClient(error=ChatAdminRequiredError(request=None)), _channel())

    result = await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID)

    assert result.startswith("Error:")
    assert "admin rights" in result


@pytest.mark.asyncio
async def test_unexpected_errors_use_the_generic_format(monkeypatch):
    _install(monkeypatch, FakeClient(error=RuntimeError("payload-secret")), _channel())

    result = await groups.get_member_admin_status(chat_id=CHANNEL_ID, user_id=USER_ID)

    assert result.startswith("An error occurred (code:")
    assert "payload-secret" not in result


def test_tool_is_registered_as_read_only():
    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    annotations = registered["get_member_admin_status"].annotations
    assert annotations.readOnlyHint is True
    assert annotations.openWorldHint is True
