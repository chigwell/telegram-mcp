"""Tests for remove_user: eject a member without leaving a ban behind.

Telegram has no "remove participant" request. For supergroups the tool bans and
then unbans, and everything that can go wrong lives in the gap between those two
requests: a failed or cancelled second step leaves a real, permanent ban while
the tool's name promises the opposite. So the tests drive the tool against a
fake client that records every raw request and can fail any one of them, and
check what the tool *says* against what was actually *sent*.
"""

import asyncio
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError, rpcerrorlist
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import groups

BASIC_GROUP = types.Chat(
    id=555, title="Basic Group", photo=None, participants_count=3, date=None, version=1
)
SUPERGROUP = types.Channel(
    id=777, title="Supergroup", photo=None, date=None, megagroup=True, access_hash=67890
)
USER = types.User(id=41, access_hash=111)
ME = types.User(id=1, is_self=True, access_hash=222)
ENTITIES = {555: BASIC_GROUP, 777: SUPERGROUP, 41: USER, 1: ME, "me": ME}

# What channels.GetParticipant can say about USER.
MEMBER = types.ChannelParticipant(user_id=41, date=None)
LEFT = types.ChannelParticipantLeft(peer=types.PeerUser(41))
KICKED = types.ChannelParticipantBanned(
    peer=types.PeerUser(41),
    kicked_by=1,
    date=None,
    banned_rights=types.ChatBannedRights(until_date=None, view_messages=True),
    left=True,
)
RESTRICTED = types.ChannelParticipantBanned(
    peer=types.PeerUser(41),
    kicked_by=1,
    date=None,
    banned_rights=types.ChatBannedRights(until_date=None, send_messages=True),
    left=False,
)


def _label(request):
    """Name a raw request by intent so a test reads as a sequence of steps."""
    if isinstance(request, functions.channels.GetParticipantRequest):
        return "membership_check"
    if isinstance(request, functions.messages.DeleteChatUserRequest):
        return "delete_chat_user"
    if isinstance(request, functions.channels.EditBannedRequest):
        return "eject" if request.banned_rights.view_messages else "clear"
    raise AssertionError(f"unexpected request: {request!r}")


class FakeRemoveClient:
    """Records every raw request; fails the steps named in `fail`; answers the
    membership check with `participant`."""

    def __init__(self, participant=MEMBER, fail=None):
        self.requests = []
        self.participant = participant
        self.fail = fail or {}

    @property
    def sent(self):
        return [_label(r) for r in self.requests]

    async def __call__(self, request):
        self.requests.append(request)
        step = _label(request)
        if step in self.fail:
            raise self.fail[step]
        if step == "membership_check":
            return types.channels.ChannelParticipant(
                participant=self.participant, chats=[], users=[]
            )
        return SimpleNamespace()


def _patch(monkeypatch, client, delay=0):
    async def fake_resolve(entity_id, cl):
        return ENTITIES[entity_id]

    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "resolve_entity", fake_resolve)
    monkeypatch.setattr(groups, "_REMOVE_USER_UNBAN_DELAY", delay)


def _restrictions(rights):
    """Names of the ChatBannedRights flags that are set."""
    return {k for k, v in rights.to_dict().items() if k not in ("_", "until_date") and v}


@pytest.mark.asyncio
async def test_basic_group_removes_via_delete_chat_user(monkeypatch):
    client = FakeRemoveClient()
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=555, user_id=41, account=None)

    assert client.sent == ["delete_chat_user"]
    (req,) = client.requests
    assert req.chat_id == 555 and req.user_id is USER
    assert "User 41 removed from chat Basic Group" in result
    assert "No ban left in place" in result


@pytest.mark.asyncio
async def test_supergroup_checks_membership_ejects_then_clears_rights(monkeypatch):
    client = FakeRemoveClient()
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=777, user_id=41, account=None)

    assert client.sent == ["membership_check", "eject", "clear"]
    check, eject, clear = client.requests
    assert check.channel is SUPERGROUP and check.participant is USER
    assert eject.channel is SUPERGROUP and eject.participant is USER
    assert clear.channel is SUPERGROUP and clear.participant is USER
    # The first request ejects; the second must leave nothing restricted behind.
    assert _restrictions(eject.banned_rights) == {"view_messages"}
    assert _restrictions(clear.banned_rights) == set()
    assert clear.banned_rights.until_date is None
    assert "User 41 removed from chat Supergroup" in result
    assert "No ban left in place" in result


@pytest.mark.asyncio
async def test_restricted_member_is_still_removed(monkeypatch):
    client = FakeRemoveClient(participant=RESTRICTED)
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=777, user_id=41, account=None)

    assert client.sent == ["membership_check", "eject", "clear"]
    assert "No ban left in place" in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_id, client",
    [
        (777, FakeRemoveClient(participant=LEFT)),
        (
            777,
            FakeRemoveClient(
                fail={"membership_check": rpcerrorlist.UserNotParticipantError(request=None)}
            ),
        ),
        (
            555,
            FakeRemoveClient(
                fail={"delete_chat_user": rpcerrorlist.UserNotParticipantError(request=None)}
            ),
        ),
    ],
    ids=["supergroup-left", "supergroup-unknown", "basic-group"],
)
async def test_non_member_is_reported_and_nothing_is_banned(monkeypatch, chat_id, client):
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=chat_id, user_id=41, account=None)

    assert result == "Error: The user is not a member of this chat."
    assert "eject" not in client.sent


@pytest.mark.asyncio
async def test_already_banned_user_is_left_alone(monkeypatch):
    client = FakeRemoveClient(participant=KICKED)
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=777, user_id=41, account=None)

    assert result.startswith("Error: The user is already banned from this chat")
    assert "unban_user" in result
    assert client.sent == ["membership_check"]  # no eject, and no accidental unban


@pytest.mark.asyncio
async def test_failed_clear_step_reports_the_ban_it_left_behind(monkeypatch, caplog):
    client = FakeRemoveClient(fail={"clear": ConnectionError("clear-exception-secret")})
    _patch(monkeypatch, client)

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        result = await groups.remove_user(chat_id=777, user_id=41, account=None)

    assert client.sent == ["membership_check", "eject", "clear"]
    assert "currently BANNED" in result
    assert "unban_user" in result
    assert "ban could not be cleared" in caplog.text
    for secret in ("clear-exception-secret", "Traceback"):
        assert secret not in result
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_flood_wait_on_clear_step_names_the_ban_and_the_wait(monkeypatch):
    client = FakeRemoveClient(fail={"clear": FloodWaitError(request=None, capture=300)})
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=777, user_id=41, account=None)

    assert "currently BANNED" in result
    assert "300 seconds" in result
    assert "unban_user" in result
    assert "do NOT retry" in result


@pytest.mark.asyncio
async def test_cancellation_between_eject_and_clear_still_clears(monkeypatch):
    client = FakeRemoveClient()
    _patch(monkeypatch, client, delay=0.02)

    task = asyncio.create_task(groups.remove_user(chat_id=777, user_id=41, account=None))
    while "eject" not in client.sent:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The shielded pair keeps running after the caller has gone away.
    for _ in range(100):
        if "clear" in client.sent:
            break
        await asyncio.sleep(0.01)
    assert client.sent == ["membership_check", "eject", "clear"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["me", 1], ids=["me", "own-id"])
async def test_refuses_to_remove_the_current_account(monkeypatch, user_id):
    client = FakeRemoveClient()
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=777, user_id=user_id, account=None)

    assert result.startswith("Error: remove_user cannot target the current account")
    assert "leave_chat" in result
    assert client.sent == []


@pytest.mark.asyncio
async def test_private_chat_target_is_rejected_plainly(monkeypatch):
    client = FakeRemoveClient()
    _patch(monkeypatch, client)

    # chat_id=41 resolves to a User, i.e. a private chat.
    result = await groups.remove_user(chat_id=41, user_id=41, account=None)

    assert result == "Error: chat_id must be a group or channel, not a user."
    assert client.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, expected",
    [
        (rpcerrorlist.ChatAdminRequiredError(request=None), "Error: admin rights required"),
        (
            rpcerrorlist.UserAdminInvalidError(request=None),
            "Error: Cannot remove this user - they are an admin",
        ),
    ],
    ids=["not-admin", "target-is-admin"],
)
async def test_admin_failures_on_eject_are_reported_plainly(monkeypatch, error, expected):
    client = FakeRemoveClient(fail={"eject": error})
    _patch(monkeypatch, client)

    result = await groups.remove_user(chat_id=777, user_id=41, account=None)

    assert result.startswith(expected)
    assert client.sent == ["membership_check", "eject"]  # nothing sent after the refusal


@pytest.mark.asyncio
async def test_unknown_error_uses_sanitized_response_and_log(monkeypatch, caplog):
    client = FakeRemoveClient(fail={"delete_chat_user": RuntimeError("kick-exception-secret")})
    _patch(monkeypatch, client)

    with caplog.at_level("ERROR", logger=runtime.logger.name):
        result = await groups.remove_user(chat_id=555, user_id=41, account=None)

    assert result.startswith("An error occurred (code:")
    assert "kick-exception-secret" not in result
    assert "kick-exception-secret" not in caplog.text
