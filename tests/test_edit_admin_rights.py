"""edit_admin_rights: replace mode sets the whole rights set, patch mode only what is passed."""

import inspect
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.utilities.func_metadata import func_metadata
from pydantic import ValidationError
from telethon.errors.rpcerrorlist import RightForbiddenError, UserNotParticipantError
from telethon.tl import functions, types
from telethon.tl.types import ChatAdminRights

from telegram_mcp.tools import groups

CHAT_ID = -1001234567890
USER_ID = 7
LAYER_RIGHTS = list(inspect.signature(ChatAdminRights.__init__).parameters)[1:]
SUPERGROUP = types.Channel(
    id=1234567890, title="Supergroup", photo=types.ChatPhotoEmpty(), date=None, megagroup=True
)
ME = types.User(id=1, is_self=True)
TARGET = types.User(id=USER_ID)


def _rights(names):
    return ChatAdminRights(**{name: True for name in names})


def _admin(names, rank=None):
    return types.ChannelParticipantAdmin(
        user_id=USER_ID, promoted_by=1, date=None, admin_rights=_rights(names), rank=rank
    )


# The creator holds every right; an anonymous one also has the anonymous flag.
ANONYMOUS_CREATOR = types.ChannelParticipantCreator(
    user_id=1, admin_rights=_rights(LAYER_RIGHTS), rank="Owner"
)


class FakeClient:
    """Answers GetParticipant for yourself with `me` and for others with `target`."""

    def __init__(self, me=ANONYMOUS_CREATOR, target=None, lookup_error=None, edit_error=None):
        self.me = me
        self.target = target
        self.lookup_error = lookup_error
        self.edit_error = edit_error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.channels.GetParticipantRequest):
            if self.lookup_error:
                raise self.lookup_error
            is_self = isinstance(request.participant, types.InputPeerSelf)
            return SimpleNamespace(participant=self.me if is_self else self.target)
        if self.edit_error:
            raise self.edit_error
        return SimpleNamespace()

    def edits(self):
        return [r for r in self.requests if isinstance(r, functions.channels.EditAdminRequest)]


@pytest.fixture
def install(monkeypatch):
    def _install(client):
        async def _resolve(identifier, _client):
            if identifier == CHAT_ID:
                return SUPERGROUP
            return ME if identifier == "me" else TARGET

        async def _connected(_client=None):
            return None

        monkeypatch.setattr(groups, "get_client", lambda account=None: client)
        monkeypatch.setattr(groups, "resolve_entity", _resolve)
        monkeypatch.setattr(groups, "ensure_connected", _connected)
        return client

    return _install


def _granted(request):
    return {name for name in LAYER_RIGHTS if getattr(request.admin_rights, name)}


@pytest.mark.asyncio
async def test_replace_mode_sends_exactly_the_true_flags_and_clears_the_rank(install):
    client = install(FakeClient(target=_admin({"ban_users", "delete_messages"}, rank="Mod")))

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, invite_users=True, other=False)

    assert result == f"Admin rights updated for user {USER_ID} in chat {CHAT_ID}."
    (edit,) = client.edits()
    assert _granted(edit) == {"invite_users"} and edit.rank == ""
    assert len(client.requests) == 1  # replace mode looks nothing up


@pytest.mark.asyncio
async def test_patch_changes_only_the_passed_rights(install):
    current = {"ban_users", "delete_messages", "other", "post_stories"}
    client = install(FakeClient(target=_admin(current)))

    result = await groups.edit_admin_rights(
        CHAT_ID, USER_ID, invite_users=True, delete_messages=False, mode="patch"
    )

    assert result.startswith("Admin rights patched for user 7 in Supergroup.")
    assert "Turned on: invite_users." in result and "Turned off: delete_messages." in result
    (edit,) = client.edits()
    # post_stories is not a parameter of the tool and is kept too.
    assert _granted(edit) == {"ban_users", "other", "post_stories", "invite_users"}


@pytest.mark.asyncio
async def test_patch_keeps_the_rank_unless_passed(install):
    client = install(FakeClient(target=_admin({"ban_users"}, rank="Mod")))

    kept = await groups.edit_admin_rights(CHAT_ID, USER_ID, invite_users=True, mode="patch")
    changed = await groups.edit_admin_rights(CHAT_ID, USER_ID, rank="Boss", mode="patch")

    assert "Rank" not in kept
    assert "Rank: 'Mod' -> 'Boss'." in changed and "Turned" not in changed
    first, second = client.edits()
    assert first.rank is None and second.rank == "Boss"
    assert _granted(second) == {"ban_users"}


@pytest.mark.asyncio
async def test_patch_that_changes_nothing_sends_nothing(install):
    client = install(FakeClient(target=_admin({"ban_users"}, rank="Mod")))

    result = await groups.edit_admin_rights(
        CHAT_ID, USER_ID, ban_users=True, anonymous=False, rank="Mod", mode="patch"
    )

    assert result.startswith("Nothing to change for user 7")
    assert client.edits() == []


@pytest.mark.asyncio
async def test_patch_with_nothing_passed_is_refused(install):
    client = install(FakeClient(target=_admin({"ban_users"})))

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, mode="patch")

    assert "Pass at least one right" in result
    assert client.requests == []


@pytest.mark.asyncio
async def test_patch_turning_every_right_off_points_at_demote_admin(install):
    client = install(FakeClient(target=_admin({"ban_users"})))

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, ban_users=False, mode="patch")

    assert "demote_admin" in result
    assert client.edits() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target, error, expected",
    [
        (types.ChannelParticipant(user_id=USER_ID, date=None), None, "promote_admin first"),
        (ANONYMOUS_CREATOR, None, "is the chat's creator"),
        (None, UserNotParticipantError(None), "7 is not in this chat"),
    ],
)
async def test_patch_needs_an_admin_target(install, target, error, expected):
    client = install(FakeClient(target=target, lookup_error=error))

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, ban_users=True, mode="patch")

    assert expected in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_patch_refused_by_telegram_is_reported(install):
    client = install(
        FakeClient(target=_admin({"ban_users"}), edit_error=RightForbiddenError(None))
    )

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, add_admins=True, mode="patch")

    assert "not allowed for your account" in result
    assert len(client.edits()) == 1


@pytest.mark.asyncio
async def test_patch_own_account_as_creator_turns_off_anonymous_only(install):
    client = install(FakeClient())

    result = await groups.edit_admin_rights(CHAT_ID, "me", anonymous=False, mode="patch")

    assert result.startswith("Admin rights patched for user me in Supergroup.")
    assert "Turned off: anonymous." in result and "Turned on" not in result
    (edit,) = client.edits()
    assert edit.user_id is ME and edit.rank is None
    assert _granted(edit) == set(LAYER_RIGHTS) - {"anonymous"}


@pytest.mark.asyncio
async def test_patch_own_account_as_creator_can_change_the_rank(install):
    client = install(FakeClient())

    result = await groups.edit_admin_rights(CHAT_ID, "me", rank="Boss", mode="patch")

    assert "Rank: 'Owner' -> 'Boss'." in result
    (edit,) = client.edits()
    assert _granted(edit) == set(LAYER_RIGHTS) and edit.rank == "Boss"


@pytest.mark.asyncio
async def test_patch_own_account_as_creator_refuses_other_rights(install):
    client = install(FakeClient())

    result = await groups.edit_admin_rights(
        CHAT_ID, "me", anonymous=False, ban_users=False, mode="patch"
    )

    assert "only anonymous and rank can be changed" in result and "Leave out ban_users" in result
    assert client.edits() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "me, error, expected",
    [
        (_admin({"ban_users", "add_admins"}), None, "you are not this chat's creator"),
        (None, UserNotParticipantError(None), "you are not in this chat"),
    ],
)
async def test_patch_own_account_is_refused_unless_creator(install, me, error, expected):
    client = install(FakeClient(me=me, lookup_error=error))

    result = await groups.edit_admin_rights(CHAT_ID, "me", anonymous=False, mode="patch")

    assert result.startswith("Error:") and expected in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_patch_refuses_a_basic_group(install, monkeypatch):
    client = install(FakeClient())

    async def _resolve(identifier, _client):
        if identifier == -55:
            return types.Chat(
                id=55,
                title="Basic",
                photo=types.ChatPhotoEmpty(),
                participants_count=2,
                date=None,
                version=1,
            )
        return TARGET

    monkeypatch.setattr(groups, "resolve_entity", _resolve)

    result = await groups.edit_admin_rights(-55, USER_ID, ban_users=True, mode="patch")

    assert "supergroups and channels only" in result
    assert client.requests == []


@pytest.mark.asyncio
async def test_unknown_mode_is_refused(install):
    client = install(FakeClient())

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, ban_users=True, mode="merge")

    assert "unknown mode 'merge'" in result and "replace, patch" in result
    assert client.requests == []


@pytest.mark.parametrize("value", ["merge", None, 1])
def test_mcp_arguments_refuse_an_unknown_mode(value):
    meta = func_metadata(groups.edit_admin_rights)
    with pytest.raises(ValidationError):
        meta.arg_model.model_validate({"chat_id": 1, "user_id": 2, "mode": value})


def test_mcp_arguments_leave_omitted_rights_unset():
    meta = func_metadata(groups.edit_admin_rights)
    args = meta.arg_model.model_validate({"chat_id": 1, "user_id": 2, "mode": "patch"})
    assert args.ban_users is None and args.rank is None and args.mode == "patch"


@pytest.mark.asyncio
async def test_patch_refuses_your_own_account_under_another_id(install):
    me_as_admin = types.ChannelParticipantAdmin(
        user_id=USER_ID,
        promoted_by=2,
        date=None,
        admin_rights=_rights({"ban_users"}),
        is_self=True,
    )
    client = install(FakeClient(target=me_as_admin))

    result = await groups.edit_admin_rights(CHAT_ID, USER_ID, anonymous=False, mode="patch")

    assert "your own account" in result and "not this chat's creator" in result
    assert client.edits() == []


@pytest.mark.asyncio
async def test_patch_can_clear_the_rank_and_leave_only_other(install):
    client = install(FakeClient(target=_admin({"ban_users", "other"}, rank="Mod")))

    result = await groups.edit_admin_rights(
        CHAT_ID, USER_ID, ban_users=False, rank="", mode="patch"
    )

    assert "Turned off: ban_users." in result and "Rank: 'Mod' -> none." in result
    (edit,) = client.edits()
    assert _granted(edit) == {"other"} and edit.rank == ""


@pytest.mark.asyncio
async def test_replace_treats_an_omitted_right_like_false(install):
    client = install(FakeClient())

    await groups.edit_admin_rights(CHAT_ID, USER_ID, ban_users=True)
    await groups.edit_admin_rights(CHAT_ID, USER_ID, ban_users=True, invite_users=False, rank="")

    first, second = client.edits()
    assert first.admin_rights == second.admin_rights and first.rank == second.rank
