"""Forum topic entries in TELEGRAM_ALLOWED_CHAT_IDS: "<chat>:<topic>" limits a chat to topics."""

import json
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from telethon.tl import functions, types
from telethon.tl.types import Channel, ChatPhotoEmpty

from telegram_mcp import runtime, transcription
from telegram_mcp.runtime import (
    _parse_allowed_chat_ids,
    get_allowed_topics,
    get_blocked_chat_ids,
    is_chat_allowed,
    is_chat_blocked,
    is_topic_allowlist_enabled,
    validate_id,
)
from telegram_mcp.tools import chats, events, groups, media, messages

DATE = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
CHANNEL_ID = 1234567890
CHAT = -1001234567890  # marked ID of CHANNEL_ID
IT, OTHER, GENERAL = 163, 24, 1


@pytest.fixture(autouse=True)
def _clean_policy(monkeypatch):
    for name in ("TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_BLOCKED_CHAT_IDS", "TELEGRAM_TRANSCRIBE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)


def _forum(username=None):
    return Channel(
        id=CHANNEL_ID,
        title="Black Port",
        photo=ChatPhotoEmpty(),
        date=DATE,
        megagroup=True,
        forum=True,
        username=username,
        access_hash=1,
    )


def _header(topic, reply_to=None):
    """A forum message header: directly in `topic`, or a reply inside it."""
    if topic in (None, GENERAL):
        return None
    if reply_to is None:
        return types.MessageReplyHeader(forum_topic=True, reply_to_msg_id=topic)
    return types.MessageReplyHeader(
        forum_topic=True, reply_to_msg_id=reply_to, reply_to_top_id=topic
    )


def _msg(msg_id, topic=GENERAL, *, reply_to=None, text=None, action=None, media_obj=None):
    return SimpleNamespace(
        id=msg_id,
        date=DATE,
        message=text if text is not None else f"text of {msg_id}",
        sender=SimpleNamespace(first_name="Ada", last_name=None, title=None, username=None),
        sender_id=42,
        from_id=None,
        peer_id=types.PeerChannel(CHANNEL_ID),
        out=False,
        mentioned=False,
        reply_to=_header(topic, reply_to),
        fwd_from=None,
        media=media_obj,
        photo=None,
        document=None,
        web_preview=None,
        grouped_id=None,
        reactions=None,
        entities=None,
        rich_message=None,
        sticker=None,
        voice=None,
        video_note=None,
        video=None,
        audio=None,
        gif=None,
        file=None,
        via_bot_id=None,
        edit_date=None,
        pinned=False,
        views=None,
        forwards=None,
        replies=None,
        buttons=None,
        action=action,
        ttl_period=None,
        forward=None,
        post_author=None,
    )


def _texts(result):
    return [record["text"] for record in json.loads(result)["results"]]


class FakeForumClient:
    """Serves a forum's messages; records the kwargs of every history read."""

    def __init__(self, msgs=()):
        self.msgs = list(msgs)
        self.calls = []

    def _select(self, kwargs):
        selected = list(self.msgs)
        topic = kwargs.get("reply_to")
        if topic is not None:  # the thread Telegram returns for reply_to
            selected = [m for m in selected if messages.message_topic_id(m, True) == topic]
        if kwargs.get("max_id"):
            selected = [m for m in selected if m.id < kwargs["max_id"]]
        if kwargs.get("min_id"):
            selected = [m for m in selected if m.id > kwargs["min_id"]]
        selected.sort(key=lambda m: m.id, reverse=not kwargs.get("reverse"))
        return selected

    async def get_messages(self, entity, *args, **kwargs):
        self.calls.append(kwargs)
        if "ids" in kwargs:
            return next((m for m in self.msgs if m.id == kwargs["ids"]), None)
        selected = self._select(kwargs)
        offset = kwargs.get("add_offset", 0)
        return selected[offset : offset + kwargs.get("limit", 20)]

    async def iter_messages(self, entity, **kwargs):
        self.calls.append(kwargs)
        for msg in self._select(kwargs):
            yield msg


def _patch_tools(monkeypatch, module, client, entity=None):
    entity = entity or _forum()

    async def resolve(identifier, cl=None):
        return entity

    monkeypatch.setattr(module, "get_client", lambda account=None: client)
    monkeypatch.setattr(module, "resolve_entity", resolve)
    return entity


# --- configuration -------------------------------------------------------------


def test_topic_entry_allows_the_chat_and_limits_it_to_the_topic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}, 555")

    assert is_chat_allowed(CHAT)
    assert is_chat_allowed(555)
    assert is_topic_allowlist_enabled()
    assert get_allowed_topics(CHAT) == {IT}
    assert get_allowed_topics(555) is None
    assert get_allowed_topics(-100999) is None


def test_topics_of_one_chat_add_up_and_whole_entries_win(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},{CHAT}:5")
    assert get_allowed_topics(CHAT) == {IT, 5}

    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},{CHAT}")
    assert get_allowed_topics(CHAT) is None


def test_topic_entries_match_usernames_and_entities(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"@Black_Port:{IT}")
    assert get_allowed_topics("@black_port") == {IT}
    assert get_allowed_topics(None, _forum(username="black_port")) == {IT}
    assert is_chat_allowed("black_port")

    # Listed whole under its username, limited under its ID: the chat is allowed whole.
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},@black_port")
    assert get_allowed_topics(CHAT) == {IT}
    assert get_allowed_topics(CHAT, _forum(username="black_port")) is None


def test_spaces_around_the_colon_are_accepted(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT} : {IT}")
    assert is_chat_allowed(CHAT)
    assert get_allowed_topics(CHAT) == {IT}


@pytest.mark.parametrize("token", [f"{CHAT}:0", f"{CHAT}:abc", f"{CHAT}:-5", f"{CHAT}:"])
def test_malformed_topic_entries_allow_nothing(monkeypatch, token):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", token)
    assert not is_chat_allowed(CHAT)
    assert not is_topic_allowlist_enabled()


def test_blocklist_does_not_read_topic_entries_as_whole_chats(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", f"{CHAT}:{IT}")
    assert CHAT not in get_blocked_chat_ids()
    assert not is_chat_blocked(CHAT)


def test_parse_keeps_plain_entries_unchanged():
    assert _parse_allowed_chat_ids(f"{CHAT}:{IT},12", allow_topics=True) == {CHAT, 12}
    assert _parse_allowed_chat_ids("12,-4567,@Handle", allow_topics=True) == {
        12,
        -4567,
        "handle",
    }


# --- the validate_id gate --------------------------------------------------------


@validate_id("chat_id")
async def _chat_wide_tool(chat_id, account=None):
    return "ran"


@validate_id("chat_id", topic_aware=True)
async def _topic_tool(chat_id, account=None):
    return "ran"


@pytest.mark.asyncio
async def test_gate_refuses_tools_that_cannot_keep_to_a_topic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},555")

    refused = await _chat_wide_tool(chat_id=CHAT)
    assert "restricted by privacy policy" in refused
    assert f"topic(s) {IT}" in refused
    assert "_chat_wide_tool" in refused
    assert await _topic_tool(chat_id=CHAT) == "ran"
    assert await _chat_wide_tool(chat_id=555) == "ran"


@pytest.mark.asyncio
async def test_gate_agrees_with_tools_when_a_username_entry_allows_the_chat_whole(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},@black_port")
    entity = _forum(username="black_port")

    async def resolve(identifier, client=None):
        return entity

    monkeypatch.setattr(runtime, "resolve_entity", resolve)
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    assert get_allowed_topics(CHAT, entity) is None
    assert await _chat_wide_tool(chat_id=CHAT) == "ran"


@pytest.mark.asyncio
async def test_gate_matches_an_unresolvable_name_as_written(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"@black_port:{IT}")

    async def unresolvable(identifier, client=None):
        raise ValueError("no such chat")

    monkeypatch.setattr(runtime, "resolve_entity", unresolvable)
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    assert "restricted by privacy policy" in await _chat_wide_tool(chat_id="@black_port")


@pytest.mark.asyncio
async def test_gate_resolves_names_to_find_limits_listed_by_id(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    entity = _forum(username="black_port")

    async def resolve(identifier, client=None):
        return entity

    monkeypatch.setattr(runtime, "resolve_entity", resolve)
    monkeypatch.setattr(runtime, "get_client", lambda account=None: object())

    assert "restricted by privacy policy" in await _chat_wide_tool(chat_id="black_port")
    assert await _topic_tool(chat_id="black_port") == "ran"


@pytest.mark.asyncio
async def test_real_tools_without_topic_support_are_refused(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")

    for result in (
        await messages.get_pinned_messages(chat_id=CHAT),
        await messages.send_message(chat_id=CHAT, message="hi"),
    ):
        assert "restricted by privacy policy" in result
        assert f"topic(s) {IT}" in result
    # get_participants reports refusals as MCP tool errors (raise_errors=True).
    with pytest.raises(ToolError, match=rf"only forum topic\(s\) {IT} are allowed"):
        await groups.get_participants(chat_id=CHAT)


# --- helpers -----------------------------------------------------------------------


def test_topic_helpers():
    creation = _msg(
        IT, topic=None, action=types.MessageActionTopicCreate(title="IT", icon_color=0)
    )
    inside, reply_inside = _msg(170, IT), _msg(171, IT, reply_to=170)
    other, general = _msg(172, OTHER), _msg(173, GENERAL)

    allowed = frozenset({IT})
    assert [
        m.id
        for m in messages.keep_allowed_topics(
            [creation, inside, reply_inside, other, general], allowed
        )
    ] == [IT, 170, 171]
    assert messages.in_allowed_topics(general, frozenset({GENERAL}))
    assert not messages.in_allowed_topics(None, allowed)
    assert messages.in_allowed_topics(other, None)

    assert messages.topic_thread_kwargs(None) == {}
    assert messages.topic_thread_kwargs(allowed) == {"reply_to": IT}
    assert messages.topic_thread_kwargs(frozenset({GENERAL})) == {}
    assert messages.topic_thread_kwargs(frozenset({IT, OTHER})) == {}


# --- reading tools -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_topics_shows_only_allowed_topics(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")

    requests = []

    class TopicsClient:
        async def __call__(self, request):
            requests.append(request)
            return SimpleNamespace(
                topics=[
                    SimpleNamespace(id=IT, title="IT"),
                    SimpleNamespace(id=OTHER, title="Bank"),
                ],
                messages=[],
            )

    _patch_tools(monkeypatch, chats, TopicsClient())
    result = json.loads(await chats.list_topics(chat_id=CHAT, limit=1))
    assert [topic["title"] for topic in result["results"]] == ["IT"]
    # Asked for by id, so a page of other topics cannot hide the allowed one.
    assert isinstance(requests[-1], functions.messages.GetForumTopicsByIDRequest)
    assert requests[-1].topics == [IT]
    assert "No topics found" in await chats.list_topics(chat_id=CHAT, search_query="bank")


@pytest.mark.asyncio
async def test_get_chat_shows_only_an_allowed_last_message(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},{CHAT}:5")

    class ChatClient(FakeForumClient):
        async def get_participants(self, entity, limit=0):
            return SimpleNamespace(total=9)

        async def get_input_entity(self, entity):
            return entity

        async def __call__(self, request):
            return SimpleNamespace(dialogs=[SimpleNamespace(unread_count=3, folder_id=0)])

    client = ChatClient([_msg(11, OTHER, text="bank secret")])
    _patch_tools(monkeypatch, chats, client)
    assert "last_message" not in json.loads(await chats.get_chat(chat_id=CHAT))

    # The newest message is in another topic: the allowed topics' newest is shown.
    client.msgs = [_msg(10, IT, text="it news"), _msg(11, OTHER, text="bank secret")]
    assert json.loads(await chats.get_chat(chat_id=CHAT))["last_message"]["text"] == "it news"


@pytest.mark.asyncio
async def test_get_history_reads_the_allowed_topic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    client = FakeForumClient([_msg(30, IT), _msg(31, OTHER), _msg(32, IT)])
    _patch_tools(monkeypatch, messages, client)

    assert _texts(await messages.get_history(chat_id=CHAT)) == ["text of 32", "text of 30"]
    assert client.calls[-1]["reply_to"] == IT

    refused = await messages.get_history(chat_id=CHAT, topic_id=OTHER)
    assert "restricted by privacy policy" in refused and f"Forum topic {OTHER}" in refused


@pytest.mark.asyncio
async def test_get_history_asks_for_a_topic_when_several_are_allowed(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},{CHAT}:5")
    _patch_tools(monkeypatch, messages, FakeForumClient([_msg(30, IT)]))

    assert "pass one of them as topic_id" in await messages.get_history(chat_id=CHAT)
    assert _texts(await messages.get_history(chat_id=CHAT, topic_id=IT)) == ["text of 30"]


@pytest.mark.asyncio
async def test_get_history_drops_other_topics_even_if_telegram_returns_them(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")

    class LeakyThreadClient(FakeForumClient):
        def _select(self, kwargs):  # ignores reply_to
            return sorted(self.msgs, key=lambda m: m.id, reverse=True)

    _patch_tools(monkeypatch, messages, LeakyThreadClient([_msg(30, IT), _msg(31, OTHER)]))
    assert _texts(await messages.get_history(chat_id=CHAT)) == ["text of 30"]


@pytest.mark.asyncio
async def test_get_messages_pages_the_allowed_topic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    client = FakeForumClient([_msg(40, IT, text="it task"), _msg(41, OTHER, text="bank secret")])
    _patch_tools(monkeypatch, messages, client)

    result = await messages.get_messages(chat_id=CHAT)
    assert "it task" in result and "bank secret" not in result
    assert client.calls[-1]["reply_to"] == IT


@pytest.mark.asyncio
async def test_list_messages_keeps_to_the_allowed_topics(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},{CHAT}:{GENERAL}")
    client = FakeForumClient([_msg(50, IT), _msg(51, OTHER), _msg(52, GENERAL)])
    _patch_tools(monkeypatch, messages, client)

    assert _texts(await messages.list_messages(chat_id=CHAT)) == ["text of 52", "text of 50"]
    assert _texts(await messages.list_messages(chat_id=CHAT, search_query="text")) == [
        "text of 52",
        "text of 50",
    ]
    assert _texts(await messages.list_messages(chat_id=CHAT, from_date="2026-11-01")) == [
        "text of 50",
        "text of 52",
    ]
    assert _texts(await messages.list_messages(chat_id=CHAT, to_date="2026-11-30")) == [
        "text of 52",
        "text of 50",
    ]


@pytest.mark.asyncio
async def test_search_messages_keeps_to_the_allowed_topic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    client = FakeForumClient([_msg(60, IT), _msg(61, OTHER), _msg(62, IT), _msg(63, IT)])
    _patch_tools(monkeypatch, messages, client)

    assert _texts(await messages.search_messages(chat_id=CHAT, query="text", limit=2)) == [
        "text of 63",
        "text of 62",
    ]


@pytest.mark.asyncio
async def test_get_message_context_keeps_to_the_allowed_topic(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    msgs = [_msg(70, IT), _msg(71, OTHER), _msg(72, IT, reply_to=71), _msg(73, OTHER)]
    _patch_tools(monkeypatch, messages, FakeForumClient(msgs))

    result = json.loads(await messages.get_message_context(chat_id=CHAT, message_id=72))
    assert [record["id"] for record in result["results"]] == [70, 72]
    assert all("replied_message" not in record for record in result["results"])

    refused = await messages.get_message_context(chat_id=CHAT, message_id=71)
    assert "restricted by privacy policy" in refused and "Message 71" in refused


@pytest.mark.asyncio
async def test_transcribe_voice_checks_the_topic_before_the_cache(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    _patch_tools(monkeypatch, messages, FakeForumClient([_msg(80, OTHER)]))
    monkeypatch.setattr(
        transcription,
        "get_cached_transcript",
        lambda *args, **kwargs: {"text": "cached secret", "source": "groq", "duration": 3},
    )

    result = await messages.transcribe_voice(chat_id=CHAT, message_id=80)
    assert "restricted by privacy policy" in result
    assert "cached secret" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "call",
    [
        lambda: media.open_photo(chat_id=CHAT, message_id=90),
        lambda: media.inspect_document(chat_id=CHAT, message_id=90),
        lambda: media.get_media_info(chat_id=CHAT, message_id=90),
    ],
)
async def test_media_tools_refuse_messages_of_other_topics(monkeypatch, call):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    photo = _msg(90, OTHER, media_obj=types.MessageMediaPhoto())
    _patch_tools(monkeypatch, media, FakeForumClient([photo]))

    result = await call()
    assert "restricted by privacy policy" in result and "Message 90" in result


@pytest.mark.asyncio
async def test_get_messages_after_filters_and_moves_the_cursor_past_dropped_ones(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT},{CHAT}:5")
    client = FakeForumClient([_msg(100, IT), _msg(101, OTHER), _msg(102, OTHER)])
    _patch_tools(monkeypatch, events, client)

    payload = json.loads(await events.get_messages_after(chat_id=CHAT, after_id=99, limit=3))
    assert [record["id"] for record in payload["messages"]] == [100]
    assert payload["next_after_id"] == 102
    assert payload["has_more"] is True


# --- tools that read many chats ---------------------------------------------------------


@pytest.mark.asyncio
async def test_search_global_drops_other_topics_of_a_limited_chat(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    forum = _forum()
    found = [_msg(110, IT, text="plan in IT"), _msg(111, OTHER, text="plan in Bank")]
    for msg in found:
        msg._finish_init = lambda *args: None
        msg.chat = forum

    class GlobalClient:
        async def __call__(self, request):
            return types.messages.Messages(messages=found, chats=[forum], users=[], topics=[])

    async def connected(client):
        pass

    monkeypatch.setattr(messages, "get_client", lambda account=None: GlobalClient())
    monkeypatch.setattr(messages, "ensure_connected", connected)
    monkeypatch.setattr(messages, "get_custom_emoji_metadata", lambda msg: {})

    assert _texts(await messages.search_global(query="plan")) == ["plan in IT"]


@pytest.mark.asyncio
async def test_get_drafts_drops_drafts_of_other_topics(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")

    def update(topic, text):
        return SimpleNamespace(
            peer=types.PeerChannel(CHANNEL_ID),
            top_msg_id=topic,
            draft=SimpleNamespace(
                message=text, date=None, no_webpage=False, reply_to=None, entities=None
            ),
        )

    class DraftsClient:
        async def __call__(self, request):
            return SimpleNamespace(
                updates=[update(IT, "it draft"), update(OTHER, "bank draft"), update(None, "x")],
                users=[],
                chats=[_forum()],
            )

    async def connected(client):
        pass

    monkeypatch.setattr(messages, "get_client", lambda account=None: DraftsClient())
    monkeypatch.setattr(messages, "ensure_connected", connected)
    monkeypatch.setattr(messages, "get_custom_emoji_metadata", lambda draft: {})

    drafts = json.loads(await messages.get_drafts())["drafts"]
    assert [draft["message"] for draft in drafts] == ["it draft"]


@pytest.mark.asyncio
async def test_export_chat_history_refuses_a_topic_limited_chat(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [tmp_path.resolve()])
    _patch_tools(monkeypatch, messages, FakeForumClient([_msg(120, OTHER)]))

    result = json.loads(
        await messages.export_chat_history(chat_id=CHAT, output_path=str(tmp_path / "out.json"))
    )
    assert result["messages_exported"] == 0
    exported = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert "export_chat_history cannot be limited" in exported["chats"][str(CHAT)]["error"]


# --- incoming events -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_log_skips_messages_of_other_topics(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", f"{CHAT}:{IT}")
    monkeypatch.setattr(events, "_event_log", deque(maxlen=50))
    monkeypatch.setattr(events, "_event_seq", 0)
    monkeypatch.setattr(events, "_event_signal", None)

    def event(msg_id, topic):
        msg = _msg(msg_id, topic)

        async def get_chat():
            return _forum()

        return SimpleNamespace(
            chat=None,
            sender=types.User(id=42, first_name="Ada"),
            is_private=False,
            is_group=True,
            is_channel=True,
            chat_id=CHAT,
            message=msg,
            get_chat=get_chat,
        )

    await events._on_message_event(event(130, OTHER))
    await events._on_message_event(event(131, IT))
    assert [(rec["message_id"], rec["topic"]) for rec in events._event_log] == [(131, IT)]
