"""Tests for what the export downloads and what it refuses to download.

Both of these were found by running a real export, not by reading the code:
a chat full of links pulled the linked videos down as "photos", and the size
limit did nothing to stop a 25 MB file because the message declared 48 KB.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import types as tl_types

from telegram_mcp.export import media as media_mod


def _message(**kwargs):
    """A message with every media attribute absent unless named."""
    fields = dict(
        id=1,
        media=object(),
        photo=None,
        voice=None,
        video_note=None,
        sticker=None,
        gif=None,
        video=None,
        audio=None,
        contact=None,
        document=None,
        web_preview=None,
        file=SimpleNamespace(mime_type=None, size=None, name=None, ext=None),
    )
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class _Client:
    """A client that writes ``payload`` and reports progress the way Telethon does."""

    def __init__(self, payload: bytes, chunk: int = 1024, fail=None):
        self.payload = payload
        self.chunk = chunk
        self.fail = fail

    async def download_media(self, message, file, progress_callback=None):
        if self.fail is not None:
            Path(file).write_bytes(self.payload[: self.chunk])  # a partial file
            raise self.fail
        written = 0
        with open(file, "wb") as handle:
            for start in range(0, len(self.payload), self.chunk):
                block = self.payload[start : start + self.chunk]
                handle.write(block)
                handle.flush()
                written += len(block)
                if progress_callback is not None:
                    progress_callback(written, len(self.payload))
        return file


# ---------------------------------------------------------------------------
# What counts as the chat's media
# ---------------------------------------------------------------------------


def _preview(webpage, **kwargs):
    return _message(media=tl_types.MessageMediaWebPage(webpage=webpage), **kwargs)


def test_a_link_preview_is_not_the_chats_media():
    # Telethon surfaces the preview's own photo through `.photo`, so a message
    # that is only a link looks exactly like a photo message.
    page = tl_types.WebPageEmpty(id=0)
    assert media_mod.classify(_preview(page, web_preview=page, photo=object())) is None


def test_a_link_preview_of_a_video_is_not_downloaded_as_a_photo():
    page = tl_types.WebPageEmpty(id=0)
    preview = _preview(page, web_preview=page, photo=object(), document=object())
    # The bug this pins: classified as "photo", the export downloaded the whole
    # linked video and saved 25 MB of MP4 under a .jpg name.
    assert media_mod.classify(preview) != "photo"


def test_an_unresolved_link_preview_is_not_media_either():
    # Telegram had not fetched the page yet, so Telethon's `.web_preview` is
    # None. The message is still nothing but a link, and calling it "other"
    # hangs a media note on plain text.
    unresolved = _preview(tl_types.WebPageEmpty(id=0), web_preview=None)
    assert media_mod.classify(unresolved) is None


def test_a_real_photo_is_still_a_photo():
    assert media_mod.classify(_message(photo=object())) == "photo"


def test_a_text_only_message_has_no_media():
    assert media_mod.classify(_message(media=None)) is None


# ---------------------------------------------------------------------------
# The size limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_media_that_understates_its_size_is_still_capped(tmp_path):
    root = tmp_path / "export" / "media"
    info = {"kind": "photo", "size": 48405, "ext": ".jpg", "name": None, "skipped": None}
    client = _Client(b"x" * (5 * 1024 * 1024))

    out = await media_mod.download(client, _message(), info, root, max_bytes=1024 * 1024)

    assert not out.get("file")
    assert "larger than limit" in out["skipped"]
    # Nothing oversized is left behind pretending to be the media.
    assert not any(p.is_file() for p in root.rglob("*"))


@pytest.mark.asyncio
async def test_a_download_under_the_limit_is_kept(tmp_path):
    root = tmp_path / "export" / "media"
    info = {"kind": "photo", "size": 2048, "ext": ".jpg", "name": None, "skipped": None}
    client = _Client(b"x" * 2048)

    out = await media_mod.download(client, _message(), info, root, max_bytes=1024 * 1024)

    assert out["skipped"] is None
    assert (root.parent / out["file"]).stat().st_size == 2048


@pytest.mark.asyncio
async def test_a_declared_size_over_the_limit_costs_no_download(tmp_path):
    root = tmp_path / "export" / "media"
    info = {"kind": "video", "size": 9_000_000, "ext": ".mp4", "name": None, "skipped": None}

    class _Refuses:
        async def download_media(self, *args, **kwargs):
            raise AssertionError("the limit should have been decided before the network")

    out = await media_mod.download(_Refuses(), _message(), info, root, max_bytes=1024 * 1024)

    assert "larger than limit" in out["skipped"]


@pytest.mark.asyncio
async def test_a_failed_download_leaves_no_partial_file(tmp_path):
    root = tmp_path / "export" / "media"
    info = {"kind": "photo", "size": 2048, "ext": ".jpg", "name": None, "skipped": None}
    client = _Client(b"x" * 2048, fail=OSError("connection reset"))

    out = await media_mod.download(client, _message(), info, root, max_bytes=None)

    assert "download failed" in out["skipped"]
    # A leftover stub would be taken for the real file by the next resume.
    assert not any(p.is_file() for p in root.rglob("*"))


@pytest.mark.asyncio
async def test_no_limit_means_no_progress_guard(tmp_path):
    """Without a limit the download must not pay for a per-chunk callback."""
    root = tmp_path / "export" / "media"
    info = {"kind": "photo", "size": None, "ext": ".jpg", "name": None, "skipped": None}
    seen = {}

    class _Records:
        async def download_media(self, message, file, progress_callback=None):
            seen["callback"] = progress_callback
            Path(file).parent.mkdir(parents=True, exist_ok=True)
            Path(file).write_bytes(b"ok")
            return file

    await media_mod.download(_Records(), _message(), info, root, max_bytes=None)

    assert seen["callback"] is None
