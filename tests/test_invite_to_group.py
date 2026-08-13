"""Tests for invite_to_group: basic groups vs channels/supergroups.

Regression guard: invite_to_group unconditionally used
channels.InviteToChannelRequest, which fails on basic (non-super) groups —
the entity cannot be cast to InputChannel. Basic groups must be handled via
messages.AddChatUserRequest, one user at a time.
"""

from types import SimpleNamespace

import pytest
from telethon.errors import rpcerrorlist
from telethon.tl import functions, types

from telegram_mcp.tools import groups


class FakeInviteClient:
    """Records every raw request; optionally raises per-user errors for
    AddChatUserRequest to simulate already-participant / privacy failures."""

    def __init__(self, add_user_errors=None):
        self.requests = []
        self.add_user_errors = add_user_errors or {}

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.messages.AddChatUserRequest):
            err = self.add_user_errors.get(request.user_id.id)
            if err is not None:
                raise err
            return SimpleNamespace()
        if isinstance(request, functions.channels.InviteToChannelRequest):
            return SimpleNamespace(users=list(request.users), count=len(request.users))
        raise AssertionError(f"unexpected request: {request!r}")


def _patch(monkeypatch, client, entity_map):
    async def fake_resolve(entity_id, cl):
        return entity_map[entity_id]

    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "resolve_entity", fake_resolve)


@pytest.mark.asyncio
async def test_basic_group_invites_via_add_chat_user(monkeypatch):
    # A basic group resolves to telethon Chat (not Channel); a plain namespace
    # stands in for it since the code only branches on isinstance(entity, Channel).
    group = SimpleNamespace(id=555, title="Basic Group")
    ok_user = SimpleNamespace(id=41)
    member = SimpleNamespace(id=42)
    private = SimpleNamespace(id=43)
    client = FakeInviteClient(
        add_user_errors={
            42: rpcerrorlist.UserAlreadyParticipantError(request=None),
            43: rpcerrorlist.UserPrivacyRestrictedError(request=None),
        }
    )
    _patch(monkeypatch, client, {555: group, 41: ok_user, 42: member, 43: private})

    result = await groups.invite_to_group(group_id=555, user_ids=[41, 42, 43], account=None)

    # Every user goes through messages.AddChatUser, never channels.InviteToChannel
    assert all(isinstance(r, functions.messages.AddChatUserRequest) for r in client.requests)
    assert [r.user_id.id for r in client.requests] == [41, 42, 43]
    assert all(r.chat_id == 555 and r.fwd_limit == 100 for r in client.requests)

    assert "Successfully invited 1 users to Basic Group" in result
    assert "1 already a participant" in result
    assert "43: UserPrivacyRestrictedError" in result


@pytest.mark.asyncio
async def test_channel_still_uses_invite_to_channel(monkeypatch):
    channel = types.Channel(id=777, title="Announcements", photo=None, date=None)
    u1, u2 = SimpleNamespace(id=41), SimpleNamespace(id=42)
    client = FakeInviteClient()
    _patch(monkeypatch, client, {777: channel, 41: u1, 42: u2})

    result = await groups.invite_to_group(group_id=777, user_ids=[41, 42], account=None)

    assert len(client.requests) == 1
    assert isinstance(client.requests[0], functions.channels.InviteToChannelRequest)
    assert "Successfully invited 2 users to Announcements" in result
