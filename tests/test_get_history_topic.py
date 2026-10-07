"""get_history(topic_id=...) asks Telegram for the topic thread instead of
filtering the last `limit` messages of the whole group client-side."""

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

from telegram_mcp.tools import messages

DATE = datetime(2026, 10, 5, tzinfo=timezone.utc)
CHANNEL_ID = 1234567890
TOPIC_ID = 3173


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


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        messages,
        "resolve_entity",
        AsyncMock(
            return_value=types.Channel(
                id=CHANNEL_ID, title="Forum", photo=types.ChatPhotoEmpty(), date=DATE
            )
        ),
    )
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


@pytest.mark.asyncio
async def test_topic_is_fetched_server_side_and_keeps_nested_replies(client):
    # A top-level topic message and a reply to it (reply_to_msg_id != topic root).
    client.get_messages.return_value = [_msg(11, 10, TOPIC_ID), _msg(10, TOPIC_ID)]

    result = await messages.get_history(chat_id=-1001234567890, limit=50, topic_id=TOPIC_ID)

    assert client.get_messages.await_args.kwargs == {"limit": 50, "reply_to": TOPIC_ID}
    ids = [r["id"] for r in json.loads(result)["results"]]
    assert ids == [11, 10]


@pytest.mark.asyncio
async def test_topic_id_string_is_accepted(client):
    client.get_messages.return_value = []
    await messages.get_history(chat_id=-1001234567890, topic_id=str(TOPIC_ID))
    assert client.get_messages.await_args.kwargs["reply_to"] == TOPIC_ID


@pytest.mark.asyncio
async def test_without_topic_reads_whole_chat(client):
    client.get_messages.return_value = []
    await messages.get_history(chat_id=-1001234567890, limit=20)
    assert client.get_messages.await_args.kwargs == {"limit": 20}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["general", 0, -5])
async def test_invalid_topic_id_is_rejected(client, bad):
    result = await messages.get_history(chat_id=-1001234567890, topic_id=bad)
    assert result == "Error: topic_id must be a positive integer."
    client.get_messages.assert_not_awaited()
