"""Tests for list_forum_topic_icons (the default topic icon emoji set)."""

import json

import pytest
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import chats

ICON_ID = 5368324170671202286
PACK_ONLY_ICON_ID = 5377498341074542641


def _document(doc_id, attributes):
    return types.Document(
        id=doc_id,
        access_hash=1,
        file_reference=b"",
        date=None,
        mime_type="application/x-tgsticker",
        size=1,
        dc_id=1,
        attributes=attributes,
    )


def _sticker_set(documents, packs=()):
    return types.messages.StickerSet(
        set=types.StickerSet(
            id=1,
            access_hash=1,
            title="Topic Icons",
            short_name="TopicIcons",
            count=len(documents),
            hash=0,
            emojis=True,
        ),
        packs=list(packs),
        keywords=[],
        documents=documents,
    )


class RecordingClient:
    def __init__(self, result=None, error=None):
        self.requests = []
        self.result = result
        self.error = error

    async def __call__(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.result


def _patch(monkeypatch, client):
    async def fake_ensure_connected(cl):
        return None

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "ensure_connected", fake_ensure_connected)


@pytest.mark.asyncio
async def test_list_forum_topic_icons_returns_ids_and_emoji(monkeypatch):
    result = _sticker_set(
        documents=[
            _document(
                ICON_ID,
                [types.DocumentAttributeCustomEmoji(alt="📰", stickerset=None, free=True)],
            ),
            # No alt on the document itself: fall back to the set's emoticon pack.
            _document(PACK_ONLY_ICON_ID, []),
        ],
        packs=[types.StickerPack(emoticon="💡", documents=[PACK_ONLY_ICON_ID])],
    )
    client = RecordingClient(result=result)
    _patch(monkeypatch, client)

    output = await chats.list_forum_topic_icons(account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.GetStickerSetRequest)
    assert isinstance(request.stickerset, types.InputStickerSetEmojiDefaultTopicIcons)
    assert request.hash == 0
    assert json.loads(output)["results"] == [
        {"icon_emoji_id": str(ICON_ID), "emoji": "📰"},
        {"icon_emoji_id": str(PACK_ONLY_ICON_ID), "emoji": "💡"},
    ]


@pytest.mark.asyncio
async def test_list_forum_topic_icons_empty_set(monkeypatch):
    client = RecordingClient(result=_sticker_set(documents=[]))
    _patch(monkeypatch, client)

    output = await chats.list_forum_topic_icons(account=None)

    assert json.loads(output) == {"results": []}


@pytest.mark.asyncio
async def test_list_forum_topic_icons_reports_errors(monkeypatch):
    client = RecordingClient(error=RuntimeError("boom"))
    _patch(monkeypatch, client)

    output = await chats.list_forum_topic_icons(account=None)

    assert "code:" in output
    assert "boom" not in output


def test_list_forum_topic_icons_is_read_only():
    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    assert registered["list_forum_topic_icons"].annotations.readOnlyHint is True
