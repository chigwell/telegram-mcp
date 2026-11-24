"""Explicit topic_id for the read tools: get_messages, list_messages, search_messages.

Server-side threads via reply_to where Telethon supports it (plain reads);
client-side topic filtering where Telegram search ignores reply_to.
"""

import json
from datetime import datetime, timezone

import pytest
from telethon.tl import types

from telegram_mcp import runtime
from telegram_mcp.tools import messages

DATE = datetime(2026, 10, 5, tzinfo=timezone.utc)
CHANNEL_ID = 1234567890
CHAT = -1001234567890
TOPIC = 3173
OTHER = 4242
GENERAL = 1


def _msg(msg_id, reply_to_msg_id, top_id=None):
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message=f"m{msg_id}",
        reply_to=types.MessageReplyHeader(
            forum_topic=True, reply_to_msg_id=reply_to_msg_id, reply_to_top_id=top_id
        ),
    )


def _general_msg(msg_id):
    """A message in General: no topic header."""
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message=f"m{msg_id}",
        reply_to=None,
    )


def _forum():
    return types.Channel(
        id=CHANNEL_ID, title="Forum", photo=types.ChatPhotoEmpty(), date=DATE, forum=True
    )


@pytest.fixture(autouse=True)
def _clean_policy(monkeypatch):
    for name in ("TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_BLOCKED_CHAT_IDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)


class _Client:
    """Records history-read kwargs; emulates Telegram's reply_to threading."""

    def __init__(self, msgs):
        self.msgs = list(msgs)
        self.get_kwargs = None
        self.iter_kwargs = None

    async def get_messages(self, entity, **kwargs):
        self.get_kwargs = kwargs
        if kwargs.get("reply_to") is not None:
            return [
                m for m in self.msgs if messages.message_topic_id(m, True) == kwargs["reply_to"]
            ]
        return list(self.msgs)

    def iter_messages(self, entity, **kwargs):
        self.iter_kwargs = kwargs

        async def gen():
            for m in self.msgs:
                yield m

        return gen()

    async def __call__(self, request):
        return types.messages.ForumTopics(
            count=0, topics=[], messages=[], chats=[], users=[], pts=0
        )


@pytest.fixture
def client(monkeypatch):
    cl = _Client([_msg(11, 10, TOPIC), _msg(10, TOPIC), _msg(9, 9, OTHER), _general_msg(12)])
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        messages,
        "resolve_entity",
        _AsyncMock(return_value=_forum()),
    )
    monkeypatch.setattr(messages, "ensure_connected", _AsyncMock())
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", _AsyncMock())
    return cl


class _AsyncMock:
    def __init__(self, return_value=None):
        self.return_value = return_value

    async def __call__(self, *args, **kwargs):
        return self.return_value


def _ids(result):
    return [r["id"] for r in json.loads(result)["results"]]


@pytest.mark.asyncio
async def test_get_messages_reads_topic_thread_server_side(client):
    await messages.get_messages(chat_id=CHAT, topic_id=TOPIC)

    assert client.get_kwargs == {"limit": 20, "add_offset": 0, "reply_to": TOPIC}


@pytest.mark.asyncio
async def test_get_messages_topic_id_string_is_accepted(client):
    await messages.get_messages(chat_id=CHAT, topic_id=str(TOPIC))

    assert client.get_kwargs["reply_to"] == TOPIC


@pytest.mark.asyncio
async def test_list_messages_without_search_reads_thread_server_side(client):
    result = await messages.list_messages(chat_id=CHAT, limit=50, topic_id=TOPIC)

    assert client.get_kwargs == {"limit": 50, "reply_to": TOPIC}
    assert _ids(result) == [11, 10]


@pytest.mark.asyncio
async def test_list_messages_search_filters_to_topic_client_side(client):
    result = await messages.list_messages(chat_id=CHAT, search_query="m", topic_id=TOPIC)

    assert client.iter_kwargs == {"search": "m"}
    assert _ids(result) == [11, 10]


@pytest.mark.asyncio
async def test_search_messages_filters_to_topic_client_side(client):
    result = await messages.search_messages(chat_id=CHAT, query="m", topic_id=TOPIC)

    assert client.iter_kwargs == {"search": "m"}
    assert _ids(result) == [11, 10]


@pytest.mark.asyncio
async def test_search_messages_outside_allowlist_is_refused(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{OTHER}")

    result = await messages.search_messages(chat_id=CHAT, query="m", topic_id=TOPIC)

    assert "Forum topic" in result and str(TOPIC) in result
    assert client.iter_kwargs is None


@pytest.mark.asyncio
async def test_explicit_topic_inside_allowlist_wins(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{TOPIC}")

    result = await messages.list_messages(chat_id=CHAT, limit=50, topic_id=TOPIC)

    assert client.get_kwargs == {"limit": 50, "reply_to": TOPIC}
    assert _ids(result) == [11, 10]


@pytest.mark.asyncio
async def test_general_topic_is_filtered_client_side(client):
    result = await messages.list_messages(chat_id=CHAT, limit=50, topic_id=GENERAL)

    assert client.get_kwargs == {"limit": 50}
    assert _ids(result) == [12]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_messages", "list_messages", "search_messages"])
@pytest.mark.parametrize("bad", ["general", 0, -5])
async def test_invalid_topic_id_is_rejected(client, tool, bad):
    kwargs = {"chat_id": CHAT, "topic_id": bad}
    if tool == "search_messages":
        kwargs["query"] = "m"

    result = await getattr(messages, tool)(**kwargs)

    assert result == "Error: topic_id must be a positive integer."
    assert client.get_kwargs is None and client.iter_kwargs is None
