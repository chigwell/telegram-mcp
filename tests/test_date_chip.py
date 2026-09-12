"""Formatted date chips on outgoing plain-text messages, without Telegram connections."""

from unittest.mock import AsyncMock

import pytest
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import messages


def _utf16_length(text):
    return len(text.encode("utf-16-le")) // 2


def _chip(text, format_date):
    entity, error = messages._date_entity(text, format_date)
    assert error is None
    return entity


def _chip_error(text, format_date):
    entity, error = messages._date_entity(text, format_date)
    assert entity is None
    return error


@pytest.mark.parametrize(
    "message, format_date, flags",
    [
        ("Lunch 13/09 17:00", "13/09 17:00", {"short_date", "short_time"}),
        ("13/09", "13/09", {"short_date"}),
        ("13/09/2026 11:00", "13/09/2026 11:00", {"long_date", "short_time"}),
        ("13/09/2026", "13/09/2026", {"long_date"}),
        ("1/2 3:45", "1/2 3:45", {"short_date", "short_time"}),
    ],
)
def test_date_entity_maps_flags_and_local_fields(message, format_date, flags):
    entity = _chip(message, format_date)
    assert isinstance(entity, types.MessageEntityFormattedDate)
    prefix = message[: message.find(format_date)]
    assert (entity.offset, entity.length) == (_utf16_length(prefix), _utf16_length(format_date))
    date = entity.date
    assert date.tzinfo is not None
    tokens = format_date.split()
    parts = tokens[0].split("/")
    assert date.day == int(parts[0])
    assert date.month == int(parts[1])
    if len(parts) == 3:
        assert date.year == int(parts[2])
    if len(tokens) == 2:
        clock = tokens[1].split(":")
        assert date.hour == int(clock[0])
        assert date.minute == int(clock[1])
    assert {
        flag for flag in ("short_date", "long_date", "short_time") if getattr(entity, flag)
    } == flags


def test_offsets_are_utf16_code_units():
    message = "😀 13/09 17:00"
    format_date = "13/09 17:00"
    entity = _chip(message, format_date)
    prefix = message[: -len(format_date)]
    assert entity.offset == _utf16_length(prefix)
    assert entity.length == _utf16_length(format_date)


@pytest.mark.parametrize(
    "message, format_date",
    [
        ("Lunch tomorrow", "13/09"),
        ("Lunch 13/09 17:00", "14/09 17:00"),
        ("Lunch foo", "13/09 17:00"),
    ],
)
def test_missing_format_date_is_reported(message, format_date):
    assert _chip_error(message, format_date).startswith("format_date")


@pytest.mark.parametrize(
    "format_date",
    [
        "13",
        "13/09/2036/1",
        "13/09 foo",
        "13/09 17:00:00",
        "32/01 17:00",
        "13/13",
        "13/09 25:00",
        "13/09 17:61",
    ],
)
def test_malformed_format_date_is_reported(format_date):
    error = _chip_error("Lunch " + format_date, format_date)
    assert "valid date" in error or "must look like" in error


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=types.User(id=42)))
    return cl


@pytest.mark.asyncio
async def test_send_message_attaches_chip_entity(client):
    text = "TODO 13/09 17:00"
    result = await messages.send_message(42, text, format_date="13/09 17:00", account="test")
    assert "successfully" in result
    req = client.await_args.args[0]
    assert isinstance(req, functions.messages.SendMessageRequest)
    assert req.message == text
    assert len(req.entities) == 1
    assert isinstance(req.entities[0], types.MessageEntityFormattedDate)
    assert (req.entities[0].offset, req.entities[0].length) == (5, _utf16_length("13/09 17:00"))


@pytest.mark.asyncio
async def test_send_message_without_chip_keeps_telethon_path(client):
    await messages.send_message(42, "plain", account="test")
    assert client.send_message.await_count == 1
    assert client.await_args is None


@pytest.mark.asyncio
async def test_reply_to_message_attaches_chip_entity(client):
    result = await messages.reply_to_message(
        42, 7, "At 13/09 17:00 ok", format_date="13/09 17:00", account="test"
    )
    assert "Replied" in result
    req = client.await_args.args[0]
    assert isinstance(req, functions.messages.SendMessageRequest)
    assert isinstance(req.reply_to, types.InputReplyToMessage)
    assert req.reply_to.reply_to_msg_id == 7
    assert isinstance(req.entities[0], types.MessageEntityFormattedDate)


@pytest.mark.asyncio
async def test_edit_message_attaches_chip_entity(client):
    result = await messages.edit_message(
        42, 7, "Due 13/09 17:00", format_date="13/09 17:00", account="test"
    )
    assert "edited" in result
    req = client.await_args.args[0]
    assert isinstance(req, functions.messages.EditMessageRequest)
    assert req.id == 7
    assert isinstance(req.entities[0], types.MessageEntityFormattedDate)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, text_arg",
    [
        (messages.send_message, "message"),
        (messages.reply_to_message, "text"),
        (messages.edit_message, "new_text"),
    ],
)
async def test_chip_conflicts_with_parse_mode(client, tool, text_arg):
    kwargs = {"message_id": 7} if tool is not messages.send_message else {}
    result = await tool(
        42,
        **{text_arg: "13/09 17:00"},
        format_date="13/09 17:00",
        parse_mode="html",
        account="test",
        **kwargs,
    )
    assert "plain-text" in result
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_chip_text_returns_error(client):
    result = await messages.send_message(42, "Lunch", format_date="13/09 17:00", account="test")
    assert result.startswith("format_date")
    client.assert_not_awaited()


@pytest.mark.parametrize("name", ["send_message", "reply_to_message", "edit_message"])
def test_mcp_descriptions_explain_format_date(name):
    description = runtime.mcp._tool_manager.get_tool(name).description
    assert "format_date" in description
    assert "'13/09', '13/09/2026', or '13/09 17:00'" in description
