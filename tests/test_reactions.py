import pytest
from telethon.errors import ReactionInvalidError
from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji

from telegram_mcp.tools import messages


CUSTOM_DOCUMENT_ID = 1234567890123456789


class _RecordingClient:
    """Capture reaction requests without contacting Telegram."""

    def __init__(self, errors=None):
        self.errors = list(errors or [])
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.errors:
            raise self.errors.pop(0)


@pytest.fixture
def reaction_client(monkeypatch):
    """Route the tool through a deterministic peer and fake Telegram client."""
    client = _RecordingClient()
    monkeypatch.setattr(messages, "get_client", lambda _account=None: client)

    async def _resolve_input_entity(chat_id, resolved_client):
        assert chat_id == 123
        assert resolved_client is client
        return "resolved-peer"

    monkeypatch.setattr(messages, "resolve_input_entity", _resolve_input_entity)
    return client


@pytest.mark.asyncio
async def test_send_reaction_keeps_unicode_emoji_behavior(reaction_client):
    """A standard Unicode reaction must remain a one-request operation."""
    result = await messages.send_reaction(chat_id=123, message_id=456, emoji="👍")

    assert "sent" in result
    assert len(reaction_client.requests) == 1
    request = reaction_client.requests[0]
    assert request.peer == "resolved-peer"
    assert request.msg_id == 456
    assert request.add_to_recent is None
    assert request.reaction == [ReactionEmoji(emoticon="👍")]


@pytest.mark.asyncio
async def test_send_reaction_accepts_round_trip_custom_document_id(reaction_client):
    """The custom:<id> shape returned by reads must work unchanged on writes."""
    result = await messages.send_reaction(
        chat_id=123,
        message_id=456,
        emoji=f"custom:{CUSTOM_DOCUMENT_ID}",
    )

    assert "sent" in result
    assert len(reaction_client.requests) == 1
    request = reaction_client.requests[0]
    assert request.add_to_recent is True
    assert request.reaction == [ReactionCustomEmoji(document_id=CUSTOM_DOCUMENT_ID)]


@pytest.mark.asyncio
async def test_send_reaction_retries_custom_emoji_without_picker_state(monkeypatch):
    """Private chats that reject add_to_recent should get one narrow retry."""
    client = _RecordingClient(errors=[ReactionInvalidError(request=None)])
    monkeypatch.setattr(messages, "get_client", lambda _account=None: client)

    async def _resolve_input_entity(_chat_id, _client):
        return "resolved-peer"

    monkeypatch.setattr(messages, "resolve_input_entity", _resolve_input_entity)

    result = await messages.send_reaction(
        chat_id=123,
        message_id=456,
        emoji=f"custom:{CUSTOM_DOCUMENT_ID}",
    )

    assert "sent" in result
    assert len(client.requests) == 2
    assert client.requests[0].add_to_recent is True
    assert client.requests[1].add_to_recent is None
    assert client.requests[0].reaction == client.requests[1].reaction


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", ["custom:", "custom:abc", "custom:0", "custom:-1"])
async def test_send_reaction_rejects_invalid_custom_document_id(
    monkeypatch, reaction_client, emoji
):
    """Malformed custom identifiers must fail before resolving or sending."""

    async def _unexpected_resolution(_chat_id, _client):
        pytest.fail("invalid custom reactions must not resolve a Telegram peer")

    monkeypatch.setattr(messages, "resolve_input_entity", _unexpected_resolution)

    result = await messages.send_reaction(chat_id=123, message_id=456, emoji=emoji)

    assert result.startswith("An error occurred")
    assert reaction_client.requests == []
