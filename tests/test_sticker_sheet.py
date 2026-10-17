"""Sticker sheet batching and pack metadata caching."""

import inspect
import io
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from mcp.server.fastmcp import Image
from PIL import Image as PillowImage
from telethon import functions, types

from telegram_mcp.contact_sheet import build_contact_sheet
from telegram_mcp.runtime import mcp
from telegram_mcp.tools import stickers
from telegram_mcp.tools.stickers import get_sticker_sheet, load_pack

_get_sticker_sheet = inspect.unwrap(get_sticker_sheet)

DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
PACK_ID = 4008062260856494640


def _jpeg_bytes(width=256, height=256, colour=(200, 40, 40)):
    rendered = io.BytesIO()
    PillowImage.new("RGB", (width, height), colour).save(rendered, format="JPEG")
    return rendered.getvalue()


def _sticker_set(short_name="Memes", pack_id=PACK_ID, hash_value=777, count=5):
    return types.StickerSet(
        id=pack_id,
        access_hash=123456,
        title="Memes",
        short_name=short_name,
        count=count,
        hash=hash_value,
        emojis=False,
        archived=False,
        official=False,
        installed_date=DATE,
    )


def _sticker_doc(doc_id, alt="😄"):
    return types.Document(
        id=doc_id,
        access_hash=654321,
        file_reference=b"ref",
        date=DATE,
        mime_type="image/webp",
        size=1024,
        dc_id=2,
        attributes=[
            types.DocumentAttributeSticker(
                alt=alt,
                stickerset=types.InputStickerSetEmpty(),
                mask=False,
            ),
            types.DocumentAttributeImageSize(w=512, h=512),
        ],
        thumbs=[],
    )


def _sticker_message(short_name="Memes", docs=None, hash_value=777):
    documents = docs or [_sticker_doc(100 + index) for index in range(5)]
    packs = [
        types.StickerPack(emoticon="😄", documents=[doc.id for doc in documents]),
    ]
    return types.messages.StickerSet(
        set=_sticker_set(short_name=short_name, hash_value=hash_value, count=len(documents)),
        packs=packs,
        keywords=[],
        documents=documents,
    )


@pytest.fixture(autouse=True)
def _fresh_caches():
    stickers.clear_sticker_caches()
    yield
    stickers.clear_sticker_caches()


def test_sticker_sheet_is_registered_as_read_only():
    registered = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    assert registered["get_sticker_sheet"].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_sticker_sheet_returns_one_image_plus_manifest(monkeypatch):
    cl = AsyncMock()
    cl.return_value = _sticker_message()
    cl.download_media = AsyncMock(return_value=_jpeg_bytes())

    monkeypatch.setattr(stickers, "get_client", lambda account=None: cl)
    monkeypatch.setattr(stickers, "ensure_connected", AsyncMock())

    response = await _get_sticker_sheet("Memes", columns=8)

    assert isinstance(response, list)
    assert "5 sticker(s) 1-5 of 5" in response[0]
    assert isinstance(response[1], Image)

    manifest = json.loads(response[2])
    assert [entry["index"] for entry in manifest] == [1, 2, 3, 4, 5]
    assert manifest[0]["document_id"] == "100"
    assert manifest[0]["alt"] == "😄"
    assert cl.download_media.await_count == 5


@pytest.mark.asyncio
async def test_sticker_sheet_limits_range_and_marks_sheet_cells(monkeypatch):
    cl = AsyncMock()
    cl.return_value = _sticker_message()
    cl.download_media = AsyncMock(return_value=_jpeg_bytes())

    monkeypatch.setattr(stickers, "get_client", lambda account=None: cl)
    monkeypatch.setattr(stickers, "ensure_connected", AsyncMock())

    response = await _get_sticker_sheet("Memes", start=2, count=2, columns=8)

    assert "2 sticker(s) 2-3 of 5" in response[0]
    manifest = json.loads(response[2])
    assert [entry["index"] for entry in manifest] == [2, 3]
    assert isinstance(response[1], Image)


@pytest.mark.asyncio
async def test_sticker_sheet_rejects_out_of_range_start(monkeypatch):
    cl = AsyncMock()
    cl.return_value = _sticker_message()

    monkeypatch.setattr(stickers, "get_client", lambda account=None: cl)
    monkeypatch.setattr(stickers, "ensure_connected", AsyncMock())

    response = await _get_sticker_sheet("Memes", start=99)
    assert "start must be between 1 and 5" in response


@pytest.mark.asyncio
async def test_pack_metadata_is_cached_and_hash_is_reused(monkeypatch):
    requests = []
    first_message = _sticker_message()

    async def respond(request):
        requests.append(request)
        if isinstance(request, functions.messages.GetStickerSetRequest):
            return first_message if len(requests) == 1 else types.messages.StickerSetNotModified()
        raise AssertionError(f"unexpected request {request!r}")

    cl = AsyncMock(side_effect=respond)

    monkeypatch.setattr(stickers, "get_client", lambda account=None: cl)
    monkeypatch.setattr(stickers, "ensure_connected", AsyncMock())

    first = await load_pack(cl, "Memes")
    second = await load_pack(cl, "Memes")

    assert isinstance(first, tuple) and isinstance(second, tuple)
    assert [doc.id for doc in second[1]] == [doc.id for doc in first[1]]
    get_requests = [
        request
        for request in requests
        if isinstance(request, functions.messages.GetStickerSetRequest)
    ]
    assert len(get_requests) == 2
    assert get_requests[0].hash == 0
    assert get_requests[1].hash == 777


@pytest.mark.asyncio
async def test_pack_id_resolves_to_installed_pack(monkeypatch):
    seen = []

    async def respond(request):
        seen.append(request)
        if isinstance(request, functions.messages.GetAllStickersRequest):
            return types.messages.AllStickers(hash=0, sets=[_sticker_set()])
        if isinstance(request, functions.messages.GetStickerSetRequest):
            return _sticker_message()
        raise AssertionError(f"unexpected request {request!r}")

    cl = AsyncMock(side_effect=respond)

    loaded = await load_pack(cl, str(PACK_ID))

    assert isinstance(loaded, tuple)
    get_request = next(
        request for request in seen if isinstance(request, functions.messages.GetStickerSetRequest)
    )
    assert isinstance(get_request.stickerset, types.InputStickerSetID)
    assert get_request.stickerset.id == PACK_ID
    assert get_request.stickerset.access_hash == 123456


@pytest.mark.asyncio
async def test_unknown_pack_id_explains_the_limitation(monkeypatch):
    async def respond(request):
        if isinstance(request, functions.messages.GetAllStickersRequest):
            return types.messages.AllStickers(hash=0, sets=[])
        raise AssertionError(f"unexpected request {request!r}")

    cl = AsyncMock(side_effect=respond)

    result = await load_pack(cl, "1234567890")
    assert "Only installed packs can be addressed by id" in result


def test_dense_sheet_options_shrink_cells_and_widen_rows():
    tiles = [(_jpeg_bytes(), str(index)) for index in range(8)]
    sheet_bytes = build_contact_sheet(
        tiles,
        columns=8,
        cell_edge=64,
        label_strip=16,
        max_columns=8,
    )
    with PillowImage.open(io.BytesIO(sheet_bytes)) as sheet:
        assert sheet.size == (8 * 64, 64 + 16)
