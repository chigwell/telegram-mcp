"""Unit tests for sticker pack listing, preview and sending."""

import inspect
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import Image
from telethon import types

from telegram_mcp.tools import stickers

_get_sticker_sets = inspect.unwrap(stickers.get_sticker_sets)
_preview_sticker = inspect.unwrap(stickers.preview_sticker)
_send_sticker = inspect.unwrap(stickers.send_sticker)

PACK_ID = 424242
DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _sticker_doc(doc_id, alt="🦆", mime_type="image/webp", animated=False, video=False):
    attrs = [
        types.DocumentAttributeSticker(
            alt=alt,
            stickerset=types.InputStickerSetShortName(short_name="DuckPack"),
        ),
        types.DocumentAttributeImageSize(w=512, h=512),
    ]
    if animated:
        attrs.append(types.DocumentAttributeAnimated())
    if video:
        attrs.append(types.DocumentAttributeVideo(duration=1, w=512, h=512))
    return types.Document(
        id=doc_id,
        access_hash=1,
        file_reference=b"ref",
        date=DATE,
        mime_type=mime_type,
        size=1024,
        dc_id=2,
        attributes=attrs,
        thumbs=[types.PhotoSize(type="s", w=512, h=512, size=512)],
    )


def _sticker_set(count=2, emojis=False):
    return types.StickerSet(
        id=PACK_ID,
        access_hash=1,
        title="Duck Pack",
        short_name="DuckPack",
        count=count,
        hash=0,
        emojis=emojis,
        archived=False,
        official=False,
        installed_date=DATE,
    )


def _pack_result(documents, emojis=False):
    packs = [
        types.StickerPack(emoticon=doc.attributes[0].alt, documents=[doc.id]) for doc in documents
    ]
    return types.messages.StickerSet(
        set=_sticker_set(count=len(documents), emojis=emojis),
        packs=packs,
        keywords=[],
        documents=documents,
    )


def _client(monkeypatch, result):
    cl = AsyncMock()
    cl.return_value = result
    monkeypatch.setattr(stickers, "get_client", lambda account=None: cl)
    monkeypatch.setattr(stickers, "ensure_connected", AsyncMock())
    return cl


@pytest.mark.asyncio
async def test_get_sticker_sets_carries_short_name(monkeypatch):
    result = types.messages.AllStickers(
        hash=0, sets=[_sticker_set(), _sticker_set(count=3, emojis=True)]
    )
    s_meta = result.sets[1]
    s_meta.short_name = "EmojiPack"
    _client(monkeypatch, result)

    sets = json.loads(await _get_sticker_sets())
    assert sets[0]["short_name"] == "DuckPack"
    assert sets[0]["url"] == "https://t.me/addstickers/DuckPack"
    assert sets[0]["count"] == 2
    assert sets[0]["is_emoji"] is False
    assert sets[1]["is_emoji"] is True
    assert sets[1]["url"] == "https://t.me/addemoji/EmojiPack"


@pytest.mark.asyncio
async def test_preview_sticker_returns_static_webp(monkeypatch):
    doc = _sticker_doc(555)
    cl = _client(monkeypatch, _pack_result([doc]))
    webp = b"RIFF\x20\x00\x00\x00WEBPVP8 fakewebp"
    cl.download_media = AsyncMock(return_value=webp)

    result = await _preview_sticker("DuckPack")
    assert isinstance(result, Image)
    assert result.data == webp
    assert result._format == "webp"


@pytest.mark.asyncio
async def test_preview_sticker_animated_uses_thumbnail(monkeypatch):
    doc = _sticker_doc(556, mime_type="application/x-tgsticker", animated=True)
    cl = _client(monkeypatch, _pack_result([doc]))
    jpeg = b"\xff\xd8\xff\xe0fakejpeg"
    cl.download_media = AsyncMock(return_value=jpeg)

    result = await _preview_sticker("DuckPack", document_id=556)
    assert isinstance(result, Image)
    cl.download_media.assert_awaited_once()
    assert cl.download_media.await_args.kwargs["thumb"] == -1


@pytest.mark.asyncio
async def test_preview_sticker_index_out_of_range(monkeypatch):
    _client(monkeypatch, _pack_result([_sticker_doc(557)]))
    res = await _preview_sticker("DuckPack", index=5)
    assert "index must be between 1 and 1" in res


@pytest.mark.asyncio
async def test_send_sticker_from_pack_sends_document(monkeypatch):
    doc = _sticker_doc(558)
    cl = _client(monkeypatch, _pack_result([doc]))
    cl.send_file = AsyncMock()
    monkeypatch.setattr(stickers, "resolve_entity", AsyncMock(return_value=object()))

    res = await _send_sticker(-100, pack="DuckPack", index=1)
    assert "Sticker 1 of DuckPack" in res
    sent = cl.send_file.await_args.args[1]
    assert sent.id == 558


@pytest.mark.asyncio
async def test_send_sticker_rejects_two_sources(monkeypatch):
    res = await _send_sticker(-100, file_path="sticker.webp", pack="DuckPack")
    assert "Pass either file_path or pack/document_id" in res


def test_select_document_by_id_and_default():
    docs = [_sticker_doc(1), _sticker_doc(2)]
    picked, error = stickers.select_document(docs, None, "2")
    assert error is None
    assert picked[0] == 2

    picked, error = stickers.select_document(docs, None, None)
    assert error is None
    assert picked[0] == 1

    _, error = stickers.select_document(docs, 1, "2")
    assert "not both" in error
