"""custom_emojis on outgoing plain-text messages, without Telegram connections."""

from unittest.mock import AsyncMock

import pytest
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import messages

WINE_ID = 5368324170671202286
COFFEE_ID = 5368324170671202287
WINE = {"emoji": "🍷", "id": str(WINE_ID)}

FAMILY = "👨\u200d👩\u200d👧\u200d👦"
CODER = "👩\u200d💻"
SKIN_TONE = "👍🏽"
HEART = "❤\ufe0f"
KEYCAP = "1\ufe0f\u20e3"
SUBDIVISION_FLAG = "🏴\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
NOT_FOUND = "was not found as a standalone emoji"
BAD_ENTRY = "Each custom_emojis entry must be"
BAD_EMOJI = "must be the fallback emoji alone, without text or spaces."
BAD_ID = "must be a positive 64-bit integer (an int or a string of digits)."
OVERLAP = "overlap in the message text."


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


def _result(text, custom_emojis, format_date=None):
    return messages._plain_entities(text, format_date, custom_emojis)


def _spans(entities):
    return [(e.offset, e.length, getattr(e, "document_id", None)) for e in entities]


@pytest.mark.parametrize(
    "text, emoji, offsets, length",
    [
        ("News 🍷", "🍷", [5], 2),  # ASCII prefix, emoji not at the start
        ("🍷 one, 🍷 two 🍷", "🍷", [0, 8, 15], 2),  # every occurrence
        ("🍷🍷", "🍷", [0, 2], 2),  # adjacent occurrences
        ("🍷☕ x ☕", "☕", [2, 6], 1),  # BMP emoji after an astral one
        (f"a {HEART} b {HEART}", HEART, [2, 7], 2),  # BMP base + U+FE0F
        (f"a {FAMILY}", FAMILY, [2], 11),  # four astral people, three ZWJ
        (f"a {SKIN_TONE}", SKIN_TONE, [2], 4),
        ("a 🇺🇳", "🇺🇳", [2], 4),
        (f"a {SUBDIVISION_FLAG}", SUBDIVISION_FLAG, [2], 14),
    ],
)
def test_offsets_and_lengths_are_utf16(text, emoji, offsets, length):
    entities, error = _result(text, [{"emoji": emoji, "id": WINE_ID}])
    assert error is None
    assert all(isinstance(e, types.MessageEntityCustomEmoji) for e in entities)
    assert _spans(entities) == [(offset, length, WINE_ID) for offset in offsets]


@pytest.mark.parametrize(
    "text, emoji, offsets",
    [
        (f"{SKIN_TONE} and 👍", "👍", [_utf16(f"{SKIN_TONE} and ")]),  # skin tone
        (SKIN_TONE, "👍", []),
        (CODER, "👩", []),  # first half of a ZWJ sequence
        (CODER, "💻", []),  # second half
        (f"{FAMILY} 👨", "👨", [_utf16(f"{FAMILY} ")]),
        (f"{HEART} ❤", "❤", [_utf16(f"{HEART} ")]),  # VS16
        ("❤", HEART, []),
        ("🇺🇳🇺🇳", "🇺🇳", [0, 4]),  # even run of regional indicators
        ("🇦🇺🇸🇬 and 🇺🇸", "🇺🇸", [_utf16("🇦🇺🇸🇬 and ")]),  # odd run: inside AU+SG
        ("🇦🇺🇸🇬", "🇺🇸", []),
        (SUBDIVISION_FLAG, "🏴", []),  # tag characters
    ],
)
def test_emoji_inside_a_longer_sequence_is_skipped(text, emoji, offsets):
    entities, error = _result(text, [{"emoji": emoji, "id": WINE_ID}])
    if offsets:
        assert error is None
        assert [e.offset for e in entities] == offsets
    else:
        assert entities is None
        assert error == (
            f"custom_emojis emoji '{emoji}' {NOT_FOUND} in the message text "
            "(it may appear only inside a longer emoji such as 👍🏽)."
        )


def test_keycaps():
    text = f"{KEYCAP} 1 #\u20e3 and {KEYCAP}"
    entities, error = _result(
        text, [{"emoji": KEYCAP, "id": WINE_ID}, {"emoji": "#\u20e3", "id": COFFEE_ID}]
    )
    assert error is None
    assert _spans(entities) == [(0, 3, WINE_ID), (6, 2, COFFEE_ID), (13, 3, WINE_ID)]
    error = _result(text, [{"emoji": "1", "id": 1}])[1]
    assert "must be the fallback emoji alone" in error


@pytest.mark.parametrize("emoji", ["🇺", "🇺🇸🇺"])
def test_odd_regional_indicators_are_refused(emoji):
    # A lone 🇺 would otherwise mark half of the 🇺🇸 flag.
    entities, error = _result("🇺🇸 x", [{"emoji": emoji, "id": WINE_ID}])
    assert entities is None
    assert error.startswith(f"custom_emojis emoji {emoji!r} {BAD_EMOJI}")


def test_format_date_and_entries_are_sorted():
    text = "Tasting 13/09 17:00 🍷 at ☕"
    entities, error = _result(
        text, [{"emoji": "☕", "id": COFFEE_ID}, WINE], format_date="13/09 17:00"
    )
    assert error is None
    assert [type(e) for e in entities] == [
        types.MessageEntityFormattedDate,
        types.MessageEntityCustomEmoji,
        types.MessageEntityCustomEmoji,
    ]
    assert _spans(entities) == [(8, 11, None), (20, 2, WINE_ID), (26, 1, COFFEE_ID)]


@pytest.mark.parametrize(
    "text, spans", [("🍷13/09", [(0, 2), (2, 5)]), ("13/09🍷", [(0, 5), (5, 2)])]
)
def test_format_date_adjacent_to_custom_emoji_is_allowed(text, spans):
    entities, error = _result(text, [WINE], format_date="13/09")
    assert error is None
    assert [(e.offset, e.length) for e in entities] == spans


def test_format_date_overlapping_custom_emoji_is_refused(monkeypatch):
    # Valid fallbacks and dates share no characters, so force a chip over the emoji.
    chip = types.MessageEntityFormattedDate(offset=1, length=5, date=None)
    monkeypatch.setattr(messages, "_date_entity", lambda *args: (chip, None))
    assert _result("🍷 13/09", [WINE], format_date="13/09")[1] == (
        f"custom emoji '🍷' ({WINE_ID}) and format_date '13/09' {OVERLAP}"
    )


@pytest.mark.parametrize("emoji_id", [1, "007", 2**63 - 1, str(2**63 - 1)])
def test_valid_ids(emoji_id):
    entities, error = _result("🍷", [{"emoji": "🍷", "id": emoji_id}])
    assert error is None
    assert entities[0].document_id == int(emoji_id)


@pytest.mark.parametrize(
    "custom_emojis, expected",
    [
        ({"emoji": "🍷", "id": WINE_ID}, "custom_emojis must be a list of"),
        ("🍷", "custom_emojis must be a list of"),
        ([{"emoji": "🍷"}], BAD_ENTRY),
        ([{"id": WINE_ID}], BAD_ENTRY),
        ([{"emoji": "🍷", "id": WINE_ID, "extra": 1}], BAD_ENTRY),
        ([{"emoji": "🍷", "document_id": WINE_ID}], BAD_ENTRY),
        ([["🍷", WINE_ID]], BAD_ENTRY),
        *[
            ([{"emoji": emoji, "id": WINE_ID}], BAD_EMOJI)
            for emoji in ["", " ", "🍷 ", "N🍷", "News", "1", "中", "𝐀", "①", "\u3000"]
            + ["\u200d🍷", "🍷\u200d", "\ufe0f🍷", "🏽", None, 5]
            + ["#", "!", "-", "€", "©", "#!"]
        ],
        *[
            ([{"emoji": "🍷", "id": emoji_id}], BAD_ID)
            for emoji_id in [True, False, "1_000", "abc", "", " 5", "-5", "٣", "5.0"]
            + [0, -1, 1.5, None, 2**63, str(2**63)]
        ],
        ([WINE, dict(WINE)], OVERLAP),  # exact duplicate
        ([WINE, {"emoji": "🍷", "id": COFFEE_ID}], OVERLAP),
        ([WINE, {"emoji": "🍷🍷", "id": COFFEE_ID}], OVERLAP),
        ([{"emoji": "☕", "id": WINE_ID}], NOT_FOUND),
    ],
)
def test_refusals(custom_emojis, expected):
    entities, error = _result("🍷🍷", custom_emojis)
    assert entities is None
    assert expected in error


def test_overlap_names_the_fallbacks():
    error = _result("🍷🍷", [WINE, {"emoji": "🍷🍷", "id": COFFEE_ID}])[1]
    assert error == (
        f"custom emoji '🍷' ({WINE_ID}) and custom emoji '🍷🍷' ({COFFEE_ID}) "
        "overlap in the message text."
    )


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    resolve = AsyncMock(return_value=types.User(id=42))
    monkeypatch.setattr(messages, "resolve_entity", resolve)
    return cl


# Keyword arguments, as MCP passes them: validate_id only checks keyword chat_id.
async def _send(text, **kw):
    return await messages.send_message(chat_id=42, message=text, **kw)


async def _reply(text, **kw):
    return await messages.reply_to_message(chat_id=42, message_id=7, text=text, **kw)


async def _edit(text, **kw):
    return await messages.edit_message(chat_id=42, message_id=7, new_text=text, **kw)


TOOLS = {
    "send_message": (_send, "Message sent successfully."),
    "reply_to_message": (_reply, "Replied to message 7 in chat 42."),
    "edit_message": (_edit, "Message 7 edited."),
}


@pytest.fixture(params=list(TOOLS))
def tool(request):
    return request.param, *TOOLS[request.param]


def _assert_nothing_sent(cl):
    cl.assert_not_awaited()
    cl.send_message.assert_not_awaited()
    cl.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_sends_raw_text_with_entities(client, tool):
    name, call, expected = tool
    text = "🍷 List<int> x && &copy; 13/09 🍷"
    assert await call(text, custom_emojis=[WINE], format_date="13/09") == expected

    req = client.await_args.args[0]
    assert req.message == text
    assert _spans(req.entities) == [
        (0, 2, WINE_ID),
        (_utf16("🍷 List<int> x && &copy; "), 5, None),
        (_utf16("🍷 List<int> x && &copy; 13/09 "), 2, WINE_ID),
    ]
    if name == "edit_message":
        assert isinstance(req, functions.messages.EditMessageRequest) and req.id == 7
    else:
        assert isinstance(req, functions.messages.SendMessageRequest)
        reply_to = req.reply_to.reply_to_msg_id if req.reply_to else None
        assert reply_to == (7 if name == "reply_to_message" else None)
    client.send_message.assert_not_awaited()
    client.edit_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("parse_mode", ["html", "md", "rich", "rich_html"])
async def test_tool_refuses_parse_mode(client, tool, parse_mode):
    _, call, _ = tool
    result = await call("🍷", custom_emojis=[WINE], parse_mode=parse_mode)
    assert result == "custom_emojis needs plain-text messages (leave parse_mode unset)."
    _assert_nothing_sent(client)


@pytest.mark.asyncio
async def test_tool_checks_allowlist_before_custom_emojis(client, tool, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-100123456789")
    _, call, _ = tool
    bad = [{"emoji": "N", "id": True}]
    result = await call("News", custom_emojis=bad, parse_mode="html")
    assert "restricted by privacy policy" in result
    _assert_nothing_sent(client)


@pytest.mark.asyncio
@pytest.mark.parametrize("custom_emojis", [None, []])
async def test_tool_default_path(client, tool, custom_emojis):
    name, call, expected = tool
    assert await call("🍷 plain", custom_emojis=custom_emojis) == expected
    assert client.await_args is None
    assert client.send_message.await_count + client.edit_message.await_count == 1
    tool_schema = runtime.mcp._tool_manager.get_tool(name).parameters
    schema = tool_schema["properties"]["custom_emojis"]
    assert {s.get("type") for s in schema["anyOf"]} == {"array", "null"}
