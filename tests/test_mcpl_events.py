"""Phase 4 tests — Telethon NewMessage event → ChannelIncomingMessage."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from telethon.tl.types import (
    Channel,
    Chat,
    MessageEntityMention,
    MessageEntityMentionName,
    User,
)

from telegram_mcp.mcpl.events import (
    _attached_clients,
    _author_dict,
    _build_chat_action_payload,
    _extract_mention_ids,
    _has_username_mention,
    attach_event_handlers,
    build_incoming_message,
    reset_attached_clients,
)


# ---------------------------------------------------------------------------
# Helpers — build fakes of Telethon types just rich enough for the tests
# ---------------------------------------------------------------------------


def make_user(uid: int, *, first="", last=None, username=None) -> User:
    return User(
        id=uid,
        is_self=False,
        contact=False,
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
        apply_min_photo=False,
        fake=False,
        bot_attach_menu=False,
        premium=False,
        attach_menu_enabled=False,
        access_hash=None,
        first_name=first,
        last_name=last,
        username=username,
        phone=None,
        photo=None,
        status=None,
        bot_info_version=None,
        restriction_reason=None,
        bot_inline_placeholder=None,
        lang_code=None,
    )


class FakeMessage:
    """Telethon Message-like fake for build_incoming_message."""

    def __init__(
        self,
        *,
        id: int,
        text: str,
        date=None,
        entities=None,
        media=None,
        is_reply: bool = False,
        reply_to=None,
        replied=None,
        mentioned: bool = False,
    ):
        self.id = id
        self.message = text
        self.date = date or datetime(2026, 5, 7, tzinfo=timezone.utc)
        self.entities = entities or []
        self.media = media
        self.is_reply = is_reply
        self.reply_to = reply_to
        self.mentioned = mentioned
        self._replied = replied

    async def get_reply_message(self):
        return self._replied


class FakeEvent:
    def __init__(self, message: FakeMessage, chat: Any, sender: Any):
        self.message = message
        self._chat = chat
        self._sender = sender

    async def get_chat(self):
        return self._chat

    async def get_sender(self):
        return self._sender


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_author_dict_user_with_full_name():
    user = make_user(7, first="Alice", last="Wonderland")
    assert _author_dict(user) == {"id": "7", "name": "Alice Wonderland"}


def test_author_dict_user_falls_back_to_username():
    user = make_user(7, first="", username="alice")
    assert _author_dict(user) == {"id": "7", "name": "@alice"}


def test_author_dict_unknown_sender():
    assert _author_dict(None) == {"id": "unknown", "name": "Unknown"}


def test_extract_mention_ids_returns_user_ids_from_entities():
    msg = SimpleNamespace(
        entities=[
            MessageEntityMentionName(offset=0, length=5, user_id=42),
            MessageEntityMention(offset=10, length=6),  # @bare username — no id
            MessageEntityMentionName(offset=20, length=5, user_id=99),
        ]
    )
    assert _extract_mention_ids(msg) == [42, 99]


def test_has_username_mention_matches_case_insensitive():
    msg = SimpleNamespace(
        message="hello @MyBot how are you",
        entities=[MessageEntityMention(offset=6, length=6)],
    )
    assert _has_username_mention(msg, "mybot") is True
    assert _has_username_mention(msg, "otherbot") is False
    assert _has_username_mention(msg, None) is False


# ---------------------------------------------------------------------------
# build_incoming_message — the integrated path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dm_text_message_basic_metadata():
    chat = make_user(42, first="Alice", username="alice")  # the OTHER party in a DM
    sender = chat
    msg = FakeMessage(id=100, text="hi there")
    event = FakeEvent(msg, chat, sender)

    out = await build_incoming_message(
        event, account_label="default", self_id=99, self_username="mybot"
    )

    assert out is not None
    assert out["channelId"] == "telegram:default:dm:42"
    assert out["messageId"] == "100"
    assert out["author"] == {"id": "42", "name": "Alice"}
    assert out["content"] == [{"type": "text", "text": "hi there"}]
    assert out["timestamp"].endswith("+00:00")
    md = out["metadata"]
    assert md["account"] == "default"
    assert md["botUserId"] == "99"
    assert md["chatType"] == "dm"
    assert md["isPrivate"] is True
    assert md["isReply"] is False
    assert md["isMention"] is False
    assert md["mentionIds"] == []
    assert md["senderId"] == "42"
    assert md["senderUsername"] == "alice"


@pytest.mark.asyncio
async def test_username_mention_marks_isMention():
    chat = make_user(42, first="Alice")
    sender = chat
    msg = FakeMessage(
        id=100,
        text="hey @MyBot please help",
        entities=[MessageEntityMention(offset=4, length=6)],
    )
    event = FakeEvent(msg, chat, sender)

    out = await build_incoming_message(
        event, account_label="default", self_id=99, self_username="MyBot"
    )

    assert out is not None
    assert out["metadata"]["isMention"] is True


@pytest.mark.asyncio
async def test_explicit_mention_name_marks_isMention_with_id_in_list():
    chat = make_user(42, first="Alice")
    sender = chat
    msg = FakeMessage(
        id=100,
        text="hey  please help",
        entities=[MessageEntityMentionName(offset=4, length=2, user_id=99)],
    )
    event = FakeEvent(msg, chat, sender)

    out = await build_incoming_message(
        event, account_label="default", self_id=99, self_username=None
    )

    assert out is not None
    assert out["metadata"]["isMention"] is True
    assert "99" in out["metadata"]["mentionIds"]


@pytest.mark.asyncio
async def test_reply_metadata_includes_snippet_and_author():
    chat = make_user(42, first="Alice")
    sender = chat
    replied = FakeMessage(id=50, text="original message text")
    replied.sender_id = 99  # we are the original author
    msg = FakeMessage(
        id=100,
        text="thanks",
        is_reply=True,
        reply_to=SimpleNamespace(reply_to_msg_id=50, forum_topic=False),
        replied=replied,
    )
    event = FakeEvent(msg, chat, sender)

    out = await build_incoming_message(
        event, account_label="default", self_id=99, self_username=None
    )

    assert out is not None
    md = out["metadata"]
    assert md["isReply"] is True
    assert md["replyToMessageId"] == "50"
    assert md["replyToContent"] == "original message text"
    assert md["replyToAuthorId"] == "99"
    assert md["isReplyToBot"] is True


class FakeChatActionEvent:
    """Mimics events.ChatAction.Event enough for _build_chat_action_payload."""

    def __init__(self, chat, *, user_id=None, **flags):
        self._chat = chat
        self.user_id = user_id
        for name in (
            "user_kicked",
            "user_left",
            "user_added",
            "user_joined",
        ):
            setattr(self, name, flags.get(name, False))

    async def get_chat(self):
        return self._chat


@pytest.mark.asyncio
async def test_attach_event_handlers_is_idempotent_per_client():
    """A host reconnect re-fires on_ready; duplicate attachment must no-op."""
    reset_attached_clients()

    on_calls: list[str] = []

    class FakeMe:
        id = 99
        username = "mybot"

    class FakeClient:
        def __init__(self):
            self.handlers: list = []

        async def get_me(self):
            return FakeMe()

        def on(self, event_filter):
            def decorator(fn):
                on_calls.append(type(event_filter).__name__)
                self.handlers.append(fn)
                return fn

            return decorator

    class FakeTransport:
        async def send_notification(self, *args, **kwargs):
            pass

    client = FakeClient()
    transport = FakeTransport()

    await attach_event_handlers(client, account_label="default", transport=transport)
    first_attach_count = len(on_calls)
    assert first_attach_count >= 1
    assert id(client) in _attached_clients

    # Second call (simulating host reconnect → on_ready re-fire) must no-op.
    await attach_event_handlers(client, account_label="default", transport=transport)
    assert len(on_calls) == first_attach_count, (
        "attach_event_handlers must be idempotent per client"
    )


@pytest.mark.asyncio
async def test_chat_action_self_left_emits_removed():
    chat = make_user(42, first="Alice")  # placeholder — using a Chat would be more realistic
    # Use a real Chat fixture
    from telethon.tl.types import Chat as TelChat

    chat = TelChat(
        id=1001,
        title="Group",
        photo=None,
        participants_count=1,
        date=None,
        version=1,
        creator=False,
        left=True,
        deactivated=False,
        call_active=False,
        call_not_empty=False,
        noforwards=False,
        migrated_to=None,
        admin_rights=None,
        default_banned_rights=None,
    )
    event = FakeChatActionEvent(chat, user_id=99, user_left=True)

    payload = await _build_chat_action_payload(
        event, account_label="default", self_id=99
    )
    assert payload == {"removed": ["telegram:default:group:1001"]}


@pytest.mark.asyncio
async def test_chat_action_other_user_left_returns_none():
    from telethon.tl.types import Chat as TelChat

    chat = TelChat(
        id=1001,
        title="Group",
        photo=None,
        participants_count=2,
        date=None,
        version=1,
        creator=False,
        left=False,
        deactivated=False,
        call_active=False,
        call_not_empty=False,
        noforwards=False,
        migrated_to=None,
        admin_rights=None,
        default_banned_rights=None,
    )
    # Someone ELSE (user 7) left — we (99) don't care
    event = FakeChatActionEvent(chat, user_id=7, user_left=True)

    payload = await _build_chat_action_payload(
        event, account_label="default", self_id=99
    )
    assert payload is None


@pytest.mark.asyncio
async def test_chat_action_self_joined_emits_added():
    from telethon.tl.types import Chat as TelChat

    chat = TelChat(
        id=1001,
        title="New Group",
        photo=None,
        participants_count=2,
        date=None,
        version=1,
        creator=False,
        left=False,
        deactivated=False,
        call_active=False,
        call_not_empty=False,
        noforwards=False,
        migrated_to=None,
        admin_rights=None,
        default_banned_rights=None,
    )
    event = FakeChatActionEvent(chat, user_id=99, user_joined=True)

    payload = await _build_chat_action_payload(
        event, account_label="default", self_id=99
    )
    assert payload is not None
    assert "added" in payload
    assert len(payload["added"]) == 1
    assert payload["added"][0]["id"] == "telegram:default:group:1001"


@pytest.mark.asyncio
async def test_supergroup_forum_topic_emits_threadId():
    # Build a supergroup channel
    from telethon.tl.types import Channel as TelChannel

    chat = TelChannel(
        id=1000,
        title="Forum",
        photo=None,
        date=None,
        creator=False,
        left=False,
        broadcast=False,
        verified=False,
        megagroup=True,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=True,
        access_hash=None,
        username=None,
        restriction_reason=None,
        admin_rights=None,
        banned_rights=None,
        default_banned_rights=None,
        participants_count=None,
        usernames=None,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        stories_max_id=None,
        color=None,
        profile_color=None,
        emoji_status=None,
        level=None,
        subscription_until_date=None,
    )
    sender = make_user(42, first="Alice")
    msg = FakeMessage(
        id=100,
        text="in topic 7",
        reply_to=SimpleNamespace(
            reply_to_msg_id=7,
            reply_to_top_id=7,
            forum_topic=True,
        ),
    )
    event = FakeEvent(msg, chat, sender)

    out = await build_incoming_message(
        event, account_label="default", self_id=99, self_username=None
    )

    assert out is not None
    assert out["threadId"] == "7"
    assert out["channelId"] == "telegram:default:supergroup:1000"
