"""Tests for reply-quote extraction (reply to a selected part of a message)."""

from datetime import datetime, timezone
from types import SimpleNamespace

from telegram_mcp.tools.messages import (
    get_reply_quote,
    message_to_dict,
    format_message_line,
)


def _reply(**overrides):
    """Fake Telethon MessageReplyHeader."""
    base = {
        "reply_to_msg_id": 100,
        "quote_text": None,
        "quote_offset": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _msg(**overrides):
    """Minimal fake Telethon Message; getattr defaults cover unset media/flags."""
    base = {
        "id": 200,
        "sender": None,
        "sender_id": 42,
        "date": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "message": "the reply body",
        "reply_to": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_get_reply_quote_returns_text_and_offset():
    msg = _msg(reply_to=_reply(quote_text="the important part", quote_offset=15))
    assert get_reply_quote(msg) == {"text": "the important part", "offset": 15}


def test_get_reply_quote_none_for_plain_reply():
    # A whole-message reply carries reply_to_msg_id but no quote_text.
    msg = _msg(reply_to=_reply())
    assert get_reply_quote(msg) is None


def test_get_reply_quote_none_when_not_a_reply():
    assert get_reply_quote(_msg(reply_to=None)) is None


def test_get_reply_quote_offset_zero_is_kept():
    # offset 0 is a valid position (quote at the very start), must not be dropped.
    msg = _msg(reply_to=_reply(quote_text="start span", quote_offset=0))
    assert get_reply_quote(msg) == {"text": "start span", "offset": 0}


def test_get_reply_quote_without_offset():
    msg = _msg(reply_to=_reply(quote_text="span", quote_offset=None))
    assert get_reply_quote(msg) == {"text": "span"}


def test_get_reply_quote_survives_cross_chat_reply_without_msg_id():
    # Cross-chat quote reply: reply_to_msg_id may be absent, quote still present.
    msg = _msg(reply_to=_reply(reply_to_msg_id=None, quote_text="from elsewhere", quote_offset=3))
    assert get_reply_quote(msg) == {"text": "from elsewhere", "offset": 3}


def test_message_to_dict_includes_reply_quote():
    msg = _msg(reply_to=_reply(quote_text="quoted span", quote_offset=7))
    d = message_to_dict(msg)
    assert d["reply_to"] == 100
    assert d["reply_quote"] == {"text": "quoted span", "offset": 7}


def test_message_to_dict_omits_reply_quote_for_plain_reply():
    d = message_to_dict(_msg(reply_to=_reply()))
    assert d["reply_to"] == 100
    assert "reply_quote" not in d


def test_format_message_line_shows_quote_preview():
    msg = _msg(reply_to=_reply(quote_text="a short quoted span", quote_offset=2))
    line = format_message_line(msg)
    assert "reply to 100" in line
    assert 'quoting "a short quoted span"' in line


def test_format_message_line_truncates_long_quote_and_flattens_newlines():
    long_quote = "x" * 80 + "\nmore"
    msg = _msg(reply_to=_reply(quote_text=long_quote, quote_offset=0))
    line = format_message_line(msg)
    assert "\n" not in line.split("quoting", 1)[1]
    assert "…" in line
