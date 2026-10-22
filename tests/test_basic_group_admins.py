"""Basic groups have no participant filters and no ban list; the tools must not pretend."""

import json
from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import groups

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
FAMILY = types.Chat(
    id=777, title="Семья", photo=types.ChatPhotoEmpty(), participants_count=5, date=NOW, version=1
)
USERS = [
    types.User(id=1, first_name="Алексей", last_name="Иванов", access_hash=1),
    types.User(id=101, first_name="Иван", access_hash=2),
    types.User(id=202, first_name="Мария", last_name="Соколова", access_hash=3),
]


class FamilyServer(TelegramClient):
    def __init__(self):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        self.requests.append(request)
        if isinstance(request, functions.messages.GetFullChatRequest):
            participants = types.ChatParticipants(
                chat_id=FAMILY.id,
                version=1,
                participants=[
                    types.ChatParticipantCreator(user_id=1),
                    types.ChatParticipant(user_id=101, inviter_id=1, date=NOW),
                    types.ChatParticipantAdmin(user_id=202, inviter_id=1, date=NOW),
                ],
            )
            full = types.ChatFull(
                id=FAMILY.id,
                about="",
                participants=participants,
                notify_settings=types.PeerNotifySettings(),
            )
            return types.messages.ChatFull(full_chat=full, chats=[FAMILY], users=USERS)
        if isinstance(request, functions.messages.EditChatAdminRequest):
            return True
        raise AssertionError(f"unexpected request {type(request).__name__}")


@pytest.fixture
def family(monkeypatch):
    client = FamilyServer()
    people = {101: USERS[1], 202: USERS[2]}

    async def resolve(identifier, cl=None):
        return FAMILY if int(identifier) == -777 else people[int(identifier)]

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "resolve_entity", resolve)
    monkeypatch.setattr(groups, "ensure_connected", connected)
    return client


@pytest.mark.asyncio
async def test_get_admins_lists_only_the_creator_and_admins(family):
    result = json.loads(await groups.get_admins(chat_id=-777))

    assert [r["id"] for r in result["results"]] == [1, 202]


@pytest.mark.asyncio
async def test_get_banned_users_does_not_list_every_member(family):
    result = await groups.get_banned_users(chat_id=-777)

    assert result == "No banned users: a basic group has no ban list."
    assert family.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [groups.ban_user, groups.unban_user])
async def test_ban_and_unban_explain_that_basic_groups_have_no_bans(family, tool):
    result = await tool(chat_id=-777, user_id=101)

    assert "basic group" in result and "remove_user" in result
    assert family.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, is_admin", [(groups.promote_admin, True), (groups.demote_admin, False)]
)
async def test_promote_and_demote_use_the_basic_group_request(family, tool, is_admin):
    result = await tool(group_id=-777, user_id=101)

    assert result.startswith("Successfully")
    (request,) = family.requests
    assert isinstance(request, functions.messages.EditChatAdminRequest)
    assert (request.chat_id, request.user_id.user_id, request.is_admin) == (777, 101, is_admin)
