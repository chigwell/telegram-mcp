"""Drafts in forum topics carry the topic in reply_to.top_msg_id (#396)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from telethon.tl import functions, types

from telegram_mcp.tools import messages

DATE = datetime(2026, 10, 5, tzinfo=timezone.utc)
CHANNEL_ID = 1234567890
TOPIC = 3173


def _topic_msg(msg_id, top_id=TOPIC):
    """A message inside a topic, replying to another message of that topic."""
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message=f"m{msg_id}",
        reply_to=types.MessageReplyHeader(
            forum_topic=True, reply_to_msg_id=msg_id - 1, reply_to_top_id=top_id
        ),
    )


def _general_msg(msg_id):
    return types.Message(
        id=msg_id, peer_id=types.PeerChannel(CHANNEL_ID), date=DATE, message="g", reply_to=None
    )


def _plain_reply_msg(msg_id):
    """A reply in a supergroup without topics: a reply header, but no forum_topic."""
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message="r",
        reply_to=types.MessageReplyHeader(reply_to_msg_id=msg_id - 1),
    )


def _client(monkeypatch, peer):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_input_entity", AsyncMock(return_value=peer))
    return cl


@pytest.fixture
def forum_client(monkeypatch):
    return _client(monkeypatch, types.InputPeerChannel(CHANNEL_ID, 0))


def _reply_to(cl):
    req = cl.await_args.args[0]
    assert isinstance(req, functions.messages.SaveDraftRequest)
    return req.reply_to


@pytest.mark.asyncio
async def test_reply_in_a_topic_saves_the_draft_in_that_topic(forum_client):
    forum_client.get_messages.return_value = _topic_msg(11)

    await messages.save_draft(-1001234567890, "hi", reply_to_msg_id=11, account="test")

    reply_to = _reply_to(forum_client)
    assert reply_to.reply_to_msg_id == 11
    assert reply_to.top_msg_id == TOPIC


@pytest.mark.asyncio
async def test_reply_to_the_topic_id_saves_the_draft_in_that_topic(forum_client):
    """send_message's way into a topic: reply to the topic's creation message."""
    forum_client.get_messages.return_value = types.MessageService(
        id=TOPIC,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        action=types.MessageActionTopicCreate(title="Topic A", icon_color=0),
    )

    await messages.save_draft(-1001234567890, "hi", reply_to_msg_id=TOPIC, account="test")

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (TOPIC, TOPIC)


@pytest.mark.asyncio
async def test_topic_id_alone_saves_the_draft_in_the_topic(forum_client):
    await messages.save_draft(-1001234567890, "hi", topic_id=TOPIC, account="test")

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (TOPIC, TOPIC)
    forum_client.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_topic_id_with_a_reply_needs_no_lookup(forum_client):
    await messages.save_draft(
        -1001234567890, "hi", reply_to_msg_id=11, topic_id=str(TOPIC), account="test"
    )

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (11, TOPIC)
    forum_client.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_in_general_carries_no_topic(forum_client):
    forum_client.get_messages.return_value = _general_msg(12)

    await messages.save_draft(-1001234567890, "hi", reply_to_msg_id=12, account="test")

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (12, None)


@pytest.mark.asyncio
async def test_general_topic_id_saves_the_draft_in_general(forum_client):
    await messages.save_draft(-1001234567890, "hi", topic_id=1, account="test")

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (1, 1)


@pytest.mark.asyncio
async def test_general_topic_id_with_a_reply_keeps_the_reply(forum_client):
    await messages.save_draft(-1001234567890, "hi", reply_to_msg_id=12, topic_id=1, account="test")

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (12, 1)
    forum_client.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_in_a_group_without_topics_is_unchanged(forum_client):
    forum_client.get_messages.return_value = _plain_reply_msg(11)

    await messages.save_draft(-1001234567890, "hi", reply_to_msg_id=11, account="test")

    reply_to = _reply_to(forum_client)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (11, None)


@pytest.mark.asyncio
async def test_reply_in_a_private_chat_makes_no_extra_request(monkeypatch):
    cl = _client(monkeypatch, types.InputPeerUser(42, 0))

    await messages.save_draft(42, "hi", reply_to_msg_id=5, account="test")

    reply_to = _reply_to(cl)
    assert (reply_to.reply_to_msg_id, reply_to.top_msg_id) == (5, None)
    cl.get_entity.assert_not_awaited()
    cl.get_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -3, "x"])
async def test_bad_topic_id_is_rejected_without_a_request(forum_client, bad):
    result = await messages.save_draft(-1001234567890, "hi", topic_id=bad, account="test")

    assert result == "Error: topic_id must be a positive integer."
    forum_client.assert_not_awaited()
