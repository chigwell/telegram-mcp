"""edit_message reports an unchanged edit instead of an opaque error, without Telegram connections."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.errors import FloodWaitError, MessageNotModifiedError
from telethon.tl import functions, types

from telegram_mcp.tools import messages

NOT_MODIFIED = "Message 7 not modified: the new text is the same as the current one."


def _not_modified():
    return MessageNotModifiedError(request=None)


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    cl.get_me.return_value = SimpleNamespace(premium=True)
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=types.User(id=42)))
    return cl


@pytest.fixture
def error_formatter(monkeypatch):
    monkeypatch.setattr(
        messages, "log_and_format_error", lambda *args, **kwargs: "formatted-error"
    )


@pytest.mark.asyncio
async def test_plain_edit_not_modified_is_not_an_error(client):
    client.edit_message.side_effect = _not_modified()
    result = await messages.edit_message(42, 7, "same text", account="test")
    assert result == NOT_MODIFIED


@pytest.mark.asyncio
async def test_plain_edit_success_is_unchanged(client):
    result = await messages.edit_message(42, 7, "new text", account="test")
    assert result == "Message 7 edited."


@pytest.mark.asyncio
async def test_format_date_edit_not_modified_is_not_an_error(client):
    client.side_effect = _not_modified()
    result = await messages.edit_message(
        42, 7, "Due 13/09 17:00", format_date="13/09 17:00", account="test"
    )
    assert result == NOT_MODIFIED
    assert isinstance(client.await_args.args[0], functions.messages.EditMessageRequest)


@pytest.mark.asyncio
async def test_custom_emojis_edit_not_modified_is_not_an_error(client):
    client.side_effect = _not_modified()
    result = await messages.edit_message(
        42, 7, "cheers 🍷", custom_emojis=[{"emoji": "🍷", "id": "5368324170671202286"}]
    )
    assert result == NOT_MODIFIED
    assert isinstance(client.await_args.args[0], functions.messages.EditMessageRequest)


@pytest.mark.asyncio
async def test_rich_edit_not_modified_is_a_structured_result(client):
    client.side_effect = _not_modified()
    result = json.loads(await messages.edit_message(42, 7, "| a | b |", parse_mode="rich"))
    assert result == {
        "sent": False,
        "rich": True,
        "edited_message_id": 7,
        "not_modified": True,
    }


@pytest.mark.asyncio
async def test_rich_edit_success_is_unchanged(client):
    result = json.loads(await messages.edit_message(42, 7, "| a | b |", parse_mode="rich"))
    assert result == {"sent": True, "rich": True, "edited_message_id": 7}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"format_date": "13/09 17:00"},
        {"custom_emojis": [{"emoji": "🍷", "id": "5368324170671202286"}]},
        {"parse_mode": "rich"},
    ],
)
async def test_other_errors_still_go_through_log_and_format_error(client, error_formatter, kwargs):
    error = FloodWaitError(request=None, capture=30)
    client.edit_message.side_effect = error
    client.side_effect = error
    result = await messages.edit_message(42, 7, "Due 13/09 17:00 🍷", **kwargs)
    assert result == "formatted-error"
