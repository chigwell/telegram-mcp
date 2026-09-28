"""Reaction tools accept Telegram custom emoji IDs and empty reaction lists."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl import functions
from telethon.tl.types import PeerUser, ReactionCustomEmoji, ReactionEmoji

from telegram_mcp.tools import messages


class FakeClient:
    def __init__(self, message=None, reactions=None):
        self.message = message
        self.reactions = reactions or []
        self.requests = []

    async def get_messages(self, peer, ids):
        assert peer == "chat"
        assert ids == 880
        return self.message

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.messages.GetMessageReactionsListRequest):
            return SimpleNamespace(reactions=self.reactions)
        return SimpleNamespace()


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(messages, "get_client", lambda account=None: fake)

    async def resolve(chat_id, selected_client):
        assert chat_id == -1004358797279
        assert selected_client is fake
        return "chat"

    monkeypatch.setattr(messages, "resolve_input_entity", resolve)
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("reaction_summary", [None, SimpleNamespace(results=[])])
async def test_unreacted_message_returns_empty_list_without_list_request(client, reaction_summary):
    client.message = SimpleNamespace(reactions=reaction_summary)

    result = json.loads(await messages.get_message_reactions(-1004358797279, 880))

    assert result == {
        "message_id": 880,
        "chat_id": "-1004358797279",
        "reactions": [],
        "count": 0,
    }
    assert client.requests == []


@pytest.mark.asyncio
async def test_missing_message_is_distinguished_from_no_reactions(client):
    result = await messages.get_message_reactions(-1004358797279, 880)

    assert result == "Message 880 not found in chat -1004358797279."
    assert client.requests == []


@pytest.mark.asyncio
async def test_existing_custom_reaction_is_read_as_reusable_id(client):
    client.message = SimpleNamespace(reactions=SimpleNamespace(results=[SimpleNamespace(count=1)]))
    client.reactions = [
        SimpleNamespace(
            peer_id=PeerUser(user_id=700097935),
            reaction=ReactionCustomEmoji(document_id=5427009714745517609),
            date=datetime(2026, 9, 27, tzinfo=timezone.utc),
        )
    ]

    result = json.loads(await messages.get_message_reactions(-1004358797279, 880))

    assert result["reactions"][0]["emoji"] == "custom:5427009714745517609"
    assert result["reactions"][0]["user_id"] == 700097935
    assert len(client.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("emoji", "reaction_type", "value"),
    [
        ("👍", ReactionEmoji, "👍"),
        ("custom:5427009714745517609", ReactionCustomEmoji, 5427009714745517609),
    ],
)
async def test_send_reaction_uses_matching_telegram_type(client, emoji, reaction_type, value):
    result = await messages.send_reaction(-1004358797279, 880, emoji)

    assert "sent" in result
    request = client.requests[0]
    assert isinstance(request, functions.messages.SendReactionRequest)
    assert isinstance(request.reaction[0], reaction_type)
    if reaction_type is ReactionCustomEmoji:
        assert request.reaction[0].document_id == value
    else:
        assert request.reaction[0].emoticon == value


@pytest.mark.asyncio
async def test_invalid_custom_id_does_not_call_telegram(client):
    result = await messages.send_reaction(-1004358797279, 880, "custom:not-an-id")

    assert "Invalid custom reaction" in result
    assert client.requests == []
