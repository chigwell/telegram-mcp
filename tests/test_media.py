import pytest
from telegram_mcp import media


@pytest.mark.asyncio
async def test_send_file():
    res = await media.send_file(123, "/tmp/dummy.txt")
    assert res is not None


@pytest.mark.asyncio
async def test_download_media():
    res = await media.download_media(123, 1)
    assert res is not None


@pytest.mark.asyncio
async def test_set_profile_photo():
    res = await media.set_profile_photo("/tmp/dummy.jpg")
    assert res is not None


@pytest.mark.asyncio
async def test_edit_chat_photo():
    res = await media.edit_chat_photo(123, "/tmp/dummy.jpg")
    assert res is not None


@pytest.mark.asyncio
async def test_send_voice():
    res = await media.send_voice(123, "/tmp/dummy.ogg")
    assert res is not None


@pytest.mark.asyncio
async def test_upload_file():
    res = await media.upload_file("/tmp/dummy.txt")
    assert res is not None
