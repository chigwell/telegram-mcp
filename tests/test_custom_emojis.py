"""Custom emoji discovery and reuse without Telegram connections."""

import json
from datetime import datetime, timezone
from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.extensions import html
from telethon.tl import types

from telegram_mcp import runtime
from telegram_mcp.tools import messages

EMOJI_ID = 5368324170671202286
EMOJI = {"emoji": "🍷", "id": str(EMOJI_ID)}
DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def _message(text="🍷 News", entities=None, **kwargs):
    if entities is None:
        entities = [types.MessageEntityCustomEmoji(0, 2, EMOJI_ID)]
    return types.Message(
        id=1,
        peer_id=types.PeerUser(42),
        date=DATE,
        message=text,
        entities=entities,
        **kwargs,
    )


@pytest.mark.parametrize("msg", [SimpleNamespace(), _message(entities=[]), _message(text="")])
def test_messages_without_custom_emoji_have_no_metadata(msg):
    assert messages.get_custom_emoji_metadata(msg) == {}


def test_distinct_ids_are_preserved_and_repetitions_are_deduplicated():
    msg = _message(
        "🍷 🍷 🍷",
        [
            types.MessageEntityBold(0, 8),
            types.MessageEntityCustomEmoji(0, 2, EMOJI_ID),
            types.MessageEntityCustomEmoji(3, 2, EMOJI_ID + 1),
            types.MessageEntityCustomEmoji(6, 2, EMOJI_ID),
        ],
    )
    result = messages.message_to_dict(msg)
    assert result["text"] == msg.message
    assert result["custom_emojis"] == [EMOJI, {"emoji": "🍷", "id": str(EMOJI_ID + 1)}]
    assert "html" not in result


@pytest.mark.parametrize(
    "emoji",
    [
        "🍷",
        "❗️",
        "👩🏽\u200d💻",
        "🇺🇳",
        "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
    ],
)
def test_original_utf16_offsets_and_compound_fallback_are_preserved(emoji):
    prefix = "😀\u200b News: "
    msg = _message(
        prefix + emoji,
        [types.MessageEntityCustomEmoji(_utf16_length(prefix), _utf16_length(emoji), EMOJI_ID)],
    )
    result = messages.message_to_dict(msg)
    assert result["custom_emojis"] == [{"emoji": emoji, "id": str(EMOJI_ID)}]
    assert "\u200b" not in result["text"]


def test_fallback_is_sanitized_and_text_output_stays_on_one_line():
    text = '\x00\u202e🍷\n"'
    msg = _message(text, [types.MessageEntityCustomEmoji(0, _utf16_length(text), EMOJI_ID)])
    line = messages.format_message_line(msg)
    metadata = messages.get_custom_emoji_metadata(msg)["custom_emojis"]
    assert metadata == [{"emoji": '🍷\n"', "id": str(EMOJI_ID)}]
    assert "\n" not in line
    assert "\x00" not in line
    assert "\u202e" not in line
    assert json.dumps(metadata, ensure_ascii=False) in line


def test_rich_emoji_are_found_in_nested_captions_lists_and_table_cells():
    emoji = types.TextCustomEmoji(document_id=EMOJI_ID, alt="\u202e👩🏽\u200d💻")
    rich = types.RichMessage(
        blocks=[
            types.PageBlockDetails(
                title=types.TextEmpty(),
                blocks=[
                    types.PageBlockPhoto(
                        photo_id=1,
                        caption=types.PageCaption(
                            text=types.TextBold(text=emoji), credit=types.TextEmpty()
                        ),
                    ),
                    types.PageBlockList(items=[types.PageListItemText(text=emoji)]),
                    types.PageBlockTable(
                        title=types.TextEmpty(),
                        rows=[
                            types.PageTableRow(
                                cells=[
                                    types.PageTableCell(
                                        text=types.TextConcat(
                                            texts=[
                                                emoji,
                                                types.TextCustomEmoji(
                                                    document_id=EMOJI_ID + 1, alt="👩🏽\u200d💻"
                                                ),
                                            ]
                                        )
                                    )
                                ]
                            )
                        ],
                    ),
                ],
            )
        ],
        photos=[],
        documents=[],
    )
    result = messages.get_custom_emoji_metadata(_message("", [], rich_message=rich))
    assert result["custom_emojis"] == [
        {"emoji": "👩🏽\u200d💻", "id": str(EMOJI_ID)},
        {"emoji": "👩🏽\u200d💻", "id": str(EMOJI_ID + 1)},
    ]


def test_plain_and_rich_emoji_share_deduplication():
    rich = types.RichMessage(
        blocks=[
            types.PageBlockParagraph(text=types.TextCustomEmoji(document_id=EMOJI_ID, alt="🍷"))
        ],
        photos=[],
        documents=[],
    )
    assert messages.get_custom_emoji_metadata(_message(rich_message=rich)) == {
        "custom_emojis": [EMOJI]
    }


def test_rich_content_without_custom_emoji_has_no_metadata():
    rich = types.RichMessage(
        blocks=[
            SimpleNamespace(),
            types.PageBlockParagraph(text=types.TextPlain(text="News")),
            types.PageBlockParagraph(text=types.TextImage(document_id=EMOJI_ID, w=10, h=10)),
        ],
        photos=[],
        documents=[],
    )
    assert messages.get_custom_emoji_metadata(_message("", [], rich_message=rich)) == {}


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    cl.get_messages.return_value = [_message()]
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=types.User(id=42)))
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [True, False])
@pytest.mark.parametrize(
    "tool, arguments",
    [
        (messages.get_history, {"chat_id": 42}),
        (messages.list_messages, {"chat_id": 42}),
        (messages.search_messages, {"chat_id": 42, "query": "News"}),
        (messages.search_global, {"query": "News"}),
        (messages.get_pinned_messages, {"chat_id": 42}),
        (messages.get_message_context, {"chat_id": 42, "message_id": 1}),
    ],
)
async def test_json_readers_expose_optional_metadata(client, tool, arguments, custom):
    msg = _message(entities=None if custom else [])
    client.get_messages.return_value = [msg]
    if tool is messages.get_message_context:
        client.get_messages.side_effect = [[], msg, []]
    result = json.loads(await tool(**arguments, account="test"))
    record = result["results"][0]
    assert record["text"] == "🍷 News"
    assert record["id"] == 1
    if custom:
        assert record["custom_emojis"] == [EMOJI]
    else:
        assert "custom_emojis" not in record
    assert client.get_messages.await_count == (3 if tool is messages.get_message_context else 1)
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_includes_replied_message_metadata(client):
    reply = _message("Reply", [], reply_to=types.MessageReplyHeader(reply_to_msg_id=2))
    client.get_messages.side_effect = [[], reply, [], _message()]
    result = json.loads(await messages.get_message_context(42, 1, account="test"))
    assert "custom_emojis" not in result["results"][0]
    assert result["results"][0]["replied_message"]["custom_emojis"] == [EMOJI]


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [True, False])
@pytest.mark.parametrize("tool", [messages.get_messages, messages.get_scheduled_messages])
async def test_text_readers_expose_optional_metadata(client, tool, custom):
    msg = _message(entities=None if custom else [])
    client.get_messages.return_value = [msg]
    client.return_value = SimpleNamespace(messages=[msg])
    result = await tool(42, account="test")
    assert "🍷 News" in result
    if custom:
        assert "custom_emojis: " + json.dumps([EMOJI], ensure_ascii=False) in result
    else:
        assert "custom_emojis" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("custom", [True, False])
async def test_drafts_expose_optional_metadata(client, custom):
    draft = types.DraftMessage(
        message="🍷 News", date=DATE, entities=_message().entities if custom else []
    )
    client.return_value = SimpleNamespace(
        updates=[SimpleNamespace(peer=types.PeerUser(42), draft=draft)]
    )
    result = json.loads(await messages.get_drafts(account="test"))
    record = result["drafts"][0]
    assert record["message"] == "🍷 News"
    if custom:
        assert record["custom_emojis"] == [EMOJI]
    else:
        assert "custom_emojis" not in record


@pytest.mark.asyncio
@pytest.mark.parametrize("rich", [False, True])
@pytest.mark.parametrize(
    "tool, text_arg, arguments, method",
    [
        (messages.send_message, "message", {}, "send_message"),
        (messages.reply_to_message, "text", {"message_id": 1}, "send_message"),
        (messages.edit_message, "new_text", {"message_id": 1}, "edit_message"),
    ],
)
async def test_discovered_emoji_can_be_reused_by_existing_html_path(
    client, tool, text_arg, arguments, method, rich
):
    msg = _message()
    if rich:
        msg = _message(
            "",
            [],
            rich_message=types.RichMessage(
                blocks=[
                    types.PageBlockHeading1(
                        text=types.TextCustomEmoji(document_id=EMOJI_ID, alt="🍷")
                    )
                ],
                photos=[],
                documents=[],
            ),
        )
    emoji = messages.get_custom_emoji_metadata(msg)["custom_emojis"][0]
    markup = f'New <tg-emoji emoji-id="{emoji["id"]}">{escape(emoji["emoji"])}</tg-emoji>'
    result = await tool(
        chat_id=42, **{text_arg: markup}, **arguments, parse_mode="html", account="test"
    )
    assert "error" not in result.lower()
    call = getattr(client, method).await_args
    assert call.kwargs["parse_mode"] == "html"
    if tool is messages.reply_to_message:
        assert call.kwargs["reply_to"] == 1
    text, entities = html.parse(call.args[-1])
    assert text == "New 🍷"
    assert len(entities) == 1
    assert isinstance(entities[0], types.MessageEntityCustomEmoji)
    assert entities[0].document_id == EMOJI_ID
    assert (entities[0].offset, entities[0].length) == (4, 2)


@pytest.mark.parametrize("name", ["send_message", "reply_to_message", "edit_message"])
def test_mcp_descriptions_explain_how_to_reuse_custom_emoji(name):
    description = runtime.mcp._tool_manager.get_tool(name).description
    assert "custom_emojis" in description
    assert '<tg-emoji emoji-id="ID">EMOJI</tg-emoji>' in description
    assert "parse_mode='html'" in description
