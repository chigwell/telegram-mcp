from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import media


@pytest.fixture
def edit_client(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    replacement = root / "replacement.png"
    replacement.write_bytes(b"image")
    (root / "replacement.mp4").write_bytes(b"video")

    client = AsyncMock()
    client.get_messages.return_value = SimpleNamespace(
        message="old caption", entities=["formatting entity"], grouped_id=7
    )
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def resolve_entity(chat_id, cl):
        return f"entity:{chat_id}"

    monkeypatch.setattr(media, "resolve_entity", resolve_entity)
    return client, replacement


@pytest.mark.asyncio
async def test_edit_album_item_preserves_caption_and_formatting(edit_client):
    client, replacement = edit_client

    result = await media.edit_message_media(
        chat_id=42, message_id=7, file_path="replacement.png", account="test"
    )

    assert result == "Media in message 7 replaced."
    client.get_messages.assert_awaited_once_with("entity:42", ids=7)
    client.edit_message.assert_awaited_once_with(
        "entity:42",
        7,
        "old caption",
        file=str(replacement),
        parse_mode=None,
        formatting_entities=["formatting entity"],
    )


@pytest.mark.asyncio
async def test_empty_caption_clears_caption(edit_client):
    client, replacement = edit_client

    result = await media.edit_message_media(
        chat_id=42,
        message_id=7,
        file_path="replacement.png",
        caption="",
        account="test",
    )

    assert result == "Media in message 7 replaced."
    client.edit_message.assert_awaited_once_with("entity:42", 7, "", file=str(replacement))


@pytest.mark.asyncio
async def test_caption_can_be_replaced_with_parse_mode(edit_client):
    client, replacement = edit_client

    result = await media.edit_message_media(
        chat_id=42,
        message_id=7,
        file_path="replacement.png",
        caption="**new**",
        parse_mode="md",
        account="test",
    )

    assert result == "Media in message 7 replaced."
    client.edit_message.assert_awaited_once_with(
        "entity:42", 7, "**new**", file=str(replacement), parse_mode="md"
    )


@pytest.mark.asyncio
async def test_video_replacement_passes_video_attributes(edit_client, monkeypatch):
    client, replacement = edit_client
    video_attributes = object()
    monkeypatch.setattr(media, "_video_send_attributes", lambda path: video_attributes)

    result = await media.edit_message_media(
        chat_id=42,
        message_id=7,
        file_path="replacement.mp4",
        caption="new caption",
        account="test",
    )

    assert result == "Media in message 7 replaced."
    client.edit_message.assert_awaited_once_with(
        "entity:42",
        7,
        "new caption",
        file=str(replacement.parent / "replacement.mp4"),
        attributes=[video_attributes],
    )


@pytest.mark.asyncio
async def test_plain_caption_mode_disables_telethon_parsing(edit_client):
    client, replacement = edit_client

    result = await media.edit_message_media(
        chat_id=42,
        message_id=7,
        file_path="replacement.png",
        caption="literal **text**",
        parse_mode="plain",
        account="test",
    )

    assert result == "Media in message 7 replaced."
    client.edit_message.assert_awaited_once_with(
        "entity:42", 7, "literal **text**", file=str(replacement), parse_mode=None
    )


@pytest.mark.asyncio
async def test_rich_caption_mode_is_rejected(edit_client):
    client, _ = edit_client

    result = await media.edit_message_media(
        chat_id=42,
        message_id=7,
        file_path="replacement.png",
        caption="**new**",
        parse_mode="rich",
        account="test",
    )

    assert (
        result
        == "parse_mode='rich' is not supported for file captions. Use 'md', 'html' or 'plain'."
    )
    client.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_media_rejects_paths_outside_allowed_roots(edit_client, tmp_path):
    client, _ = edit_client
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"image")

    result = await media.edit_message_media(
        chat_id=42, message_id=7, file_path=str(outside), account="test"
    )

    assert result == "Path is outside allowed roots."
    client.get_messages.assert_not_awaited()
    client.edit_message.assert_not_awaited()
