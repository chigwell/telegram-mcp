import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telethon.errors import BotMethodInvalidError
from telethon.tl.types import User

from telegram_mcp import runtime
from telegram_mcp.tools import chats, contacts, groups


class FakeBotClient:
    """Mock Telethon client simulating a bot account token where get_dialogs is forbidden."""

    def __init__(self, invoke_result=None):
        self.get_dialogs_called = False
        self.invoke_result = invoke_result

    async def __call__(self, *args, **kwargs):
        if callable(self.invoke_result):
            return await self.invoke_result(*args, **kwargs)
        return self.invoke_result

    async def get_dialogs(self, *args, **kwargs):
        self.get_dialogs_called = True
        raise BotMethodInvalidError(request=None)


def _async_return(val):
    async def _fn(*args, **kwargs):
        return val

    return _fn


@pytest.mark.asyncio
async def test_get_chats_bot_method_invalid_error_fallback(monkeypatch):
    """Verifies that get_chats handles BotMethodInvalidError cleanly without unhandled crash."""
    client = FakeBotClient()
    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "ensure_connected", _async_return(None))

    result = await chats.get_chats(page=1, page_size=10, account=None)

    assert client.get_dialogs_called is True
    assert "Listing chats" in result or "bot accounts" in result
    assert "GEN-ERR" not in result
    assert "Traceback" not in result


@pytest.mark.asyncio
async def test_list_chats_bot_method_invalid_error_fallback(monkeypatch):
    """Verifies that list_chats handles BotMethodInvalidError cleanly without unhandled crash."""
    client = FakeBotClient()
    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "ensure_connected", _async_return(None))

    result = await chats.list_chats(limit=20, account=None)

    assert client.get_dialogs_called is True
    assert "Listing chats" in result or "bot accounts" in result
    assert "GEN-ERR" not in result
    assert "Traceback" not in result


@pytest.mark.asyncio
async def test_resolve_with_retries_swallows_bot_dialog_warm_error(monkeypatch):
    """Verifies that _resolve_with_retries does not crash when get_dialogs raises BotMethodInvalidError."""
    client = FakeBotClient()
    resolved_entity = SimpleNamespace(id=12345, username="test_bot")

    call_count = 0

    async def fake_get_entity(identifier):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("Cold entity cache miss")
        return resolved_entity

    client.get_entity = fake_get_entity
    monkeypatch.setattr(runtime, "ensure_connected", _async_return(None))

    result = await runtime._resolve_with_retries("get_entity", 12345, client, "user")

    assert client.get_dialogs_called is True
    assert result is resolved_entity


@pytest.mark.asyncio
async def test_get_direct_chat_by_contact_bot_fallback(monkeypatch):
    """Verifies that get_direct_chat_by_contact handles BotMethodInvalidError without crashing."""
    contact_user = SimpleNamespace(
        id=999,
        first_name="Test",
        last_name="User",
        username="testuser",
        phone="+123456789",
    )
    mock_contacts_res = SimpleNamespace(users=[contact_user])
    client = FakeBotClient(invoke_result=mock_contacts_res)

    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "ensure_connected", _async_return(None))

    result = await contacts.get_direct_chat_by_contact(contact_query="Test", account=None)

    assert client.get_dialogs_called is True
    assert "Found contacts: Test User, but no direct chats were found" in result
    assert "GEN-ERR" not in result


@pytest.mark.asyncio
async def test_get_contact_chats_bot_fallback(monkeypatch):
    """Verifies that get_contact_chats handles BotMethodInvalidError without crashing."""
    client = FakeBotClient()
    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "ensure_connected", _async_return(None))

    contact_user = User(
        id=999,
        is_self=False,
        contact=True,
        mutual_contact=False,
        deleted=False,
        bot=False,
        bot_chat_history=False,
        bot_nochats=False,
        verified=False,
        restricted=False,
        min=False,
        bot_inline_geo=False,
        support=False,
        scam=False,
        fake=False,
        first_name="Alice",
        access_hash=123456,
    )
    monkeypatch.setattr(contacts, "resolve_entity", _async_return(contact_user))

    result = await contacts.get_contact_chats(contact_id=999, account=None)

    assert client.get_dialogs_called is True
    assert "GEN-ERR" not in result


@pytest.mark.asyncio
async def test_create_group_bot_fallback(monkeypatch):
    """Verifies that create_group handles BotMethodInvalidError when inspecting recent dialogs."""
    client = FakeBotClient(invoke_result=SimpleNamespace())
    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "ensure_connected", _async_return(None))
    monkeypatch.setattr(groups, "resolve_entity", _async_return(SimpleNamespace(id=123)))

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await groups.create_group(title="Test Bot Group", user_ids=[123], account=None)

    assert client.get_dialogs_called is True
    assert "Group created successfully" in result
    assert "GEN-ERR" not in result


@pytest.mark.asyncio
async def test_runner_warm_caches_bot_fallback(monkeypatch):
    """Verifies that runner cache warming handles bot clients raising BotMethodInvalidError."""
    import asyncio

    bot_client = FakeBotClient()
    user_dialog_called = False

    class FakeUserClient:
        async def get_dialogs(self):
            nonlocal user_dialog_called
            user_dialog_called = True
            return []

    user_client = FakeUserClient()
    clients = {"bot": bot_client, "user": user_client}

    async def _warm_client(label, cl):
        try:
            await cl.get_dialogs()
        except BotMethodInvalidError:
            pass

    await asyncio.gather(*(_warm_client(label, cl) for label, cl in clients.items()))

    assert bot_client.get_dialogs_called is True
    assert user_dialog_called is True
