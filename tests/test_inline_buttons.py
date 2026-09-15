"""Offline contracts for button inspection and callback selection."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import messages


class RecordingClient:
    def __init__(self):
        self.by_id = None
        self.recent = []
        self.reads = []
        self.requests = []

    async def get_messages(self, peer, **kwargs):
        self.reads.append(kwargs)
        return self.by_id if "ids" in kwargs else self.recent

    async def __call__(self, request):
        bytes(request)
        self.requests.append(request)
        return SimpleNamespace(message="Done", alert=True)


@pytest.fixture
def client(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(
        messages, "resolve_entity", AsyncMock(return_value=types.InputPeerUser(42, 99))
    )
    return client


def keyboard(representation="buttons"):
    buttons = [
        SimpleNamespace(text="Open", url="https://example.com"),
        SimpleNamespace(text="Confirm", data=b"confirm"),
    ]
    msg = SimpleNamespace(id=7, date=datetime(2026, 8, 1, tzinfo=timezone.utc))
    if representation == "buttons":
        msg.buttons = [[buttons[0]], [buttons[1]]]
    else:
        msg.reply_markup = SimpleNamespace(rows=[SimpleNamespace(buttons=buttons)])
    return msg


@pytest.mark.asyncio
@pytest.mark.parametrize("representation", ["buttons", "reply_markup"])
async def test_listing_preserves_flat_order_and_metadata(client, representation):
    client.by_id = [keyboard(representation)]
    result = json.loads(await messages.list_inline_buttons(42, message_id="7"))
    assert result == {
        "results": [
            {"index": 0, "text": "Open", "has_callback": False, "url": "https://example.com"},
            {"index": 1, "text": "Confirm", "has_callback": True},
        ],
        "message_id": 7,
        "date": "2026-08-01T00:00:00+00:00",
    }
    assert client.reads == [{"ids": 7}]
    assert client.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("representation", ["buttons", "reply_markup"])
@pytest.mark.parametrize("selection", [{"button_index": "1"}, {"button_text": " CONFIRM "}])
async def test_press_preserves_callback_request(client, representation, selection):
    client.by_id = keyboard(representation)
    result = json.loads(await messages.press_inline_button(42, message_id=7, **selection))
    assert result == {"results": [], "response": "Done Telegram displayed an alert to the user."}
    assert client.reads == [{"ids": 7}]
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.GetBotCallbackAnswerRequest)
    assert (request.peer.user_id, request.msg_id, request.data) == (42, 7, b"confirm")


@pytest.mark.asyncio
async def test_only_press_retries_id_fetch_without_markup(client):
    client.by_id = SimpleNamespace(id=7)
    client.recent = [keyboard()]
    assert await messages.list_inline_buttons(42, message_id=7) == (
        "Message 7 does not contain inline buttons."
    )
    assert client.reads == [{"ids": 7}]
    client.reads.clear()
    await messages.press_inline_button(42, message_id=7, button_index=1)
    assert client.reads == [{"ids": 7}, {"limit": 30}]
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_recent_search_keeps_tool_specific_limits(client):
    client.recent = [SimpleNamespace(id=6), keyboard()]
    await messages.list_inline_buttons(42, limit=9)
    await messages.press_inline_button(42, button_index=1)
    assert client.reads == [{"limit": 9}, {"limit": 20}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selection,expected",
    [
        (
            {"button_index": 0},
            "Selected button opens a URL instead of sending a callback: https://example.com",
        ),
        ({"button_index": 2}, "button_index out of range. Valid indices: 0-1."),
        ({"button_text": "Missing"}, "Button not found. Available buttons: [0] Open, [1] Confirm"),
        ({}, "Provide button_text or button_index to choose a button."),
    ],
)
async def test_invalid_selection_never_sends_callback(client, selection, expected):
    client.by_id = keyboard()
    assert await messages.press_inline_button(42, message_id=7, **selection) == expected
    assert client.requests == []
