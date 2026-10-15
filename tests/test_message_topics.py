"""Messages of forum topics carry `topic`; titles come once per response."""

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

from telegram_mcp.tools import messages

DATE = datetime(2026, 10, 5, tzinfo=timezone.utc)
CHANNEL_ID = 1234567890
CHAT = -1001234567890


def _msg(msg_id, header=None, action=None):
    return types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message=f"m{msg_id}",
        reply_to=header,
        action=action,
    )


def _in_topic(msg_id, topic):
    """A plain message in a topic: reply_to_msg_id is the topic's id."""
    return _msg(msg_id, types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic))


def _reply_in_topic(msg_id, replied, topic):
    return _msg(
        msg_id,
        types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=replied, reply_to_top_id=topic),
    )


def _channel(forum):
    return types.Channel(
        id=CHANNEL_ID,
        title="Forum",
        photo=types.ChatPhotoEmpty(),
        date=DATE,
        megagroup=True,
        forum=forum,
    )


def _topics_result(titles):
    return types.messages.ForumTopics(
        count=len(titles),
        topics=[
            types.ForumTopic(
                id=i,
                date=DATE,
                peer=types.PeerChannel(CHANNEL_ID),
                title=title,
                icon_color=0,
                top_message=i,
                read_inbox_max_id=0,
                read_outbox_max_id=0,
                unread_count=0,
                unread_mentions_count=0,
                unread_reactions_count=0,
                unread_poll_votes_count=0,
                from_id=types.PeerUser(1),
                notify_settings=types.PeerNotifySettings(),
            )
            for i, title in titles.items()
        ],
        messages=[],
        chats=[],
        users=[],
        pts=0,
    )


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(messages, "_topic_titles", {})
    monkeypatch.setattr(messages, "_topic_misses", {})
    monkeypatch.setattr(messages, "_topic_actions_seen", {})


def _client(monkeypatch, entity, titles=None):
    cl = AsyncMock()
    cl.side_effect = lambda request: _topics_result(
        {i: titles[i] for i in request.topics if i in titles}
    )
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=entity))
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


def test_plain_message_in_topic_has_topic_and_no_reply():
    rec = messages.message_to_dict(_in_topic(5, 3), CHAT, forum=True)
    assert rec["topic"] == 3
    assert "reply_to" not in rec


def test_reply_inside_topic_keeps_reply_and_topic():
    rec = messages.message_to_dict(_reply_in_topic(9, 5, 3), CHAT, forum=True)
    assert rec["topic"] == 3
    assert rec["reply_to"] == 5


def test_reply_to_the_topic_root_is_not_a_reply():
    rec = messages.message_to_dict(_reply_in_topic(9, 3, 3), CHAT, forum=True)
    assert rec["topic"] == 3
    assert "reply_to" not in rec


def test_general_topic_is_one_and_its_replies_stay_replies():
    plain = messages.message_to_dict(_msg(5), CHAT, forum=True)
    reply = messages.message_to_dict(
        _msg(6, types.MessageReplyHeader(reply_to_msg_id=5)), CHAT, forum=True
    )
    assert plain["topic"] == 1 and "reply_to" not in plain
    assert reply["topic"] == 1 and reply["reply_to"] == 5


def test_no_topic_key_outside_forums():
    rec = messages.message_to_dict(
        _msg(6, types.MessageReplyHeader(reply_to_msg_id=5, reply_to_top_id=4)), CHAT
    )
    assert "topic" not in rec
    assert rec["reply_to"] == 5


@pytest.mark.asyncio
async def test_get_history_has_one_title_map(monkeypatch):
    cl = _client(monkeypatch, _channel(True), {1: "General", 3: "Ideas"})
    cl.get_messages.return_value = [_in_topic(5, 3), _reply_in_topic(6, 5, 3), _msg(7)]

    data = json.loads(await messages.get_history(chat_id=CHAT))

    assert [r.get("topic") for r in data["results"]] == [3, 3, 1]
    assert data["topics"] == {"1": "General", "3": "Ideas"}
    assert "Ideas" not in json.dumps(data["results"])


@pytest.mark.asyncio
async def test_titles_are_cached_and_refetched_for_an_unknown_topic(monkeypatch):
    cl = _client(monkeypatch, _channel(True), {3: "Ideas", 4: "Help"})
    cl.get_messages.return_value = [_in_topic(5, 3)]
    await messages.get_history(chat_id=CHAT)
    await messages.get_history(chat_id=CHAT)
    assert cl.await_count == 1

    cl.get_messages.return_value = [_in_topic(5, 3), _in_topic(8, 4)]
    data = json.loads(await messages.get_history(chat_id=CHAT))
    assert cl.await_count == 2
    assert cl.await_args.args[0].topics == [4]
    assert data["topics"] == {"3": "Ideas", "4": "Help"}


@pytest.mark.asyncio
async def test_topic_edit_service_message_refreshes_titles(monkeypatch):
    titles = {3: "Ideas"}
    cl = _client(monkeypatch, _channel(True), titles)
    cl.get_messages.return_value = [_in_topic(5, 3)]
    await messages.get_history(chat_id=CHAT)

    titles[3] = "Plans"
    edit = _msg(
        9,
        types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=3),
        types.MessageActionTopicEdit(title="Plans"),
    )
    cl.get_messages.return_value = [_in_topic(5, 3), edit]
    data = json.loads(await messages.get_history(chat_id=CHAT))
    assert data["topics"] == {"3": "Plans"}


@pytest.mark.asyncio
async def test_no_topics_in_a_plain_group(monkeypatch):
    cl = _client(monkeypatch, _channel(False))
    cl.get_messages.return_value = [_msg(5)]
    data = json.loads(await messages.get_history(chat_id=CHAT))
    assert "topics" not in data and "topic" not in data["results"][0]
    cl.assert_not_awaited()


@pytest.mark.asyncio
async def test_title_lookup_failure_does_not_fail_the_read(monkeypatch):
    cl = _client(monkeypatch, _channel(True))
    cl.side_effect = RuntimeError("boom")
    cl.get_messages.return_value = [_in_topic(5, 3)]
    data = json.loads(await messages.get_history(chat_id=CHAT))
    assert data["results"][0]["topic"] == 3
    assert "topics" not in data


@pytest.mark.asyncio
async def test_get_messages_lines_name_topic_and_end_with_titles(monkeypatch):
    cl = _client(monkeypatch, _channel(True), {3: "Ideas"})
    cl.get_messages.return_value = [_in_topic(5, 3), _reply_in_topic(6, 5, 3)]
    lines = (await messages.get_messages(chat_id=CHAT)).split("\n")
    assert "topic 3" in lines[0] and "reply to" not in lines[0]
    assert "topic 3" in lines[1] and "reply to 5" in lines[1]
    assert lines[2] == 'Topics: {"3": "Ideas"}'
    assert "Ideas" not in lines[0] + lines[1]


@pytest.mark.asyncio
async def test_pinned_messages_name_topic(monkeypatch):
    cl = _client(monkeypatch, _channel(True), {3: "Ideas"})
    cl.get_messages.return_value = [_in_topic(5, 3)]
    data = json.loads(await messages.get_pinned_messages(chat_id=CHAT))
    assert data["results"][0]["topic"] == 3
    assert data["topics"] == {"3": "Ideas"}


@pytest.mark.asyncio
async def test_failed_lookup_is_not_retried_for_a_while(monkeypatch):
    cl = _client(monkeypatch, _channel(True))
    cl.side_effect = RuntimeError("boom")
    cl.get_messages.return_value = [_in_topic(5, 3)]
    clock = [1000.0]
    monkeypatch.setattr(messages.time, "monotonic", lambda: clock[0])

    await messages.get_history(chat_id=CHAT)
    await messages.get_history(chat_id=CHAT)
    assert cl.await_count == 1

    clock[0] += messages.TOPIC_MISS_SECONDS + 1
    await messages.get_history(chat_id=CHAT)
    assert cl.await_count == 2


@pytest.mark.asyncio
async def test_deleted_topic_is_asked_for_once(monkeypatch):
    cl = _client(monkeypatch, _channel(True), {3: "Ideas"})  # no title for topic 4
    cl.get_messages.return_value = [_in_topic(5, 3), _in_topic(8, 4)]

    first = json.loads(await messages.get_history(chat_id=CHAT))
    second = json.loads(await messages.get_history(chat_id=CHAT))

    assert first["topics"] == second["topics"] == {"3": "Ideas"}
    assert cl.await_count == 1


@pytest.mark.asyncio
async def test_a_slow_lookup_is_cut_off(monkeypatch):
    cl = _client(monkeypatch, _channel(True))

    async def sleeps(request):
        await asyncio.sleep(30)  # a flood wait Telethon sleeps through

    cl.side_effect = sleeps
    monkeypatch.setattr(messages, "TOPIC_LOOKUP_TIMEOUT", 0.01)
    cl.get_messages.return_value = [_in_topic(5, 3)]

    data = json.loads(await asyncio.wait_for(messages.get_history(chat_id=CHAT), 2))

    assert data["results"][0]["topic"] == 3 and "topics" not in data


@pytest.mark.asyncio
async def test_topic_creation_clears_the_misses_too(monkeypatch):
    titles = {}
    cl = _client(monkeypatch, _channel(True), titles)
    cl.get_messages.return_value = [_in_topic(5, 3)]
    await messages.get_history(chat_id=CHAT)  # no title yet: a miss

    titles[3] = "Ideas"
    create = _msg(
        3,
        None,
        types.MessageActionTopicCreate(title="Ideas", icon_color=0),
    )
    cl.get_messages.return_value = [create, _in_topic(5, 3)]
    data = json.loads(await messages.get_history(chat_id=CHAT))
    assert data["topics"] == {"3": "Ideas"}


def test_topic_creation_message_belongs_to_the_topic_it_creates():
    create = _msg(7, None, types.MessageActionTopicCreate(title="Ideas", icon_color=0))
    assert messages.message_to_dict(create, CHAT, forum=True)["topic"] == 7


def _edit(msg_id, topic):
    return _msg(
        msg_id,
        types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic),
        types.MessageActionTopicEdit(title="Plans"),
    )


@pytest.mark.asyncio
async def test_a_service_message_in_every_read_clears_the_caches_once(monkeypatch):
    cl = _client(monkeypatch, _channel(True), {3: "Plans"})
    cl.get_messages.return_value = [_in_topic(5, 3), _edit(9, 3)]
    for _ in range(3):
        data = json.loads(await messages.get_history(chat_id=CHAT))
    assert cl.await_count == 1
    assert data["topics"] == {"3": "Plans"}


@pytest.mark.asyncio
async def test_a_newer_rename_clears_the_cache_again(monkeypatch):
    titles = {3: "Plans"}
    cl = _client(monkeypatch, _channel(True), titles)
    cl.get_messages.return_value = [_in_topic(5, 3), _edit(9, 3)]
    await messages.get_history(chat_id=CHAT)

    titles[3] = "Roadmap"
    cl.get_messages.return_value = [_in_topic(5, 3), _edit(9, 3), _edit(12, 3)]
    data = json.loads(await messages.get_history(chat_id=CHAT))
    assert data["topics"] == {"3": "Roadmap"}
    assert cl.await_count == 2


@pytest.mark.asyncio
async def test_two_service_messages_newest_first_clear_once(monkeypatch):
    # get_history returns newest first: the mark must stay at the newest id.
    cl = _client(monkeypatch, _channel(True), {3: "Plans"})
    cl.get_messages.return_value = [_edit(20, 3), _edit(12, 3), _in_topic(5, 3)]
    for _ in range(3):
        await messages.get_history(chat_id=CHAT)
    assert cl.await_count == 1
    assert messages._topic_actions_seen[CHAT] == 20
