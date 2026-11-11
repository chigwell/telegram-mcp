"""Links in message entities (text URLs, explicit URLs, name mentions) as `links`."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.tl import types

from telegram_mcp import runtime
from telegram_mcp.tools import contacts, messages

DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
URL = "https://t.me/example_support"
LINK = {"text": "support account", "url": URL}
TEXT = "Questions? Write to our support account today"


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


def _span(text, part):
    start = text.index(part)
    return _utf16(text[:start]), _utf16(part)


def _message(text=TEXT, entities=None, **kwargs):
    return types.Message(
        id=1, peer_id=types.PeerUser(42), date=DATE, message=text, entities=entities, **kwargs
    )


def _text_url_message(text=TEXT, part="support account", url=URL, **kwargs):
    return _message(text, [types.MessageEntityTextUrl(*_span(text, part), url)], **kwargs)


@pytest.mark.parametrize(
    "msg",
    [
        SimpleNamespace(),
        _message(entities=None),
        _message(entities=[]),
        _message("", [types.MessageEntityTextUrl(0, 3, URL)]),
        _message(entities=[types.MessageEntityBold(0, 5), types.MessageEntityMention(0, 3)]),
    ],
)
def test_messages_without_links_have_no_metadata(msg):
    assert runtime.get_link_metadata(msg) == {}


def test_text_url_is_paired_with_its_visible_text():
    assert runtime.get_link_metadata(_text_url_message()) == {"links": [LINK]}


def test_explicit_url_uses_the_text_itself():
    text = "Docs: https://example.com/a?b=1 end"
    msg = _message(text, [types.MessageEntityUrl(*_span(text, "https://example.com/a?b=1"))])
    assert runtime.get_link_metadata(msg) == {
        "links": [{"text": "https://example.com/a?b=1", "url": "https://example.com/a?b=1"}]
    }


def test_mention_name_entities_expose_user_id():
    text = "Ask Anna or Boris"
    msg = _message(
        text,
        [
            types.MessageEntityMentionName(*_span(text, "Anna"), user_id=111),
            types.InputMessageEntityMentionName(
                *_span(text, "Boris"), user_id=types.InputUser(222, 0)
            ),
        ],
    )
    assert runtime.get_link_metadata(msg) == {
        "links": [{"text": "Anna", "user_id": 111}, {"text": "Boris", "user_id": 222}]
    }


@pytest.mark.parametrize(
    "prefix", ["", "🍷 ", "👩🏽‍💻 x ", "🇺🇳🇺🇳 ", "𝔘𝔫𝔦𝔠𝔬𝔡𝔢 ", "a​b ", "Привет, "]
)
def test_utf16_offsets_after_astral_characters(prefix):
    text = prefix + "write to our support account, ok 🙂"
    assert runtime.get_link_metadata(_text_url_message(text)) == {"links": [LINK]}


def test_order_is_kept_and_duplicates_are_dropped():
    text = "🔗 first 💬 second first"
    entities = [
        types.MessageEntityBold(0, _utf16(text)),
        types.MessageEntityTextUrl(*_span(text, "first"), "https://a.example"),
        types.MessageEntityTextUrl(*_span(text, "💬 second"), "https://b.example"),
        types.MessageEntityTextUrl(_utf16(text) - 5, 5, "https://a.example"),
    ]
    assert runtime.get_link_metadata(_message(text, entities))["links"] == [
        {"text": "first", "url": "https://a.example"},
        {"text": "💬 second", "url": "https://b.example"},
    ]


def test_link_fields_are_sanitized():
    text = "see‮ here"
    msg = _message(text, [types.MessageEntityTextUrl(0, _utf16(text), "https://x.example/​p")])
    assert runtime.get_link_metadata(msg) == {
        "links": [{"text": "see here", "url": "https://x.example/p"}]
    }


def test_message_to_dict_and_line_include_links():
    msg = _text_url_message()
    d = messages.message_to_dict(msg)
    assert d["links"] == [LINK]
    assert d["link_urls"] == [URL]  # unchanged, kept for compatibility
    assert d["text"] == TEXT
    assert "links" not in messages.message_to_dict(_message(entities=[]))
    line = messages.format_message_line(msg)
    assert "links: " + json.dumps([LINK], ensure_ascii=False) in line
    assert "links:" not in messages.format_message_line(_message(entities=[]))


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    cl._mb_entity_cache = MagicMock()
    cl._mb_entity_cache.get.return_value = None
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=types.User(id=42)))
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [True, False])
@pytest.mark.parametrize(
    "tool, arguments",
    [
        (messages.get_history, {"chat_id": 42}),
        (messages.list_messages, {"chat_id": 42}),
        (messages.search_messages, {"chat_id": 42, "query": "support"}),
        (messages.search_global, {"query": "support"}),
        (messages.get_pinned_messages, {"chat_id": 42}),
        (messages.get_message_context, {"chat_id": 42, "message_id": 1}),
    ],
)
async def test_json_readers_expose_links(client, tool, arguments, linked):
    msg = _text_url_message() if linked else _message(entities=[])
    client.get_messages.return_value = [msg]
    if tool is messages.search_global:
        client.return_value = SimpleNamespace(messages=[msg], users=[types.User(id=42)], chats=[])
    if tool is messages.get_message_context:
        client.get_messages.side_effect = [[], msg, []]
    record = json.loads(await tool(**arguments, account="test"))["results"][0]
    assert record["text"] == TEXT
    if linked:
        assert record["links"] == [LINK]
    else:
        assert "links" not in record


@pytest.mark.asyncio
async def test_context_replied_message_has_links(client):
    reply = _message("Reply", [], reply_to=types.MessageReplyHeader(reply_to_msg_id=2))
    client.get_messages.side_effect = [[], reply, [], _text_url_message()]
    record = json.loads(await messages.get_message_context(42, 1, account="test"))["results"][0]
    assert "links" not in record
    assert record["replied_message"]["links"] == [LINK]


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [True, False])
@pytest.mark.parametrize("tool", [messages.get_messages, messages.get_scheduled_messages])
async def test_text_readers_expose_links(client, tool, linked):
    msg = _text_url_message() if linked else _message(entities=[])
    client.get_messages.return_value = [msg]
    client.return_value = SimpleNamespace(messages=[msg])
    result = await tool(42, account="test")
    if linked:
        assert "links: " + json.dumps([LINK], ensure_ascii=False) in result
    else:
        assert "links: " not in result


@pytest.mark.asyncio
async def test_drafts_expose_links(client):
    draft = types.DraftMessage(message=TEXT, date=DATE, entities=_text_url_message().entities)
    client.return_value = SimpleNamespace(
        updates=[SimpleNamespace(peer=types.PeerUser(42), draft=draft)]
    )
    record = json.loads(await messages.get_drafts(account="test"))["drafts"][0]
    assert record["links"] == [LINK]


@pytest.mark.asyncio
async def test_last_interaction_exposes_links(monkeypatch):
    cl = AsyncMock()
    cl.get_messages.return_value = [_text_url_message()]
    monkeypatch.setattr(contacts, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        contacts, "resolve_entity", AsyncMock(return_value=types.User(id=42, first_name="Bot"))
    )
    result = json.loads(await contacts.get_last_interaction(42, account="test"))
    assert result["results"][0]["links"] == [LINK]


@pytest.mark.parametrize(
    "name",
    [
        "list_messages",
        "get_messages",
        "get_message_context",
        "search_messages",
        "search_global",
        "get_history",
        "get_pinned_messages",
    ],
)
def test_reader_descriptions_mention_links(name):
    assert "links" in runtime.mcp._tool_manager.get_tool(name).description
