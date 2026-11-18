"""Per-message event log, wait_for_events and get_messages_after (chat bridges),
and send_message naming the id of the message it sent."""

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

from telegram_mcp.tools import events, messages

DATE = datetime(2026, 10, 10, 18, 2, 11, tzinfo=timezone.utc)
SUPERGROUP_ID = 1234567890
MARKED_SUPERGROUP_ID = -1001234567890


@pytest.fixture(autouse=True)
def _clean_log(monkeypatch):
    monkeypatch.setattr(events, "_event_log", deque(maxlen=2000))
    monkeypatch.setattr(events, "_event_seq", 0)
    monkeypatch.setattr(events, "_event_signal", None)
    monkeypatch.setattr(events, "_pending_msgs", {})
    monkeypatch.setattr(events, "_activity_event", None)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_EVENT_LOG_SIZE", raising=False)
    monkeypatch.setattr(events, "ALLOWED_CHAT_IDS", None, raising=False)


def _event(
    *,
    chat_id,
    msg_id=7,
    private=False,
    group=True,
    channel=False,
    out=False,
    mentioned=False,
    action=None,
    text="secret words",
    sender_name="Ada",
    chat=None,
    reply_to=SimpleNamespace(reply_to_msg_id=5),
):
    sender = types.User(id=42, first_name=sender_name)
    msg = SimpleNamespace(
        id=msg_id,
        out=out,
        mentioned=mentioned,
        action=action,
        message=text,
        sender_id=42,
        date=DATE,
        reply_to=reply_to,
    )

    async def get_sender():
        return sender

    async def get_chat():
        return chat

    return SimpleNamespace(
        chat=chat,
        sender=sender,
        is_private=private,
        is_group=None if group is None else (group and not private),
        is_channel=channel,
        chat_id=chat_id,
        message=msg,
        get_sender=get_sender,
        get_chat=get_chat,
    )


def _target(chat_id):
    async def _resolve(value, account=None):
        return chat_id if value is not None else None

    return _resolve


# --- recording -------------------------------------------------------------


@pytest.mark.asyncio
async def test_group_mention_is_addressed_and_carries_no_text():
    await events._on_message_event(
        _event(chat_id=MARKED_SUPERGROUP_ID, channel=True, mentioned=True)
    )

    (rec,) = events._event_log
    assert rec["chat_type"] == "supergroup"
    assert rec["addressed"] is True
    assert rec["out"] is False
    assert rec["message_id"] == 7
    assert rec["sender_name"] == "Ada"
    assert rec["reply_to"] == 5
    assert rec["date"] == "2026-10-10T18:02:11Z"
    assert "secret words" not in json.dumps(rec)


@pytest.mark.asyncio
async def test_group_message_without_mention_is_not_addressed():
    await events._on_message_event(_event(chat_id=-555, channel=False))

    (rec,) = events._event_log
    assert rec["chat_type"] == "group"
    assert rec["addressed"] is False


@pytest.mark.asyncio
async def test_private_message_is_addressed():
    await events._on_message_event(_event(chat_id=42, private=True, group=False))

    (rec,) = events._event_log
    assert rec["chat_type"] == "private"
    assert rec["addressed"] is True


@pytest.mark.asyncio
async def test_own_message_from_another_device_is_out_and_not_addressed():
    await events._on_message_event(_event(chat_id=42, private=True, group=False, out=True))

    (rec,) = events._event_log
    assert rec["out"] is True
    assert rec["addressed"] is False


@pytest.mark.asyncio
async def test_broadcast_channel_posts_are_not_recorded():
    await events._on_message_event(_event(chat_id=MARKED_SUPERGROUP_ID, group=False, channel=True))

    assert list(events._event_log) == []


@pytest.mark.asyncio
async def test_unknown_channel_kind_is_resolved_from_the_chat():
    megagroup = types.Channel(
        id=SUPERGROUP_ID,
        title="Group",
        photo=types.ChatPhotoEmpty(),
        date=DATE,
        megagroup=True,
    )
    await events._on_message_event(
        _event(chat_id=MARKED_SUPERGROUP_ID, group=None, channel=True, chat=megagroup)
    )

    (rec,) = events._event_log
    assert rec["chat_type"] == "supergroup"


@pytest.mark.asyncio
async def test_service_message_is_flagged():
    await events._on_message_event(_event(chat_id=-555, action=object()))

    (rec,) = events._event_log
    assert rec["service"] is True


@pytest.mark.asyncio
async def test_allowlist_keeps_other_chats_out(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")

    await events._on_message_event(_event(chat_id=-555))
    await events._on_message_event(_event(chat_id=42, private=True, group=False))

    assert [rec["chat_id"] for rec in events._event_log] == [42]


@pytest.mark.asyncio
async def test_allowlist_username_entries_match_the_chat(monkeypatch):
    group = types.Channel(
        id=SUPERGROUP_ID,
        title="G",
        photo=types.ChatPhotoEmpty(),
        date=DATE,
        megagroup=True,
        username="okgroup",
    )
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "okgroup")

    await events._on_message_event(_event(chat_id=MARKED_SUPERGROUP_ID, channel=True, chat=group))
    result = json.loads(await events.wait_for_events(timeout=0))

    assert [e["chat_id"] for e in result["events"]] == [MARKED_SUPERGROUP_ID]
    assert "_chat" not in result["events"][0]


@pytest.mark.asyncio
async def test_reads_reapply_the_allowlist_with_the_chat_entity(monkeypatch):
    group = types.Channel(
        id=SUPERGROUP_ID,
        title="G",
        photo=types.ChatPhotoEmpty(),
        date=DATE,
        megagroup=True,
        username="okgroup",
    )
    await events._on_message_event(_event(chat_id=MARKED_SUPERGROUP_ID, channel=True, chat=group))
    await events._on_message_event(_event(chat_id=-555))

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "okgroup")
    result = json.loads(await events.wait_for_events(timeout=0))

    assert [e["chat_id"] for e in result["events"]] == [MARKED_SUPERGROUP_ID]


@pytest.mark.asyncio
async def test_wait_for_a_chat_outside_the_allowlist_is_refused(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")
    monkeypatch.setattr(events, "_wait_target", _target(-555))
    monkeypatch.setattr(events, "get_client", lambda account=None: object())
    monkeypatch.setattr(
        events,
        "resolve_entity",
        AsyncMock(
            return_value=types.Chat(
                id=555,
                title="other",
                photo=types.ChatPhotoEmpty(),
                participants_count=2,
                date=DATE,
                version=1,
            )
        ),
    )
    await events._on_message_event(_event(chat_id=42, private=True, group=False))

    result = await asyncio.wait_for(events.wait_for_events(chat_id=-555, timeout=5), 1)

    assert "restricted" in result
    assert "last_seq" not in result


@pytest.mark.asyncio
async def test_log_size_zero_records_nothing(monkeypatch):
    monkeypatch.setattr(events, "_EVENT_LOG_SIZE", 0)

    await events._on_message_event(_event(chat_id=-555))

    assert list(events._event_log) == []


@pytest.mark.asyncio
async def test_group_messages_stay_out_of_the_private_debounce_tools():
    await events._on_new_incoming(_event(chat_id=-555))

    assert events._pending_msgs == {}


def test_both_handlers_registered_per_client(monkeypatch):
    added = []

    class FakeClient:
        def add_event_handler(self, callback, event):
            added.append((callback, event))

    monkeypatch.setattr(events, "clients", {"default": FakeClient()})
    events.register_incoming_handlers()

    assert len(added) == 2
    assert added[0][0] is events._on_new_incoming
    assert added[0][1].incoming is True
    assert added[1][1].incoming is None  # outgoing messages from other devices too


# --- wait_for_events -------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_returns_retained_events_at_once():
    await events._on_message_event(_event(chat_id=-555, msg_id=1))
    await events._on_message_event(_event(chat_id=-555, msg_id=2))

    result = json.loads(await events.wait_for_events(timeout=5))

    assert [e["message_id"] for e in result["events"]] == [1, 2]
    assert result["last_seq"] == 2
    assert result["gap"] is False
    assert result["boot"] == events._event_boot


@pytest.mark.asyncio
async def test_wait_blocks_until_an_event_arrives():
    waiter = asyncio.create_task(events.wait_for_events(after_seq=0, timeout=5))
    await asyncio.sleep(0.05)
    assert not waiter.done()

    await events._on_message_event(_event(chat_id=-555, msg_id=9))
    result = json.loads(await asyncio.wait_for(waiter, 1))

    assert [e["message_id"] for e in result["events"]] == [9]


@pytest.mark.asyncio
async def test_wait_times_out_empty():
    result = json.loads(await events.wait_for_events(after_seq=0, timeout=0.05))

    assert result["events"] == []
    assert result["last_seq"] == 0


@pytest.mark.asyncio
async def test_wait_for_one_chat_ignores_others_but_advances(monkeypatch):
    monkeypatch.setattr(events, "_wait_target", _target(42))
    await events._on_message_event(_event(chat_id=-555, msg_id=1))

    waiter = asyncio.create_task(events.wait_for_events(chat_id=42, timeout=5))
    await asyncio.sleep(0.05)
    assert not waiter.done()  # the other chat did not wake it

    await events._on_message_event(_event(chat_id=42, msg_id=2, private=True, group=False))
    result = json.loads(await asyncio.wait_for(waiter, 1))

    assert [e["chat_id"] for e in result["events"]] == [42]
    assert result["last_seq"] == 2


@pytest.mark.asyncio
async def test_wait_with_another_boot_asks_to_resync():
    await events._on_message_event(_event(chat_id=-555))

    result = json.loads(await events.wait_for_events(after_seq=1, boot="old", timeout=5))

    assert result["reset"] is True
    assert result["last_seq"] == 1
    assert result["events"] == []


@pytest.mark.asyncio
async def test_wait_beyond_the_current_sequence_asks_to_resync():
    result = json.loads(await events.wait_for_events(after_seq=50, timeout=5))

    assert result["reset"] is True and result["last_seq"] == 0


@pytest.mark.asyncio
async def test_overflowed_log_reports_a_gap(monkeypatch):
    monkeypatch.setattr(events, "_event_log", deque(maxlen=2))
    for msg_id in (1, 2, 3, 4):
        await events._on_message_event(_event(chat_id=-555, msg_id=msg_id))

    result = json.loads(await events.wait_for_events(after_seq=1, timeout=5))

    assert result["gap"] is True
    assert [e["message_id"] for e in result["events"]] == [3, 4]


@pytest.mark.asyncio
async def test_limit_returns_the_last_returned_sequence():
    for msg_id in (1, 2, 3):
        await events._on_message_event(_event(chat_id=-555, msg_id=msg_id))

    result = json.loads(await events.wait_for_events(limit=2, timeout=5))

    assert [e["seq"] for e in result["events"]] == [1, 2]
    assert result["last_seq"] == 2


@pytest.mark.asyncio
async def test_timeout_is_capped_below_typical_proxy_timeouts(monkeypatch):
    assert events._EVENT_WAIT_MAX_SECONDS <= 100
    monkeypatch.setattr(events, "_EVENT_WAIT_MAX_SECONDS", 0.05)

    result = json.loads(await asyncio.wait_for(events.wait_for_events(timeout=600), 2))

    assert result["events"] == []


def test_wait_ceiling_stays_under_the_tool_timeout(monkeypatch):
    monkeypatch.delenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", raising=False)
    assert events._event_wait_ceiling() == 50.0  # default tool timeout 55 s
    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "300")
    assert events._event_wait_ceiling() == events._EVENT_WAIT_MAX_SECONDS
    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0")
    assert events._event_wait_ceiling() == events._EVENT_WAIT_MAX_SECONDS


@pytest.mark.asyncio
async def test_after_seq_minus_one_starts_from_now():
    await events._on_message_event(_event(chat_id=-555, msg_id=1))

    result = json.loads(await events.wait_for_events(after_seq=-1, boot="old", timeout=5))

    assert result == {"boot": events._event_boot, "gap": False, "last_seq": 1, "events": []}


@pytest.mark.asyncio
async def test_account_filter_folds_the_label():
    await events._on_message_event(_event(chat_id=-555, msg_id=1), "work_acc")
    await events._on_message_event(_event(chat_id=-555, msg_id=2), "home")

    result = json.loads(await events.wait_for_events(account="Work-Acc", timeout=0))

    assert [e["message_id"] for e in result["events"]] == [1]


@pytest.mark.asyncio
async def test_events_name_their_account_only_with_several_accounts(monkeypatch):
    await events._on_message_event(_event(chat_id=-555), "work_acc")

    single = json.loads(await events.wait_for_events(timeout=0))
    monkeypatch.setattr(events, "is_multi_mode", lambda: True)
    multi = json.loads(await events.wait_for_events(timeout=0))

    assert "account" not in single["events"][0]
    assert multi["events"][0]["account"] == "work_acc"


def test_invalid_log_size_falls_back_to_the_default(monkeypatch):
    warnings = []
    logger = logging.getLogger("telegram_mcp")
    monkeypatch.setattr(logger, "warning", lambda msg, *a, **k: warnings.append(msg))
    monkeypatch.setenv("TELEGRAM_EVENT_LOG_SIZE", "lots")

    assert events._event_log_size() == events._EVENT_LOG_DEFAULT_SIZE
    assert warnings == ["Invalid TELEGRAM_EVENT_LOG_SIZE; using the default size."]
    monkeypatch.setenv("TELEGRAM_EVENT_LOG_SIZE", "0")
    assert events._event_log_size() == 0


def test_tools_are_read_only_and_registered():
    tools = {t.name: t for t in events.mcp._tool_manager.list_tools()}
    for name in ("wait_for_events", "get_messages_after"):
        assert tools[name].annotations.readOnlyHint is True


# --- get_messages_after ----------------------------------------------------


def _group_message(msg_id, *, mentioned=False, out=False, text="hi", action=None):
    msg = types.Message(
        id=msg_id,
        peer_id=types.PeerChannel(SUPERGROUP_ID),
        date=DATE,
        message=text,
        out=out,
        mentioned=mentioned,
        from_id=types.PeerUser(42),
    )
    if action is not None:
        msg = types.MessageService(
            id=msg_id, peer_id=types.PeerChannel(SUPERGROUP_ID), date=DATE, action=action
        )
    return msg


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(events, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        events,
        "resolve_entity",
        AsyncMock(
            return_value=types.Channel(
                id=SUPERGROUP_ID,
                title="Group",
                photo=types.ChatPhotoEmpty(),
                date=DATE,
                megagroup=True,
            )
        ),
    )
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


@pytest.mark.asyncio
async def test_messages_after_reads_forward_from_the_id(client):
    client.get_messages.return_value = [
        _group_message(11, mentioned=True),
        _group_message(12, out=True),
        _group_message(13, action=types.MessageActionChatJoinedByLink(inviter_id=1)),
    ]

    result = json.loads(
        await events.get_messages_after(MARKED_SUPERGROUP_ID, after_id=10, account="test")
    )

    kwargs = client.get_messages.call_args.kwargs
    assert kwargs["min_id"] == 10 and kwargs["reverse"] is True
    assert result["chat_id"] == MARKED_SUPERGROUP_ID
    assert result["chat_type"] == "supergroup"
    first, own, service = result["messages"]
    assert first["id"] == 11 and first["addressed"] is True and first["text"] == "hi"
    assert first["date"] == "2026-10-10T18:02:11Z"
    assert own["out"] is True and own["addressed"] is False
    assert service["action"] == "MessageActionChatJoinedByLink"


@pytest.mark.asyncio
async def test_messages_after_without_id_returns_newest_oldest_first(client):
    client.get_messages.return_value = [_group_message(12), _group_message(11)]

    result = json.loads(
        await events.get_messages_after(MARKED_SUPERGROUP_ID, limit=2, account="test")
    )

    assert [m["id"] for m in result["messages"]] == [11, 12]
    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_messages_after_rejects_a_negative_id(client):
    result = await events.get_messages_after(MARKED_SUPERGROUP_ID, after_id=-1, account="test")

    assert "after_id" in result
    client.get_messages.assert_not_called()


@pytest.mark.asyncio
async def test_messages_after_respects_the_allowlist(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "42")

    result = await events.get_messages_after(MARKED_SUPERGROUP_ID, after_id=1, account="test")

    assert "restricted" in result
    client.get_messages.assert_not_called()


# --- send_message ----------------------------------------------------------


@pytest.fixture
def send_client(monkeypatch):
    cl = AsyncMock()
    cl.send_message.return_value = SimpleNamespace(id=5515)
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=SimpleNamespace(id=1)))
    return cl


@pytest.mark.asyncio
async def test_send_message_names_the_sent_id(send_client):
    result = await messages.send_message(42, "hello", account="test")

    assert result == "Message sent successfully. message_id=5515"
    assert send_client.send_message.call_args.kwargs["reply_to"] is None


@pytest.mark.asyncio
async def test_send_message_replies_and_names_the_id(send_client):
    result = await messages.send_message(42, "hello", reply_to=5513, account="test")

    assert result.endswith("message_id=5515")
    assert send_client.send_message.call_args.kwargs["reply_to"] == 5513


def test_raw_send_result_id_is_found():
    short = types.UpdateShortSentMessage(id=77, pts=1, pts_count=1, date=DATE)
    assert messages._first_sent_message_id(short) == 77
    full = types.Updates(
        updates=[types.UpdateMessageID(id=78, random_id=1)], users=[], chats=[], date=DATE, seq=0
    )
    assert messages._first_sent_message_id(full) == 78
    assert messages._sent_result(None) == "Message sent successfully."


# --- forum topics ----------------------------------------------------------


def _forum():
    return types.Channel(
        id=SUPERGROUP_ID,
        title="Forum",
        photo=types.ChatPhotoEmpty(),
        date=DATE,
        megagroup=True,
        forum=True,
    )


def _header(reply_to_msg_id, top_id=None):
    return types.MessageReplyHeader(
        forum_topic=True, reply_to_msg_id=reply_to_msg_id, reply_to_top_id=top_id
    )


@pytest.mark.asyncio
async def test_event_of_a_plain_message_in_a_topic_has_topic_and_no_reply():
    await events._on_message_event(
        _event(chat_id=MARKED_SUPERGROUP_ID, channel=True, reply_to=_header(3), chat=_forum())
    )

    (rec,) = events._event_log
    assert rec["topic"] == 3
    assert rec["reply_to"] is None
    assert "reply_to" not in events._public_event(rec)


@pytest.mark.asyncio
async def test_event_of_a_reply_inside_a_topic_has_both():
    await events._on_message_event(
        _event(chat_id=MARKED_SUPERGROUP_ID, channel=True, reply_to=_header(5, 3), chat=_forum())
    )

    (rec,) = events._event_log
    assert rec["topic"] == 3 and rec["reply_to"] == 5


@pytest.mark.asyncio
async def test_event_in_general_has_topic_one_and_other_chats_none():
    await events._on_message_event(
        _event(chat_id=MARKED_SUPERGROUP_ID, channel=True, reply_to=None, chat=_forum())
    )
    await events._on_message_event(_event(chat_id=MARKED_SUPERGROUP_ID + 1, channel=True))

    forum_rec, plain_rec = events._event_log
    assert forum_rec["topic"] == 1
    assert "topic" not in events._public_event(plain_rec)


@pytest.fixture
def forum_client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(events, "get_client", lambda account=None: cl)
    monkeypatch.setattr(events, "resolve_entity", AsyncMock(return_value=_forum()))
    monkeypatch.setattr(messages, "_topic_titles", {})
    monkeypatch.setattr(messages, "_topic_misses", {})
    monkeypatch.setattr(messages, "_topic_actions_seen", {})
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


@pytest.mark.asyncio
async def test_messages_after_names_topics_once(forum_client):
    def msg(msg_id, header):
        return types.Message(
            id=msg_id,
            peer_id=types.PeerChannel(SUPERGROUP_ID),
            date=DATE,
            message=f"m{msg_id}",
            reply_to=header,
        )

    forum_client.get_messages.return_value = [msg(1, _header(3)), msg(2, _header(1, 3))]
    topic = types.ForumTopic(
        id=3,
        date=DATE,
        peer=types.PeerChannel(SUPERGROUP_ID),
        title="Ideas",
        icon_color=0,
        top_message=3,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=0,
        unread_mentions_count=0,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        from_id=types.PeerUser(1),
        notify_settings=types.PeerNotifySettings(),
    )
    forum_client.side_effect = lambda request: types.messages.ForumTopics(
        count=1, topics=[topic], messages=[], chats=[], users=[], pts=0
    )

    result = json.loads(await events.get_messages_after(MARKED_SUPERGROUP_ID, account="test"))

    assert result["topics"] == {"3": "Ideas"}
    first, reply = sorted(result["messages"], key=lambda m: m["id"])
    assert first["topic"] == 3 and "reply_to" not in first
    assert reply["topic"] == 3 and reply["reply_to"] == 1
    assert "Ideas" not in json.dumps(result["messages"])


def test_get_messages_after_description_names_the_topics_field():
    # Chat bridges read this to learn that records carry forum topics.
    assert "`topics`" in (events.get_messages_after.__doc__ or "")


@pytest.mark.asyncio
async def test_event_of_a_chat_that_is_no_forum_has_no_topic():
    plain_group = types.Channel(
        id=SUPERGROUP_ID, title="Group", photo=types.ChatPhotoEmpty(), date=DATE, megagroup=True
    )
    await events._on_message_event(
        _event(chat_id=MARKED_SUPERGROUP_ID, channel=True, reply_to=_header(3), chat=plain_group)
    )

    (rec,) = events._event_log
    assert "topic" not in events._public_event(rec)
