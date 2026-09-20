"""Unit tests for TELEGRAM_ALLOWED_CHAT_IDS per-chat privacy allowlist (Issue #212)."""

import json
from types import SimpleNamespace
from typing import Optional, Union

import pytest
from telethon.tl.types import Channel, Chat, User

import main
from telegram_mcp import runtime
from telegram_mcp.runtime import (
    ALLOWED_CHAT_IDS,
    ChatAccessDeniedError,
    _parse_allowed_chat_ids,
    check_chat_access,
    get_effective_allowed_chat_ids,
    get_marked_id,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    validate_id,
)
from telegram_mcp.tools import chats, messages


class FakeDialog:
    def __init__(self, entity, unread_count=0, archived=False):
        self.entity = entity
        self.unread_count = unread_count
        self.archived = archived
        self.dialog = SimpleNamespace(
            unread_mark=False,
            notify_settings=SimpleNamespace(mute_until=None),
        )


class FakeAllowlistClient:
    """Mock client for testing chat and messaging tools under allowlist enforcement."""

    def __init__(self, dialogs=None, messages_list=None):
        self._dialogs = dialogs or []
        self._messages = messages_list or []

    async def get_dialogs(self, *args, **kwargs):
        return list(self._dialogs)

    async def get_messages(self, entity, limit=20, add_offset=0, search=None):
        if search:
            return list(self._messages)
        return list(self._messages[add_offset : add_offset + limit])

    async def get_participants(self, entity, limit=0):
        return SimpleNamespace(total=10)

    async def send_message(self, entity, message, **kwargs):
        return SimpleNamespace(
            id=101, message=message, date=SimpleNamespace(isoformat=lambda: "2026-09-20T12:00:00Z")
        )

    async def __call__(self, request):
        return SimpleNamespace(
            chats=[], users=[], dialogs=[SimpleNamespace(unread_count=0, folder_id=0)]
        )


# ==============================================================================
# 1. Parsing & Configuration Tests
# ==============================================================================


def test_parse_allowed_chat_ids_none_and_empty():
    assert _parse_allowed_chat_ids(None) is None
    assert _parse_allowed_chat_ids("") is None
    assert _parse_allowed_chat_ids("   ") is None
    assert _parse_allowed_chat_ids(" , ,  ") is None


def test_parse_allowed_chat_ids_integers_and_marked_variants():
    # User ID: 12345678 -> also indexes supergroup (-1000000012345678) and group (-12345678)
    parsed = _parse_allowed_chat_ids("12345678, -100123456789")
    assert parsed is not None
    assert 12345678 in parsed
    assert -100123456789 in parsed
    # Unmarked variant of supergroup is 123456789
    assert 123456789 in parsed
    # Variants of 12345678
    assert (-1000000000000 - 12345678) in parsed
    assert -12345678 in parsed


def test_parse_allowed_chat_ids_usernames_and_handles():
    parsed = _parse_allowed_chat_ids("@my_team, public_channel, 99999")
    assert parsed is not None
    assert "my_team" in parsed
    assert "public_channel" in parsed
    assert 99999 in parsed
    assert "unrelated" not in parsed


def test_get_effective_allowed_chat_ids_precedence(monkeypatch):
    # Unset env
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)
    assert not is_chat_allowlist_enabled()
    assert get_effective_allowed_chat_ids() is None

    # Set via monkeypatching runtime attribute
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", {111, 222})
    assert is_chat_allowlist_enabled()
    assert get_effective_allowed_chat_ids() == {111, 222}

    # Environment variable overrides
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "333,444")
    effective = get_effective_allowed_chat_ids()
    assert effective is not None
    assert 333 in effective
    assert 444 in effective


# ==============================================================================
# 2. is_chat_allowed & check_chat_access Unit Tests
# ==============================================================================


def test_is_chat_allowed_when_disabled(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)

    assert is_chat_allowed(12345) is True
    assert is_chat_allowed(-100999999999) is True
    assert is_chat_allowed("any_username") is True
    assert check_chat_access(12345) is None


def test_is_chat_allowed_when_enabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345678, -100123456789, allowed_handle")

    # Allowed cases
    assert is_chat_allowed(12345678) is True
    assert is_chat_allowed("12345678") is True
    assert is_chat_allowed(-100123456789) is True
    assert is_chat_allowed(123456789) is True  # Unmarked variant
    assert is_chat_allowed("@allowed_handle") is True
    assert is_chat_allowed("allowed_handle") is True
    assert check_chat_access(12345678) is None

    # Disallowed cases
    assert is_chat_allowed(99999999) is False
    assert is_chat_allowed(-100999999999) is False
    assert is_chat_allowed("secret_group") is False
    err = check_chat_access(99999999)
    assert err is not None
    assert "restricted by privacy policy" in err
    assert "99999999" in err


def test_is_chat_allowed_with_telethon_entities(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "1001, -1002002")

    allowed_channel = SimpleNamespace(id=2002, title="Allowed Channel", username="allowed_ch")
    # Simulate Channel type for get_marked_id
    monkeypatch.setattr(runtime, "get_marked_id", lambda e: -1002002 if e.id == 2002 else -1009999)

    assert is_chat_allowed(None, allowed_channel) is True
    assert is_chat_allowed(999999, allowed_channel) is True

    forbidden_channel = SimpleNamespace(id=9999, title="Secret Channel", username="secret_ch")
    assert is_chat_allowed(999999, forbidden_channel) is False


# ==============================================================================
# 3. validate_id Decorator Enforcement Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_validate_id_blocks_unallowed_numeric_chat_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345678, -100123456789")

    @validate_id("chat_id")
    async def sample_tool(chat_id: Union[int, str], account: Optional[str] = None):
        return f"success:{chat_id}"

    # Allowed ID passes through
    res_allowed = await sample_tool(chat_id=12345678)
    assert res_allowed == "success:12345678"

    # Disallowed ID is blocked immediately
    res_blocked = await sample_tool(chat_id=99999999)
    assert "restricted by privacy policy" in res_blocked
    assert "99999999" in res_blocked


@pytest.mark.asyncio
async def test_validate_id_blocks_batch_from_and_to_chats(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100111111111")

    @validate_id("from_chat_id", "to_chat_id")
    async def sample_forward(from_chat_id: int, to_chat_id: int):
        return "forwarded"

    # Both allowed
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100111111111, -100222222222")
    res_ok = await sample_forward(from_chat_id=-100111111111, to_chat_id=-100222222222)
    assert res_ok == "forwarded"

    # to_chat_id disallowed
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100111111111")
    res_blocked = await sample_forward(from_chat_id=-100111111111, to_chat_id=-100999999999)
    assert "restricted by privacy policy" in res_blocked
    assert "-100999999999" in res_blocked


# ==============================================================================
# 4. list_chats & get_chats Filtering Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_list_chats_filters_out_unallowed_chats(monkeypatch):
    allowed_entity = SimpleNamespace(id=111, title="Public Dev", username="dev_chat")
    secret_entity = SimpleNamespace(id=222, title="Private CEO", username="ceo_chat")

    d1 = FakeDialog(allowed_entity, unread_count=2)
    d2 = FakeDialog(secret_entity, unread_count=5)
    client = FakeAllowlistClient(dialogs=[d1, d2])

    async def _fake_connected(cl):
        pass

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "ensure_connected", _fake_connected)
    monkeypatch.setattr(chats, "get_marked_id", lambda e: -100111 if e.id == 111 else -100222)
    monkeypatch.setattr(chats, "get_entity_type", lambda e: "Channel")
    monkeypatch.setattr(chats, "get_entity_filter_type", lambda e: "channel")

    # When allowlist is set to only allowed_entity (-100111)
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100111")

    result = await chats.list_chats(account=None)
    parsed = json.loads(result).get("results", [])

    assert len(parsed) == 1
    assert parsed[0]["chat_id"] == -100111
    assert parsed[0]["title"] == "Public Dev"
    # Secret chat (-100222) must never be present
    assert not any(c.get("chat_id") == -100222 for c in parsed)


@pytest.mark.asyncio
async def test_get_chats_filters_dialogs_before_pagination(monkeypatch):
    allowed_entity = SimpleNamespace(id=111, title="Tech Support")
    forbidden_entity = SimpleNamespace(id=999, title="Confidential")

    d1 = FakeDialog(forbidden_entity)
    d2 = FakeDialog(allowed_entity)
    client = FakeAllowlistClient(dialogs=[d1, d2])

    async def _fake_connected(cl):
        pass

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "ensure_connected", _fake_connected)
    monkeypatch.setattr(chats, "get_marked_id", lambda e: 111 if e.id == 111 else 999)

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")

    result = await chats.get_chats(page=1, page_size=10, account=None)
    parsed = json.loads(result).get("results", [])

    assert len(parsed) == 1
    assert parsed[0]["chat_id"] == 111
    assert parsed[0]["title"] == "Tech Support"


# ==============================================================================
# 5. get_chat & get_full_chat Privacy Guard Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_get_chat_blocks_unallowed_chat(monkeypatch):
    forbidden_entity = SimpleNamespace(id=999, title="Top Secret", username=None)
    client = FakeAllowlistClient()

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", lambda cid, cl: forbidden_entity)
    monkeypatch.setattr(chats, "get_marked_id", lambda e: 999)
    monkeypatch.setattr(chats, "get_entity_type", lambda e: "User")

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345")

    result = await chats.get_chat(chat_id=999, account=None)
    assert "restricted by privacy policy" in result
    assert "999" in result


@pytest.mark.asyncio
async def test_get_full_chat_blocks_unallowed_chat(monkeypatch):
    forbidden_entity = SimpleNamespace(id=888, title="Secret Group", username="secret")
    client = FakeAllowlistClient()

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", lambda cid, cl: forbidden_entity)
    monkeypatch.setattr(chats, "get_marked_id", lambda e: -100888)

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345")

    result = await chats.get_full_chat(chat_id=-100888, account=None)
    assert "restricted by privacy policy" in result
    assert "-100888" in result


# ==============================================================================
# 6. Messaging Tools (send_message & get_messages) Privacy Guard Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_send_message_blocks_unallowed_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123456789")

    # Sending to forbidden chat is blocked at validate_id
    res = await messages.send_message(chat_id=-100999999999, message="hello", account=None)
    assert "restricted by privacy policy" in res
    assert "-100999999999" in res


@pytest.mark.asyncio
async def test_get_messages_blocks_unallowed_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123456789")

    res = await messages.get_messages(chat_id=-100999999999, account=None)
    assert "restricted by privacy policy" in res
    assert "-100999999999" in res


@pytest.mark.asyncio
async def test_create_poll_blocks_unallowed_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123456789")

    res = await messages.create_poll(
        chat_id=-100999999999,
        question="Should we launch?",
        options=["Yes", "No"],
        account=None,
    )
    assert "restricted by privacy policy" in res
    assert "-100999999999" in res


# ==============================================================================
# 7. main.py Compatibility Alias & Sync Tests
# ==============================================================================


def test_main_compatibility_aliases(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", None)

    assert not main.is_chat_allowlist_enabled()

    monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", {12345})
    assert main.is_chat_allowlist_enabled()
    assert main.is_chat_allowed(12345)
    assert not main.is_chat_allowed(99999)


# ==============================================================================
# 8. search_global & get_drafts Privacy Guard Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_search_global_filters_unallowed_chats(monkeypatch):
    from datetime import datetime, timezone

    allowed_chat = SimpleNamespace(id=111, title="Allowed Project")
    forbidden_chat = SimpleNamespace(id=999, title="Secret Project")

    m1 = SimpleNamespace(
        id=1,
        chat_id=111,
        chat=allowed_chat,
        date=datetime.now(timezone.utc),
        message="Launch plan",
        sender=SimpleNamespace(first_name="Alice", last_name=None, title=None),
    )
    m2 = SimpleNamespace(
        id=2,
        chat_id=999,
        chat=forbidden_chat,
        date=datetime.now(timezone.utc),
        message="Secret budget",
        sender=SimpleNamespace(first_name="Bob", last_name=None, title=None),
    )

    client = FakeAllowlistClient(messages_list=[m1, m2])

    async def _fake_connected(cl):
        pass

    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "ensure_connected", _fake_connected)
    monkeypatch.setattr(messages, "get_custom_emoji_metadata", lambda msg: {})
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")

    res = await messages.search_global(query="plan", account=None)
    parsed = json.loads(res).get("results", [])

    assert len(parsed) == 1
    assert parsed[0]["chat_id"] == 111
    assert parsed[0]["text"] == "Launch plan"
    assert not any(item.get("chat_id") == 999 for item in parsed)


@pytest.mark.asyncio
async def test_get_drafts_filters_unallowed_chats(monkeypatch):
    # GetAllDraftsRequest returns an Updates object with draft info
    update_allowed = SimpleNamespace(
        peer=SimpleNamespace(user_id=111),
        draft=SimpleNamespace(
            message="Draft to allowed", date=None, no_webpage=False, reply_to=None
        ),
    )
    update_forbidden = SimpleNamespace(
        peer=SimpleNamespace(user_id=999),
        draft=SimpleNamespace(
            message="Confidential draft", date=None, no_webpage=False, reply_to=None
        ),
    )

    class FakeDraftsClient:
        async def __call__(self, req):
            return SimpleNamespace(updates=[update_allowed, update_forbidden])

    async def _fake_connected(cl):
        pass

    monkeypatch.setattr(messages, "get_client", lambda account=None: FakeDraftsClient())
    monkeypatch.setattr(messages, "ensure_connected", _fake_connected)
    monkeypatch.setattr(messages, "get_custom_emoji_metadata", lambda d: {})
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "111")

    res = await messages.get_drafts(account=None)
    parsed = json.loads(res).get("drafts", [])

    assert len(parsed) == 1
    assert parsed[0]["peer_id"] == 111
    assert parsed[0]["message"] == "Draft to allowed"
    assert not any(d.get("peer_id") == 999 for d in parsed)
