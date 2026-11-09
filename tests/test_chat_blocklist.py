"""Global chat blocklist enforcement and allowlist precedence."""

import json
from types import SimpleNamespace

import pytest
from telethon.tl.types import User

from telegram_mcp import runtime


@pytest.fixture(autouse=True)
def clean_policy(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS", raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)


def test_marked_ids_and_username_take_precedence(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42,-1000000000042,@blocked")
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "-1000000000042,@Blocked")
    assert runtime.is_chat_allowed(42, User(id=42, username="someone"))
    assert not runtime.is_chat_allowed(-1000000000042)
    assert not runtime.is_chat_allowed(42, User(id=42, username="blocked"))
    assert "TELEGRAM_BLOCKED_CHAT_IDS" in runtime.check_chat_access("@BLOCKED")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocked", "target"),
    [("@hidden", 42), ("42", "@hidden")],
    ids=["numeric-target", "username-target"],
)
async def test_resolved_target_is_checked_before_call(monkeypatch, blocked, target):
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", blocked)
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    async def resolve(identifier, client):
        return User(id=42, username="hidden")

    monkeypatch.setattr(runtime, "resolve_entity", resolve)
    called = False

    @runtime.validate_id("chat_id")
    async def tool(chat_id, account=None):
        nonlocal called
        called = True
        return "ok"

    response = await tool(chat_id=target)
    assert "TELEGRAM_BLOCKED_CHAT_IDS" in response
    assert not called


@pytest.mark.asyncio
async def test_unresolved_username_check_reports_warning(monkeypatch):
    logged = []
    monkeypatch.setattr(runtime.logger, "warning", logged.append)
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@hidden")
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    async def resolve(identifier, client):
        raise ValueError("not cached")

    monkeypatch.setattr(runtime, "resolve_entity", resolve)

    @runtime.with_account(readonly=True)
    @runtime.validate_id("chat_id")
    async def tool(chat_id, account=None):
        return json.dumps({"chat_id": chat_id})

    result = await tool(chat_id=42)
    response = json.loads(result.content[0].text)
    assert response["chat_id"] == 42
    assert "blocked username could not be checked" in result.content[1].text
    assert result.structuredContent == {"result": result.content[0].text}
    metadata = runtime.mcp._tool_manager.get_tool("get_messages").fn_metadata
    assert metadata.convert_result(result) == result
    assert "blocked username could not be checked" in logged[0]


@pytest.mark.asyncio
async def test_list_chats_hides_blocked_username_for_each_account(monkeypatch):
    from telegram_mcp.tools import chats

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@hidden")
    entities = [User(id=1, username="hidden"), User(id=2, username="visible")]
    dialogs = [SimpleNamespace(entity=e, unread_count=0, archived=False) for e in entities]

    class Client:
        async def get_dialogs(self, *args, **kwargs):
            return dialogs

    async def connected(client):
        pass

    monkeypatch.setattr(chats, "get_client", lambda account=None: Client())
    monkeypatch.setattr(chats, "ensure_connected", connected)
    for account in ("work", "personal"):
        result = await chats.list_chats(account=account)
        assert "hidden" not in result
        assert "visible" in result


@pytest.mark.asyncio
async def test_event_for_blocked_chat_is_not_recorded(monkeypatch):
    from collections import deque
    from telegram_mcp.tools import events

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@hidden")
    monkeypatch.setattr(events, "_event_log", deque(maxlen=20))
    chat = User(id=42, username="hidden")

    async def get_chat():
        return chat

    event = SimpleNamespace(
        chat_id=42,
        chat=chat,
        is_private=True,
        is_group=False,
        is_channel=False,
        sender=chat,
        get_chat=get_chat,
        message=SimpleNamespace(id=1, out=False, sender_id=42, mentioned=False),
    )
    await events._on_message_event(event, account="work")
    assert not events._event_log
    monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS")
    await events._on_message_event(event, account="work")
    assert len(events._event_log) == 1


@pytest.mark.asyncio
async def test_retry_resolves_blocked_entity_before_action(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "42")
    client = object()
    monkeypatch.setattr(runtime, "get_client", lambda account=None: client)
    attempts = 0

    async def resolve_with_retries(getter, identifier, cl, label, try_marked=True):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("temporarily unavailable")
        return User(id=42, username="hidden")

    monkeypatch.setattr(runtime, "_resolve_with_retries", resolve_with_retries)
    called = False

    @runtime.validate_id("chat_id")
    async def tool(chat_id, account=None):
        nonlocal called
        await runtime.resolve_entity(chat_id, client)
        called = True

    with pytest.raises(runtime.ChatAccessDeniedError):
        await tool(chat_id="@hidden")
    assert not called


@pytest.mark.asyncio
async def test_positive_id_resolving_to_blocked_channel(monkeypatch):
    from telethon.tl.types import Channel

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "-1000000000042")
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    async def resolve(identifier, client):
        return Channel(id=42, title="Blocked", photo=None, date=None)

    monkeypatch.setattr(runtime, "resolve_entity", resolve)

    @runtime.validate_id("chat_id")
    async def tool(chat_id, account=None):
        return "should not run"

    assert "TELEGRAM_BLOCKED_CHAT_IDS" in await tool(chat_id=42)
    assert "TELEGRAM_BLOCKED_CHAT_IDS" in await tool(42)


@pytest.mark.asyncio
async def test_contacts_and_aliases_exclude_blocked_user(monkeypatch):
    from telegram_mcp.tools import contacts

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "42,@hidden")
    users = [
        User(id=42, username="hidden", first_name="Hidden"),
        User(id=43, username="visible", first_name="Visible"),
    ]

    class Client:
        async def __call__(self, request):
            return SimpleNamespace(users=users)

    async def connected(client):
        pass

    async def resolve(identifier, client):
        return users[0] if identifier == 42 else users[1]

    monkeypatch.setattr(contacts, "get_client", lambda account=None: Client())
    monkeypatch.setattr(contacts, "ensure_connected", connected)
    monkeypatch.setattr(contacts, "resolve_entity", resolve)
    monkeypatch.setattr(contacts, "match_aliases", lambda query: [("hidden", {"id": 42})])
    for result in (await contacts.list_contacts(), await contacts.search_contacts(query="x")):
        assert "Hidden" not in result
        assert "hidden" not in result
        assert "Visible" in result


def test_input_peer_is_compared_as_marked_id(monkeypatch):
    from telethon.tl.types import InputPeerChannel

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "-1000000000042")
    assert runtime.is_chat_blocked("@channel", InputPeerChannel(channel_id=42, access_hash=1))


@pytest.mark.asyncio
async def test_folder_snapshot_explains_blocklist(monkeypatch):
    from telegram_mcp.tools import folders

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "42")
    assert "blocklist" in await folders.get_folder_snapshot(account="default")
