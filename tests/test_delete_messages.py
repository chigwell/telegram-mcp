"""delete_message / delete_messages_bulk must only delete messages of the given chat.

Private chats and basic groups share one account-wide message counter, so Telegram's
messages.deleteMessages takes bare IDs and deletes wherever they are, for both sides.
These tests run Telethon's real get_messages/delete_messages against a fake server that
numbers messages that way.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession
from telethon.tl.types import Channel, ChatPhotoEmpty, User

from telegram_mcp.tools import messages

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
IVAN = User(id=101, first_name="Ivan", access_hash=11)
MARIA = User(id=202, first_name="Maria", access_hash=22)


class AccountServer(TelegramClient):
    """A real Telethon client whose requests are answered by an in-memory account."""

    def __init__(self, chat_of_message):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.server = {
            message_id: types.Message(
                id=message_id, peer_id=types.PeerUser(user_id), date=NOW, message="hi"
            )
            for message_id, user_id in chat_of_message.items()
        }
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        batch = request if isinstance(request, list) else [request]
        replies = [self._answer(r) for r in batch]
        return replies if isinstance(request, list) else replies[0]

    def _answer(self, request):
        self.requests.append(type(request).__name__)
        if isinstance(request, functions.messages.GetMessagesRequest):
            ids = [getattr(i, "id", i) for i in request.id]
            found = [self.server.get(i) or types.MessageEmpty(id=i) for i in ids]
            return types.messages.Messages(messages=found, topics=[], chats=[], users=[])
        if isinstance(request, functions.messages.DeleteMessagesRequest):
            gone = [i for i in request.id if self.server.pop(i, None)]
            return types.messages.AffectedMessages(pts=1, pts_count=len(gone))
        raise AssertionError(f"unexpected request {type(request).__name__}")


@pytest.fixture
def account(monkeypatch):
    # 4519-4521 are in the chat with Maria, 4522-4523 in the chat with Ivan.
    client = AccountServer({4519: 202, 4520: 202, 4521: 202, 4522: 101, 4523: 101})
    people = {101: IVAN, 202: MARIA}

    async def fake_resolve(chat_id, cl=None):
        return people[int(chat_id)]

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "ensure_connected", connected)
    return client


@pytest.mark.asyncio
async def test_delete_message_refuses_a_message_from_another_chat(account):
    result = await messages.delete_message(chat_id=101, message_id=4521)

    assert result == "Message 4521 is not in chat 101; nothing was deleted."
    assert 4521 in account.server
    assert "DeleteMessagesRequest" not in account.requests


@pytest.mark.asyncio
async def test_delete_message_deletes_a_message_of_the_chat(account):
    result = await messages.delete_message(chat_id=101, message_id=4523)

    assert result == "Message 4523 deleted."
    assert 4523 not in account.server
    assert set(account.server) == {4519, 4520, 4521, 4522}


@pytest.mark.asyncio
async def test_delete_messages_bulk_refuses_when_any_id_is_from_another_chat(account):
    result = await messages.delete_messages_bulk(chat_id=101, message_ids=[4523, 4519, 4520])

    assert result == "Message(s) 4519, 4520 are not in chat 101; nothing was deleted."
    assert set(account.server) == {4519, 4520, 4521, 4522, 4523}
    assert "DeleteMessagesRequest" not in account.requests


@pytest.mark.asyncio
async def test_delete_messages_bulk_deletes_messages_of_the_chat(account):
    result = await messages.delete_messages_bulk(chat_id=101, message_ids=[4522, 4523])

    assert result == "Deleted 2 of 2 messages from chat 101."
    assert set(account.server) == {4519, 4520, 4521}


@pytest.mark.asyncio
async def test_delete_message_in_a_channel_skips_the_ownership_lookup(monkeypatch):
    channel = Channel(
        id=1555000000, title="Team", photo=ChatPhotoEmpty(), date=NOW, megagroup=True
    )

    class ChannelClient:
        def __init__(self):
            self.deleted = None

        async def get_messages(self, *args, **kwargs):
            raise AssertionError("channel deletes are already scoped by Telegram")

        async def delete_messages(self, entity, message_ids):
            self.deleted = (entity, message_ids)

    async def fake_resolve(chat_id, cl=None):
        return channel

    async def connected(cl=None):
        return None

    client = ChannelClient()
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "ensure_connected", connected)

    result = await messages.delete_message(chat_id=-1001555000000, message_id=7)

    assert result == "Message 7 deleted."
    assert client.deleted == (channel, 7)


@pytest.mark.asyncio
async def test_delete_messages_bulk_in_a_channel_skips_the_ownership_lookup(monkeypatch):
    channel = Channel(
        id=1555000000, title="Team", photo=ChatPhotoEmpty(), date=NOW, megagroup=True
    )

    class ChannelClient:
        def __init__(self):
            self.requests = []

        async def get_messages(self, *args, **kwargs):
            raise AssertionError("channel deletes are already scoped by Telegram")

        async def __call__(self, request):
            self.requests.append(request)
            return SimpleNamespace(pts_count=len(request.id))

    async def fake_resolve(chat_id, cl=None):
        return channel

    async def connected(cl=None):
        return None

    client = ChannelClient()
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "ensure_connected", connected)

    result = await messages.delete_messages_bulk(chat_id=-1001555000000, message_ids=[7, 8])

    assert result == "Deleted 2 of 2 messages from chat -1001555000000."
    assert isinstance(client.requests[0], functions.channels.DeleteMessagesRequest)
