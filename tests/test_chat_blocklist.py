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

    async def resolve(getter, identifier, client, label, try_marked=True):
        return User(id=42, username="hidden")

    monkeypatch.setattr(runtime, "_resolve_with_retries", resolve)
    called = False

    @runtime.validate_id("chat_id")
    async def tool(chat_id, account=None):
        nonlocal called
        await runtime.resolve_entity(chat_id, object())
        called = True
        return "ok"

    with pytest.raises(runtime.ChatAccessDeniedError):
        await tool(chat_id=target)
    assert not called


@pytest.mark.asyncio
async def test_list_chats_hides_blocked_username_for_each_account(monkeypatch):
    from telegram_mcp.tools import chats

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@hidden")
    entities = [User(id=1, username="hidden"), User(id=2, username="visible")]
    dialogs = [SimpleNamespace(entity=e, unread_count=0, archived=False) for e in entities]

    class Client:
        async def get_dialogs(self, *args, **kwargs):
            return dialogs

        async def iter_dialogs(self, *args, **kwargs):
            for dialog in dialogs:
                yield dialog

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
async def test_positive_id_resolving_to_blocked_channel(monkeypatch):
    from telethon.tl.types import Channel

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "-1000000000042")
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    async def resolve(getter, identifier, client, label, try_marked=True):
        return Channel(id=42, title="Blocked", photo=None, date=None)

    monkeypatch.setattr(runtime, "_resolve_with_retries", resolve)

    @runtime.validate_id("chat_id")
    async def tool(chat_id, account=None):
        return await runtime.resolve_entity(chat_id, object())

    with pytest.raises(runtime.ChatAccessDeniedError):
        await tool(chat_id=42)
    with pytest.raises(runtime.ChatAccessDeniedError):
        await tool(42)


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


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["join_chat_by_link", "import_chat_invite"])
async def test_invite_does_not_expose_blocked_chat(monkeypatch, tool_name):
    from telethon.tl import functions, types
    from telegram_mcp.tools import groups

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "-1000000000042")
    chat = types.Channel(id=42, title="Hidden", photo=types.ChatPhotoEmpty(), date=None)
    requests = []

    class Client:
        async def __call__(self, request):
            requests.append(request)
            return SimpleNamespace(chat=chat)

    async def connected(client):
        pass

    monkeypatch.setattr(groups, "get_client", lambda account=None: Client())
    monkeypatch.setattr(groups, "ensure_connected", connected)
    tool = getattr(groups, tool_name)
    result = await tool(
        **({"link": "+hash"} if tool_name == "join_chat_by_link" else {"hash": "hash"})
    )
    assert "Hidden" not in result
    assert not any(isinstance(r, functions.messages.ImportChatInviteRequest) for r in requests)


@pytest.mark.asyncio
async def test_list_chats_fetches_past_blocked_dialogs(monkeypatch):
    from telegram_mcp.tools import chats

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "1,2")
    dialogs = [
        SimpleNamespace(entity=User(id=i, first_name=f"user{i}"), unread_count=0)
        for i in (1, 2, 3, 4)
    ]
    visited = []

    class Client:
        async def iter_dialogs(self, **kwargs):
            for dialog in dialogs:
                visited.append(dialog.entity.id)
                yield dialog

    async def connected(client):
        pass

    monkeypatch.setattr(chats, "get_client", lambda account=None: Client())
    monkeypatch.setattr(chats, "ensure_connected", connected)
    result = json.loads(await chats.list_chats(limit=1))
    assert [r["chat_id"] for r in result["results"]] == [3]
    assert visited == [1, 2, 3]


@pytest.mark.asyncio
async def test_blocklist_filters_reads_but_does_not_block_actions(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "42")
    actions = []

    @runtime.with_account(readonly=True)
    @runtime.validate_id("chat_id")
    async def read(chat_id, account=None):
        return "secret"

    @runtime.with_account(readonly=False)
    @runtime.validate_id("chat_id")
    async def write(chat_id, account=None):
        actions.append(chat_id)
        return "done"

    assert "secret" not in await read(chat_id=42)
    assert "omitted" in await write(chat_id=42)
    assert actions == [42]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["forward_message", "forward_messages"])
async def test_forward_from_blocked_chat_is_refused(monkeypatch, tool_name):
    from telegram_mcp.tools import messages

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "@source")
    calls = []

    class Client:
        async def forward_messages(self, *args, **kwargs):
            calls.append(args)

    async def resolve(identifier, client):
        return User(id=42, username="source") if identifier == 42 else User(id=43)

    monkeypatch.setattr(messages, "get_client", lambda account=None: Client())
    monkeypatch.setattr(messages, "resolve_entity", resolve)
    kwargs = {"from_chat_id": 42, "to_chat_id": 43}
    kwargs["message_id" if tool_name == "forward_message" else "message_ids"] = (
        1 if tool_name == "forward_message" else [1]
    )
    with pytest.raises(runtime.ToolError, match="blocked"):
        await getattr(messages, tool_name)(**kwargs)
    assert not calls


@pytest.mark.asyncio
async def test_download_media_from_blocked_chat_is_refused(monkeypatch):
    from telegram_mcp.tools import media

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "42")
    calls = []

    class Client:
        async def get_messages(self, *args, **kwargs):
            calls.append(args)

    monkeypatch.setattr(media, "get_client", lambda account=None: Client())
    result = await media.download_media(chat_id=42, message_id=1)
    assert "block" in result.lower()
    assert not calls


@pytest.mark.asyncio
async def test_forward_to_blocked_destination_is_allowed(monkeypatch):
    from telegram_mcp.tools import messages

    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", "43")
    calls = []

    class Client:
        async def forward_messages(self, *args, **kwargs):
            calls.append(args)

    async def resolve(identifier, client):
        return User(id=identifier)

    monkeypatch.setattr(messages, "get_client", lambda account=None: Client())
    monkeypatch.setattr(messages, "resolve_entity", resolve)
    await messages.forward_messages(from_chat_id=42, to_chat_id=43, message_ids=[1])
    assert len(calls) == 1
