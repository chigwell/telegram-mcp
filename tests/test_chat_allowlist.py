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
from telegram_mcp.tools import chats, contacts, folders, groups, messages


@pytest.fixture(autouse=True)
def _reset_chat_allowlist_global_state(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)
    if hasattr(main, "ALLOWED_CHAT_IDS"):
        monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", None)
    yield
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)
    if hasattr(main, "ALLOWED_CHAT_IDS"):
        monkeypatch.setattr(main, "ALLOWED_CHAT_IDS", None)


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
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)

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


# ==============================================================================
# 9. Chat parameters not named chat_id, and tools that list chats
# ==============================================================================


def _no_telegram(account=None):
    raise AssertionError("a call refused by the allowlist must not reach Telegram")


def _channel(channel_id, title):
    from datetime import datetime, timezone

    from telethon.tl.types import ChatPhotoEmpty

    return Channel(
        id=channel_id,
        title=title,
        photo=ChatPhotoEmpty(),
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        megagroup=True,
        access_hash=1,
    )


async def _connected(cl=None):
    return None


@pytest.mark.asyncio
async def test_get_last_interaction_blocks_unallowed_contact(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101")
    monkeypatch.setattr(contacts, "get_client", _no_telegram)

    res = await contacts.get_last_interaction(contact_id=202, account=None)

    assert "restricted by privacy policy" in res
    assert "202" in res


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, kwargs",
    [
        ("invite_to_group", {"user_ids": [101]}),
        ("promote_admin", {"user_id": 101}),
        ("demote_admin", {"user_id": 101}),
    ],
)
async def test_group_admin_tools_block_unallowed_group(monkeypatch, tool, kwargs):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101")
    monkeypatch.setattr(groups, "get_client", _no_telegram)

    res = await getattr(groups, tool)(group_id=-1005550000000, account=None, **kwargs)

    assert "restricted by privacy policy" in res
    assert "-1005550000000" in res


@pytest.mark.asyncio
async def test_create_folder_refuses_unallowed_chat_ids(monkeypatch):
    from telethon.tl.types import InputPeerUser

    class FolderClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(type(request).__name__)
            return SimpleNamespace(filters=[])

    async def fake_resolve_input(chat_id, cl=None):
        return InputPeerUser(int(chat_id), 0)

    client = FolderClient()
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101")
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    monkeypatch.setattr(folders, "resolve_input_entity", fake_resolve_input)

    res = await folders.create_folder(title="Work", chat_ids=[101, 202], account=None)

    assert "restricted by privacy policy" in res
    assert "202" in res
    assert "UpdateDialogFilterRequest" not in client.requests


@pytest.mark.asyncio
async def test_group_admin_tool_lets_an_allowlisted_group_through(monkeypatch):
    # The widened CHAT_PARAM_NAMES must not block a group that is on the allowlist.
    reached = []

    async def resolve(identifier, cl=None):
        reached.append(identifier)
        raise RuntimeError("stop here: the allowlist check has passed")

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-1005550000000")
    monkeypatch.setattr(groups, "get_client", lambda account=None: object())
    monkeypatch.setattr(groups, "resolve_entity", resolve)

    res = await groups.invite_to_group(group_id=-1005550000000, user_ids=[101], account=None)

    assert reached == [-1005550000000]
    assert "restricted by privacy policy" not in res


@pytest.mark.asyncio
@pytest.mark.parametrize("allowlist", ["", "101"], ids=["no-allowlist", "allowlist"])
async def test_create_folder_accepts_saved_messages(monkeypatch, allowlist):
    from telethon.tl.types import InputPeerSelf

    class FolderClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return SimpleNamespace(filters=[])

    async def fake_resolve_input(chat_id, cl=None):
        return InputPeerSelf()

    client = FolderClient()
    if allowlist:
        monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", allowlist)
    else:
        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    monkeypatch.setattr(folders, "resolve_input_entity", fake_resolve_input)

    res = await folders.create_folder(title="Notes", chat_ids=["me"], account=None)

    assert "restricted by privacy policy" not in res
    created = [r for r in client.requests if type(r).__name__ == "UpdateDialogFilterRequest"]
    assert len(created) == 1
    assert isinstance(created[0].filter.include_peers[0], InputPeerSelf)


@pytest.mark.asyncio
async def test_get_common_chats_hides_unallowed_chats(monkeypatch):
    allowed, forbidden = _channel(1111111111, "Allowed Team"), _channel(2222222222, "Board")

    class CommonChatsClient:
        async def __call__(self, request):
            return SimpleNamespace(chats=[allowed, forbidden])

    async def fake_resolve(identifier, cl=None):
        return User(id=101, first_name="Ivan")

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101,-1001111111111")
    monkeypatch.setattr(chats, "get_client", lambda account=None: CommonChatsClient())
    monkeypatch.setattr(chats, "ensure_connected", _connected)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    res = await chats.get_common_chats(user_id=101, account=None)

    assert "Allowed Team" in res
    assert "Board" not in res and "2222222222" not in res


@pytest.mark.asyncio
async def test_get_contact_chats_hides_unallowed_common_chats(monkeypatch):
    ivan = User(id=101, first_name="Ivan", last_name="Petrov")
    allowed, forbidden = _channel(1111111111, "Allowed Team"), _channel(2222222222, "Board")

    class ContactChatsClient:
        async def get_dialogs(self, **kwargs):
            return [SimpleNamespace(entity=ivan, unread_count=0)]

        async def get_common_chats(self, entity, **kwargs):
            return [allowed, forbidden]

    async def fake_resolve(identifier, cl=None):
        return ivan

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101,-1001111111111")
    monkeypatch.setattr(contacts, "get_client", lambda account=None: ContactChatsClient())
    monkeypatch.setattr(contacts, "resolve_entity", fake_resolve)

    res = json.loads(await contacts.get_contact_chats(contact_id=101, account=None))

    assert [r["chat_id"] for r in res["results"]] == [101, -1001111111111]


@pytest.mark.asyncio
async def test_get_direct_chat_by_contact_hides_unallowed_contacts(monkeypatch):
    ivan = User(id=101, first_name="Ivan", last_name="Petrov")
    other_ivan = User(id=202, first_name="Ivan", last_name="Sidorov")

    class DirectChatClient:
        async def __call__(self, request):
            return SimpleNamespace(users=[ivan, other_ivan])

        async def get_dialogs(self, **kwargs):
            return [
                SimpleNamespace(entity=ivan, unread_count=1),
                SimpleNamespace(entity=other_ivan, unread_count=5),
            ]

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101")
    monkeypatch.setattr(contacts, "get_client", lambda account=None: DirectChatClient())
    monkeypatch.setattr(contacts, "ensure_connected", _connected)

    res = json.loads(await contacts.get_direct_chat_by_contact(contact_query="ivan", account=None))

    assert [r["chat_id"] for r in res["results"]] == [101]
    assert "Sidorov" not in json.dumps(res)


@pytest.mark.asyncio
async def test_get_folder_hides_unallowed_chats(monkeypatch):
    from telethon.tl.types import DialogFilter, InputPeerUser, TextWithEntities

    people = {
        101: User(id=101, first_name="Ivan", last_name="Petrov"),
        202: User(id=202, first_name="Maria", last_name="Sokolova"),
    }
    folder = DialogFilter(
        id=2,
        title=TextWithEntities(text="Personal", entities=[]),
        pinned_peers=[InputPeerUser(202, 0)],
        include_peers=[InputPeerUser(101, 0), InputPeerUser(202, 0)],
        exclude_peers=[InputPeerUser(303, 0)],
    )

    class FolderClient:
        async def __call__(self, request):
            return SimpleNamespace(filters=[folder])

    async def fake_resolve(peer, cl=None):
        if peer.user_id not in people:
            raise ValueError("unknown peer")
        return people[peer.user_id]

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "101")
    monkeypatch.setattr(folders, "get_client", lambda account=None: FolderClient())
    monkeypatch.setattr(folders, "resolve_entity", fake_resolve)

    res = json.loads(await folders.get_folder(folder_id=2, account=None))

    assert [c["id"] for c in res["included_chats"]] == [101]
    assert res["pinned_chats"] == []
    assert res["excluded_chats"] == []
