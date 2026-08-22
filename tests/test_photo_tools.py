import io
import json

import pytest
from mcp.server.fastmcp import Image
from PIL import Image as PillowImage

from telegram_mcp import contact_sheet, runtime
from telegram_mcp.photo_source import PhotoReference
from telegram_mcp.tools import media


class _Entity:
    pass


def _jpeg_bytes(colour=(10, 120, 200)):
    rendered = io.BytesIO()
    PillowImage.new("RGB", (300, 300), colour).save(rendered, format="JPEG")
    return rendered.getvalue()


def _reference(identifier, is_current=False, caption=""):
    return PhotoReference(
        identifier=identifier,
        photo=object(),
        is_current=is_current,
        taken_at=None,
        caption=caption,
    )


@pytest.fixture
def patched_media(monkeypatch):
    entity = _Entity()
    client = object()
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "clients", {"default": client})

    async def _resolve_entity(chat_id, cl):
        return entity

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    monkeypatch.setattr(media, "get_marked_id", lambda _entity: 100200300)
    monkeypatch.setattr(media, "get_entity_type", lambda _entity: "User")
    return monkeypatch


@pytest.mark.asyncio
async def test_list_photos_returns_text_only_and_exposes_openable_ids(patched_media):
    async def _list(cl, entity, source, limit):
        return [_reference(200, is_current=True), _reference(100)]

    patched_media.setattr(media, "list_photo_references", _list)

    result = await media.list_photos("example_user")

    assert isinstance(result, str)
    indexed = json.loads(result)
    assert indexed["source"] == "avatars"
    assert indexed["count"] == 2
    assert [photo["id"] for photo in indexed["photos"]] == [200, 100]
    assert indexed["photos"][0]["is_current"] is True


@pytest.mark.asyncio
async def test_list_photos_sanitizes_captions(patched_media):
    async def _list(cl, entity, source, limit):
        return [_reference(5, caption="hello")]

    patched_media.setattr(media, "list_photo_references", _list)
    patched_media.setattr(media, "sanitize_user_content", lambda text, max_length: "SANITIZED")

    indexed = json.loads(await media.list_photos("chat", source="messages"))

    assert indexed["photos"][0]["caption"] == "SANITIZED"


@pytest.mark.asyncio
async def test_list_photos_rejects_an_unknown_source_without_calling_telegram(patched_media):
    result = await media.list_photos("chat", source="stories")

    assert "Unknown photo source" in result
    assert "avatars" in result


@pytest.mark.asyncio
async def test_open_photo_returns_viewable_image(patched_media):
    async def _find(cl, entity, source, identifier, depth):
        assert source == "avatars"
        assert identifier == 200
        return _reference(200, is_current=True)

    async def _download(cl, reference, thumbnail=False):
        assert thumbnail is False
        return _jpeg_bytes()

    patched_media.setattr(media, "find_photo_reference", _find)
    patched_media.setattr(media, "download_photo_bytes", _download)

    result = await media.open_photo("example_user", photo_id=200)

    assert isinstance(result, Image)
    assert result._mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_open_photo_with_message_id_selects_the_message_source(patched_media):
    seen = {}

    async def _find(cl, entity, source, identifier, depth):
        seen["source"] = source
        seen["identifier"] = identifier
        return _reference(9001)

    patched_media.setattr(media, "find_photo_reference", _find)
    patched_media.setattr(
        media, "download_photo_bytes", lambda cl, ref, thumbnail=False: _async(_jpeg_bytes())
    )

    await media.open_photo("chat", message_id=9001)

    assert seen == {"source": "messages", "identifier": 9001}


def _async(value):
    async def _coroutine():
        return value

    return _coroutine()


@pytest.mark.asyncio
async def test_open_photo_reports_a_missing_identifier_instead_of_raising(patched_media):
    async def _find(cl, entity, source, identifier, depth):
        return None

    patched_media.setattr(media, "find_photo_reference", _find)

    result = await media.open_photo("example_user", photo_id=404)

    assert "No avatars photo found" in result
    assert "404" in result


@pytest.mark.asyncio
async def test_open_photo_save_path_keeps_a_copy_inside_allowed_roots(patched_media, tmp_path):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    patched_media.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])

    async def _find(cl, entity, source, identifier, depth):
        return _reference(200, is_current=True)

    patched_media.setattr(media, "find_photo_reference", _find)
    patched_media.setattr(
        media, "download_photo_bytes", lambda cl, ref, thumbnail=False: _async(_jpeg_bytes())
    )

    result = await media.open_photo("example_user", save_path="kept.jpg")

    assert isinstance(result, Image)
    assert (root / "kept.jpg").read_bytes() == _jpeg_bytes()


@pytest.mark.asyncio
async def test_open_photo_refuses_a_save_path_outside_allowed_roots(patched_media, tmp_path):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    patched_media.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])

    async def _find(cl, entity, source, identifier, depth):
        return _reference(200)

    patched_media.setattr(media, "find_photo_reference", _find)
    patched_media.setattr(
        media, "download_photo_bytes", lambda cl, ref, thumbnail=False: _async(_jpeg_bytes())
    )

    result = await media.open_photo("example_user", save_path=str(tmp_path / "outside.jpg"))

    assert "outside allowed roots" in result


@pytest.mark.asyncio
async def test_photo_sheet_returns_one_labelled_image_with_a_text_preamble(patched_media):
    async def _list(cl, entity, source, limit):
        return [_reference(200, is_current=True), _reference(100)]

    async def _download(cl, reference, thumbnail=False):
        assert thumbnail is True
        return _jpeg_bytes()

    patched_media.setattr(media, "list_photo_references", _list)
    patched_media.setattr(media, "download_photo_bytes", _download)

    preamble, sheet = await media.get_photo_sheet("example_user")

    assert "2 avatars photo(s)" in preamble
    assert isinstance(sheet, Image)
    with PillowImage.open(io.BytesIO(sheet.data)) as opened:
        assert opened.width == 2 * contact_sheet.CELL_EDGE_PIXELS


@pytest.mark.asyncio
async def test_photo_sheet_reports_an_empty_peer_rather_than_failing(patched_media):
    async def _list(cl, entity, source, limit):
        return []

    patched_media.setattr(media, "list_photo_references", _list)

    result = await media.get_photo_sheet("silent_channel")

    assert "No avatars photos found" in result


@pytest.mark.asyncio
async def test_photo_sheet_degrades_politely_when_pillow_is_missing(patched_media):
    async def _list(cl, entity, source, limit):
        return [_reference(200)]

    def _unavailable(tiles, columns):
        raise contact_sheet.ContactSheetUnavailable("Pillow is required to build contact sheets.")

    patched_media.setattr(media, "list_photo_references", _list)
    patched_media.setattr(
        media, "download_photo_bytes", lambda cl, ref, thumbnail=False: _async(_jpeg_bytes())
    )
    patched_media.setattr(media, "build_contact_sheet", _unavailable)

    result = await media.get_photo_sheet("example_user")

    assert "Pillow is required" in result


@pytest.mark.asyncio
async def test_photo_sheet_caps_the_number_of_tiles_it_will_request(patched_media):
    requested = {}

    async def _list(cl, entity, source, limit):
        requested["limit"] = limit
        return [_reference(1)]

    patched_media.setattr(media, "list_photo_references", _list)
    patched_media.setattr(
        media, "download_photo_bytes", lambda cl, ref, thumbnail=False: _async(_jpeg_bytes())
    )

    await media.get_photo_sheet("example_user", limit=500)

    assert requested["limit"] == media.PHOTO_SHEET_MAXIMUM_TILES


@pytest.mark.parametrize("tool_name", ["list_photos", "open_photo", "get_photo_sheet"])
def test_photo_tools_survive_read_only_exposure(tool_name):
    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    assert registered[tool_name].annotations.readOnlyHint is True
