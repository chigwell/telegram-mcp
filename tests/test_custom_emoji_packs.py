"""Unit tests for Custom Emoji Pack MCP tools."""

import inspect
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from mcp.server.fastmcp import Context, Image
from telethon import functions, types
from telethon.errors.rpcerrorlist import StickersetInvalidError

from telegram_mcp.runtime import mcp
from telegram_mcp.tools import custom_emoji, stickers
from telegram_mcp.tools.custom_emoji import (
    extract_pack_short_name,
    get_custom_emoji_pack,
    get_custom_emoji_packs,
    preview_custom_emoji,
)

_get_custom_emoji_packs = inspect.unwrap(get_custom_emoji_packs)
_get_custom_emoji_pack = inspect.unwrap(get_custom_emoji_pack)
_preview_custom_emoji = inspect.unwrap(preview_custom_emoji)

EMOJI_DOC_ID = 5368324170671202286
PACK_ID = 987654321
DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _mock_sticker_set(
    id: int = PACK_ID,
    title: str = "Cute Ducks",
    short_name: str = "CuteDucks",
    count: int = 1,
    emojis: bool = True,
    **kwargs,
):
    return types.StickerSet(
        id=id,
        access_hash=123456,
        title=title,
        short_name=short_name,
        count=count,
        hash=0,
        emojis=emojis,
        archived=kwargs.get("archived", False),
        official=kwargs.get("official", False),
        installed_date=kwargs.get("installed_date", DATE),
    )


def _mock_custom_emoji_doc(
    doc_id: int = EMOJI_DOC_ID,
    alt: str = "🦆",
    free: bool = False,
    mime_type: str = "image/webp",
    is_animated: bool = False,
    is_video: bool = False,
):
    attrs = [
        types.DocumentAttributeCustomEmoji(
            alt=alt,
            stickerset=types.InputStickerSetShortName(short_name="CuteDucks"),
            free=free,
        )
    ]
    if is_animated:
        attrs.append(types.DocumentAttributeAnimated())
    if is_video:
        attrs.append(types.DocumentAttributeVideo(duration=1, w=100, h=100))

    return types.Document(
        id=doc_id,
        access_hash=654321,
        file_reference=b"ref",
        date=DATE,
        mime_type=mime_type,
        size=1024,
        dc_id=2,
        attributes=attrs,
        thumbs=[types.PhotoSize(type="s", w=100, h=100, size=512)],
    )


# ---------------------------------------------------------
# Test: FastMCP Registration & Annotations
# ---------------------------------------------------------
def test_tools_are_registered_and_read_only():
    registered = {t.name: t for t in mcp._tool_manager.list_tools()}
    assert "get_custom_emoji_packs" in registered
    assert "get_custom_emoji_pack" in registered
    assert "preview_custom_emoji" in registered

    assert registered["get_custom_emoji_packs"].annotations.readOnlyHint is True
    assert registered["get_custom_emoji_pack"].annotations.readOnlyHint is True
    assert registered["preview_custom_emoji"].annotations.readOnlyHint is True


def test_stickers_alias_exports():
    assert stickers.extract_pack_short_name is extract_pack_short_name
    assert stickers.get_custom_emoji_packs is get_custom_emoji_packs
    assert stickers.get_custom_emoji_pack is get_custom_emoji_pack
    assert stickers.preview_custom_emoji is preview_custom_emoji


# ---------------------------------------------------------
# Test: extract_pack_short_name
# ---------------------------------------------------------
@pytest.mark.parametrize(
    "input_val, expected",
    [
        ("https://t.me/addemoji/DuckPack", "DuckPack"),
        ("http://t.me/addemoji/DuckPack", "DuckPack"),
        ("t.me/addemoji/Duck_Pack_2026", "Duck_Pack_2026"),
        ("https://t.me/addstickers/ClassicStickers", "ClassicStickers"),
        ("t.me/addstickers/ClassicStickers", "ClassicStickers"),
        ("tg://addemoji?set=MyEmojiSet", "MyEmojiSet"),
        ("tg://addstickers?set=MyStickerSet", "MyStickerSet"),
        ("SimplePackName", "SimplePackName"),
        ("  WithWhitespace  ", "WithWhitespace"),
        ("", ""),
        (None, ""),
    ],
)
def test_extract_pack_short_name(input_val, expected):
    assert extract_pack_short_name(input_val) == expected


# ---------------------------------------------------------
# Test: get_custom_emoji_packs
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_get_custom_emoji_packs_default(monkeypatch):
    cl = AsyncMock()
    s1 = _mock_sticker_set(id=1, title="Ducks", short_name="ducks", count=5)
    s2 = _mock_sticker_set(id=2, title="Cats", short_name="cats", count=10)
    cl.return_value = types.messages.AllStickers(hash=0, sets=[s1, s2])

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result_json = await _get_custom_emoji_packs(account="test")
    packs = json.loads(result_json)

    assert len(packs) == 2
    assert packs[0]["id"] == "1"
    assert packs[0]["title"] == "Ducks"
    assert packs[0]["short_name"] == "ducks"
    assert packs[0]["count"] == 5
    assert packs[0]["url"] == "https://t.me/addemoji/ducks"
    assert packs[1]["title"] == "Cats"


@pytest.mark.asyncio
async def test_get_custom_emoji_packs_featured(monkeypatch):
    cl = AsyncMock()
    s = _mock_sticker_set(id=10, title="Featured Pack", short_name="feat_pack")
    covered = types.StickerSetCovered(set=s, cover=types.DocumentEmpty(id=1))
    cl.return_value = types.messages.FeaturedStickers(hash=0, count=1, sets=[covered], unread=[])

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result_json = await _get_custom_emoji_packs(featured=True, account="test")
    packs = json.loads(result_json)

    assert len(packs) == 1
    assert packs[0]["id"] == "10"
    assert packs[0]["title"] == "Featured Pack"
    assert packs[0]["short_name"] == "feat_pack"


@pytest.mark.asyncio
async def test_get_custom_emoji_packs_search(monkeypatch):
    cl = AsyncMock()
    s = _mock_sticker_set(id=20, title="Found Ducks", short_name="found_ducks")
    cl.return_value = types.messages.FoundStickerSets(hash=0, sets=[s])

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result_json = await _get_custom_emoji_packs(query="ducks", account="test")
    packs = json.loads(result_json)

    assert len(packs) == 1
    assert packs[0]["id"] == "20"
    assert packs[0]["title"] == "Found Ducks"


@pytest.mark.asyncio
async def test_get_custom_emoji_packs_respects_limit(monkeypatch):
    cl = AsyncMock()
    sets = [_mock_sticker_set(id=i, title=f"Pack {i}", short_name=f"p{i}") for i in range(10)]
    cl.return_value = types.messages.AllStickers(hash=0, sets=sets)

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result_json = await _get_custom_emoji_packs(limit=3, account="test")
    packs = json.loads(result_json)
    assert len(packs) == 3


@pytest.mark.asyncio
async def test_get_custom_emoji_packs_error(monkeypatch):
    cl = AsyncMock()
    cl.side_effect = RuntimeError("Connection failed")

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    res = await _get_custom_emoji_packs()
    assert "An error occurred (code:" in res


# ---------------------------------------------------------
# Test: get_custom_emoji_pack
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_get_custom_emoji_pack_success(monkeypatch):
    cl = AsyncMock()
    s_meta = _mock_sticker_set(
        id=100, title="Duck Pack", short_name="duck_pack", count=2, emojis=True
    )
    doc1 = _mock_custom_emoji_doc(doc_id=101, alt="🦆", free=True, mime_type="image/webp")
    doc2 = _mock_custom_emoji_doc(
        doc_id=102, alt="🔥", free=False, mime_type="application/x-tgsticker", is_animated=True
    )
    packs = [
        types.StickerPack(emoticon="🦆", documents=[101]),
        types.StickerPack(emoticon="🔥", documents=[102]),
    ]
    cl.return_value = types.messages.StickerSet(
        set=s_meta, packs=packs, keywords=[], documents=[doc1, doc2]
    )

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result_json = await _get_custom_emoji_pack("https://t.me/addemoji/duck_pack")
    data = json.loads(result_json)

    assert data["pack"]["id"] == "100"
    assert data["pack"]["title"] == "Duck Pack"
    assert data["pack"]["short_name"] == "duck_pack"
    assert data["pack"]["is_emoji"] is True
    assert data["pack"]["url"] == "https://t.me/addemoji/duck_pack"

    emojis = data["emojis"]
    assert len(emojis) == 2

    assert emojis[0]["document_id"] == "101"
    assert emojis[0]["alt"] == "🦆"
    assert emojis[0]["emoji"] == "🦆"
    assert emojis[0]["free"] is True
    assert emojis[0]["format"] == "static"
    assert emojis[0]["mime_type"] == "image/webp"

    assert emojis[1]["document_id"] == "102"
    assert emojis[1]["alt"] == "🔥"
    assert emojis[1]["free"] is False
    assert emojis[1]["format"] == "animated"
    assert emojis[1]["mime_type"] == "application/x-tgsticker"


@pytest.mark.asyncio
async def test_get_custom_emoji_pack_invalid_input():
    res = await _get_custom_emoji_pack("")
    assert "Invalid pack name or URL provided." in res


@pytest.mark.asyncio
async def test_get_custom_emoji_pack_not_found(monkeypatch):
    cl = AsyncMock()
    cl.side_effect = StickersetInvalidError(request=None)

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    res = await _get_custom_emoji_pack("NonExistentPack")
    assert "Emoji pack 'NonExistentPack' not found or is invalid." in res


# ---------------------------------------------------------
# Test: preview_custom_emoji
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_preview_custom_emoji_invalid_id():
    res = await _preview_custom_emoji("invalid_id")
    assert "Invalid document_id" in res

    res2 = await _preview_custom_emoji(-5)
    assert "Invalid document_id" in res2


@pytest.mark.asyncio
async def test_preview_custom_emoji_not_found(monkeypatch):
    cl = AsyncMock()
    cl.return_value = []

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    res = await _preview_custom_emoji(12345)
    assert "Custom emoji with document ID 12345 not found." in res


@pytest.mark.asyncio
async def test_preview_custom_emoji_thumbnail_success(monkeypatch):
    cl = AsyncMock()
    doc = _mock_custom_emoji_doc(doc_id=555)
    cl.return_value = [doc]

    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIFfakejpeg"
    cl.download_media = AsyncMock(return_value=jpeg_bytes)

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result = await _preview_custom_emoji(555, thumbnail=True)
    assert isinstance(result, Image)
    assert result.data == jpeg_bytes
    assert result._format == "jpeg"


@pytest.mark.asyncio
async def test_preview_custom_emoji_webp_success(monkeypatch):
    cl = AsyncMock()
    doc = _mock_custom_emoji_doc(doc_id=777)
    cl.return_value = [doc]

    webp_bytes = b"RIFF\x20\x00\x00\x00WEBPVP8 fakewebp"
    cl.download_media = AsyncMock(return_value=webp_bytes)

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())

    result = await _preview_custom_emoji(777, thumbnail=False)
    assert isinstance(result, Image)
    assert result.data == webp_bytes
    assert result._format == "webp"


@pytest.mark.asyncio
async def test_preview_custom_emoji_with_save_path(monkeypatch, tmp_path):
    cl = AsyncMock()
    doc = _mock_custom_emoji_doc(doc_id=888)
    cl.return_value = [doc]

    png_bytes = b"\x89PNG\r\n\x1a\nfakepng"
    cl.download_media = AsyncMock(return_value=png_bytes)

    target_file = tmp_path / "emoji_888.png"

    monkeypatch.setattr(custom_emoji, "get_client", lambda account=None: cl)
    monkeypatch.setattr(custom_emoji, "ensure_connected", AsyncMock())
    monkeypatch.setattr(
        custom_emoji,
        "_resolve_writable_file_path",
        AsyncMock(return_value=(target_file, None)),
    )

    result = await _preview_custom_emoji(888, save_path=str(target_file))
    assert isinstance(result, list)
    assert f"Custom emoji 888 saved to {target_file}" in result[0]
    assert isinstance(result[1], Image)
    assert target_file.read_bytes() == png_bytes
