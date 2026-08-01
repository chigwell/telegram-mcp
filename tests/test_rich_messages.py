import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl import functions, types

from telegram_mcp.tools import messages


class _Client:
    def __init__(self):
        self.request = None

    async def __call__(self, request):
        self.request = request


@pytest.mark.asyncio
async def test_send_rich_message_uses_native_html_payload(monkeypatch):
    client = _Client()
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)

    async def resolve(chat_id, cl):
        assert (chat_id, cl) == (123, client)
        return "peer"

    monkeypatch.setattr(messages, "resolve_input_entity", resolve)

    result = await messages.send_rich_message(123, html="<h1>Hello</h1>")

    assert result == "Rich message sent successfully."
    assert isinstance(client.request, functions.messages.SendMessageRequest)
    assert client.request.peer == "peer"
    assert isinstance(client.request.rich_message, types.InputRichMessageHTML)
    assert client.request.rich_message.html == "<h1>Hello</h1>"


@pytest.mark.asyncio
async def test_send_rich_message_draft_uses_native_draft_action(monkeypatch):
    client = _Client()
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)

    async def resolve(*args):
        return "peer"

    monkeypatch.setattr(messages, "resolve_input_entity", resolve)

    result = await messages.send_rich_message_draft(123, 42, markdown="# Hello")

    assert result == "Rich message draft updated successfully."
    assert isinstance(client.request, functions.messages.SetTypingRequest)
    assert isinstance(client.request.action, types.InputSendMessageRichMessageDraftAction)
    assert client.request.action.random_id == 42
    assert client.request.action.rich_message.markdown == "# Hello"


def test_rich_message_is_exposed_in_message_results():
    rich = types.RichMessage(blocks=[], photos=[], documents=[])
    message = SimpleNamespace(
        id=1,
        sender=None,
        date=datetime.now(timezone.utc),
        sender_id=None,
        out=False,
        message="",
        media=None,
        grouped_id=None,
        reply_to=None,
        fwd_from=None,
        via_bot_id=None,
        edit_date=None,
        pinned=False,
        reactions=None,
        replies=None,
        buttons=None,
        entities=None,
        action=None,
        ttl_period=None,
        rich_message=rich,
    )

    record = messages.message_to_dict(message)

    assert record["rich_message"]["_"] == "RichMessage"
    assert json.dumps(record, default=str)
