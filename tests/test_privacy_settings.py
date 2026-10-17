"""set_privacy_settings adds per-user exceptions and never widens the main choice."""

import pytest
from telethon import functions, types
from telethon.tl.types import User

from telegram_mcp.tools import profile

IVAN = User(id=101, first_name="Ivan", username="ivan_k", access_hash=11)
MOM = User(id=303, first_name="Mom", access_hash=33)
IVAN_INPUT = types.InputUser(user_id=101, access_hash=11)
MOM_INPUT = types.InputUser(user_id=303, access_hash=33)


class PrivacyClient:
    """Answers account.getPrivacy with `current` and records account.setPrivacy."""

    def __init__(self, current):
        self.current = current
        self.sent = []

    async def __call__(self, request):
        if isinstance(request, functions.account.GetPrivacyRequest):
            return types.account.PrivacyRules(rules=self.current, chats=[], users=[IVAN, MOM])
        if isinstance(request, functions.account.SetPrivacyRequest):
            self.sent.append(request)
            return types.account.PrivacyRules(rules=[], chats=[], users=[])
        raise AssertionError(f"unexpected request {type(request).__name__}")


def install(monkeypatch, current):
    client = PrivacyClient(current)

    async def fake_resolve(identifier, cl=None):
        if identifier in ("@ivan_k", 101):
            return IVAN
        raise ValueError("No user has that username")

    monkeypatch.setattr(profile, "get_client", lambda account=None: client)
    monkeypatch.setattr(profile, "resolve_entity", fake_resolve)
    return client


@pytest.mark.asyncio
async def test_hiding_from_one_user_keeps_nobody(monkeypatch):
    client = install(monkeypatch, [types.PrivacyValueDisallowAll()])

    result = await profile.set_privacy_settings(key="phone", disallow_users=["@ivan_k"])

    assert result == "Privacy settings for phone updated successfully."
    assert client.sent[0].rules == [
        types.InputPrivacyValueDisallowUsers(users=[IVAN_INPUT]),
        types.InputPrivacyValueDisallowAll(),
    ]


@pytest.mark.asyncio
async def test_hiding_from_one_user_under_everybody_puts_the_exception_first(monkeypatch):
    client = install(monkeypatch, [types.PrivacyValueAllowAll()])

    await profile.set_privacy_settings(key="phone", disallow_users=["@ivan_k"])

    assert client.sent[0].rules == [
        types.InputPrivacyValueDisallowUsers(users=[IVAN_INPUT]),
        types.InputPrivacyValueAllowAll(),
    ]


@pytest.mark.asyncio
async def test_existing_exceptions_are_kept_and_a_user_moves_between_lists(monkeypatch):
    client = install(
        monkeypatch,
        [
            types.PrivacyValueAllowUsers(users=[MOM.id]),
            types.PrivacyValueDisallowUsers(users=[IVAN.id]),
            types.PrivacyValueAllowContacts(),
        ],
    )

    await profile.set_privacy_settings(key="status", allow_users=[101])

    assert client.sent[0].rules == [
        types.InputPrivacyValueAllowUsers(users=[MOM_INPUT, IVAN_INPUT]),
        types.InputPrivacyValueAllowContacts(),
    ]


@pytest.mark.asyncio
async def test_unknown_user_changes_nothing(monkeypatch):
    client = install(monkeypatch, [types.PrivacyValueDisallowAll()])

    result = await profile.set_privacy_settings(key="phone", disallow_users=["@ivn_k"])

    assert result == "Error: Could not find a requested user; privacy settings were not changed."
    assert client.sent == []


@pytest.mark.asyncio
async def test_without_users_nothing_is_sent(monkeypatch):
    client = install(monkeypatch, [types.PrivacyValueDisallowAll()])

    result = await profile.set_privacy_settings(key="phone")

    assert result.startswith("Error: Pass allow_users and/or disallow_users.")
    assert client.sent == []


@pytest.mark.asyncio
async def test_own_account_is_refused_with_a_reason(monkeypatch):
    client = install(monkeypatch, [types.PrivacyValueAllowAll()])
    me = User(id=1, first_name="Me", is_self=True, access_hash=1)

    async def resolve_me(identifier, cl=None):
        return me

    monkeypatch.setattr(profile, "resolve_entity", resolve_me)

    result = await profile.set_privacy_settings(key="phone", disallow_users=["me"])

    assert result == (
        "Error: Your own account cannot be a privacy exception. "
        "Privacy settings were not changed."
    )
    assert client.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current, reason",
    [
        (
            [types.PrivacyValueAllowUsers(users=[999])],
            "Privacy rule names a user Telegram did not return.",
        ),
        ([types.PrivacyValueAllowBots()], "Unsupported privacy rule; refusing to drop it."),
    ],
    ids=["unknown-user-in-rule", "unsupported-rule"],
)
async def test_refusals_tell_the_agent_why_nothing_changed(monkeypatch, current, reason):
    client = install(monkeypatch, current)
    if isinstance(current[0], types.PrivacyValueAllowBots):
        # make sure this build has no Input twin, otherwise the rule would be accepted
        monkeypatch.setattr(profile.types, "InputPrivacyValueAllowBots", None, raising=False)

    result = await profile.set_privacy_settings(key="phone", disallow_users=["@ivan_k"])

    assert result == f"Error: {reason} Privacy settings were not changed."
    assert client.sent == []
