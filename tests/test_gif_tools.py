"""get_gif_search / send_gif: native GIF search that yields documents send_gif can send."""

import json
from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import media

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
FRIEND = types.User(id=101, first_name="Ivan", access_hash=1)
GIF_ID = 5390871221226233431


def gif_document(document_id=GIF_ID):
    return types.Document(
        id=document_id,
        access_hash=777,
        file_reference=b"ref",
        date=NOW,
        mime_type="video/mp4",
        size=1000,
        dc_id=2,
        attributes=[types.DocumentAttributeAnimated()],
    )


def gif_message(document_id=GIF_ID):
    return types.Message(
        id=1,
        date=NOW,
        peer_id=types.PeerUser(FRIEND.id),
        message="",
        media=types.MessageMediaDocument(document=gif_document(document_id)),
    )


class GifServer(TelegramClient):
    def __init__(self, saved=()):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.requests = []
        self.saved = list(saved)

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        self.requests.append(request)
        if isinstance(request, functions.messages.SearchGlobalRequest):
            # A real server pages by offset_id and respects limit: return the hits
            # after the offset, capped at the requested limit.
            remaining = [
                gif_message(GIF_ID + i) for i in range(3) if GIF_ID + i > request.offset_id
            ]
            page = remaining[: max(request.limit, 0)]
            return types.messages.Messages(messages=page, topics=[], chats=[], users=[FRIEND])
        if isinstance(request, functions.messages.GetSavedGifsRequest):
            return types.messages.SavedGifs(hash=1, gifs=self.saved)
        if isinstance(request, functions.messages.SendMediaRequest):
            return types.Updates(updates=[], users=[], chats=[], date=NOW, seq=0)
        raise AssertionError(f"unexpected request {type(request).__name__}")


@pytest.fixture
def server(monkeypatch):
    client = GifServer()

    async def resolve(chat_id, cl=None):
        return FRIEND

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", resolve)
    monkeypatch.setattr(media, "ensure_connected", connected)
    monkeypatch.setattr(media, "_known_gifs", {})
    return client


@pytest.mark.asyncio
async def test_gif_search_returns_ids_that_send_gif_accepts(server):
    result = json.loads(await media.get_gif_search(query="cat", limit=2))

    assert result == [GIF_ID, GIF_ID + 1]
    searches = [
        r for r in server.requests if isinstance(r, functions.messages.SearchGlobalRequest)
    ]
    assert searches[0].q == "cat"
    assert isinstance(searches[0].filter, types.InputMessagesFilterGif)
    assert not [
        r for r in server.requests if isinstance(r, functions.messages.GetInlineBotResultsRequest)
    ]


@pytest.mark.asyncio
async def test_send_gif_sends_a_gif_found_by_the_search(server):
    await media.get_gif_search(query="cat")

    result = await media.send_gif(chat_id=101, gif_id=GIF_ID)

    assert result == "GIF sent to chat 101."
    sent = [r for r in server.requests if isinstance(r, functions.messages.SendMediaRequest)]
    assert len(sent) == 1
    document = sent[0].media.id
    assert (document.id, document.access_hash, document.file_reference) == (GIF_ID, 777, b"ref")


@pytest.mark.asyncio
async def test_send_gif_finds_a_saved_gif(server):
    server.saved = [gif_document(42)]

    assert await media.send_gif(chat_id=101, gif_id=42) == "GIF sent to chat 101."


@pytest.mark.asyncio
async def test_send_gif_explains_an_unknown_id(server):
    result = await media.send_gif(chat_id=101, gif_id=1)

    assert "Run get_gif_search first" in result
    assert not [r for r in server.requests if isinstance(r, functions.messages.SendMediaRequest)]
