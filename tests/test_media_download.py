import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_mcp.tools import media

_download_media = inspect.unwrap(media.download_media)


async def _call_download(monkeypatch, target, client, message):
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        assert cl is client
        return "entity"

    async def _resolve_path(**kwargs):
        raw_path = kwargs["raw_path"]
        return (Path(raw_path).resolve() if raw_path else target), None

    async def _allowed_roots(ctx, tool_name):
        return [target.parent], None

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    monkeypatch.setattr(media, "_resolve_writable_file_path", _resolve_path)
    monkeypatch.setattr(media, "_ensure_allowed_roots", _allowed_roots)
    return await _download_media(123, message.id, file_path=str(target))


async def _call_default_download(monkeypatch, root, client, message):
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        assert cl is client
        return "entity"

    async def _resolve_path(**kwargs):
        raw_path = kwargs["raw_path"]
        if raw_path:
            return Path(raw_path).resolve(), None
        downloads = root / "downloads"
        downloads.mkdir(exist_ok=True)
        return (downloads / kwargs["default_filename"]).resolve(), None

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    monkeypatch.setattr(media, "_resolve_writable_file_path", _resolve_path)
    return await _download_media(123, message.id)


def _message(size):
    return SimpleNamespace(id=7, media=object(), file=SimpleNamespace(size=size))


class _Client:
    def __init__(self, message, download):
        self.message = message
        self._download = download
        self.download_calls = 0

    async def get_messages(self, entity, ids):
        assert entity == "entity"
        assert ids == self.message.id
        return self.message

    async def download_media(self, message, file, **kwargs):
        self.download_calls += 1
        return await self._download(Path(file), kwargs)


@pytest.mark.asyncio
async def test_default_download_paths_do_not_collide_when_time_is_frozen(tmp_path, monkeypatch):
    message = _message(size=3)
    payloads = iter((b"one", b"two"))

    async def successful_download(file, kwargs):
        downloaded = Path(f"{file}.webp")
        downloaded.write_bytes(next(payloads))
        return str(downloaded)

    client = _Client(message, successful_download)
    monkeypatch.setattr(media.time, "time", lambda: 1_700_000_000)

    first = await _call_default_download(monkeypatch, tmp_path, client, message)
    second = await _call_default_download(monkeypatch, tmp_path, client, message)

    prefix = "Media downloaded to "
    first_path = Path(first.removeprefix(prefix).removesuffix("."))
    second_path = Path(second.removeprefix(prefix).removesuffix("."))
    assert first_path != second_path
    assert first_path.read_bytes() == b"one"
    assert second_path.read_bytes() == b"two"


@pytest.mark.asyncio
async def test_download_media_rejects_declared_oversize_before_transfer(tmp_path, monkeypatch):
    target = tmp_path / "large.bin"
    message = _message(size=5)

    async def fail_download(file, kwargs):
        raise AssertionError("download must not start")

    client = _Client(message, fail_download)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    result = await _call_download(monkeypatch, target, client, message)

    assert "too large" in result.lower()
    assert client.download_calls == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_media_aborts_in_progress_oversize_and_cleans_staging(
    tmp_path, monkeypatch
):
    target = tmp_path / "bounded.bin"
    message = _message(size=None)

    async def oversized_download(file, kwargs):
        partial = Path(f"{file}.bin")
        partial.write_bytes(b"12345")
        kwargs["progress_callback"](5, 10)
        return str(partial)

    client = _Client(message, oversized_download)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    result = await _call_download(monkeypatch, target, client, message)

    assert "too large" in result.lower()
    assert client.download_calls == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_media_verifies_final_size_and_cleans_staging(tmp_path, monkeypatch):
    target = tmp_path / "stale-size.bin"
    message = _message(size=4)

    async def oversized_download(file, kwargs):
        downloaded = Path(f"{file}.bin")
        downloaded.write_bytes(b"12345")
        return str(downloaded)

    client = _Client(message, oversized_download)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    result = await _call_download(monkeypatch, target, client, message)

    assert "too large" in result.lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_media_cleans_extension_appended_file_after_false_return(
    tmp_path, monkeypatch
):
    target = tmp_path / "missing.bin"
    message = _message(size=3)

    async def failed_download(file, kwargs):
        Path(f"{file}.part").write_bytes(b"123")
        return None

    client = _Client(message, failed_download)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    result = await _call_download(monkeypatch, target, client, message)

    assert "download failed" in result.lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_media_cleans_extension_appended_file_after_exception(
    tmp_path, monkeypatch
):
    target = tmp_path / "broken.bin"
    message = _message(size=3)

    async def failed_download(file, kwargs):
        Path(f"{file}.part").write_bytes(b"123")
        raise RuntimeError("network failed")

    client = _Client(message, failed_download)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    result = await _call_download(monkeypatch, target, client, message)

    assert "error occurred" in result.lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_media_stages_atomically_and_preserves_detected_extension(
    tmp_path, monkeypatch
):
    requested = tmp_path / "photo.jpg"
    final_path = tmp_path / "photo.webp"
    final_path.write_bytes(b"old")
    message = _message(size=3)

    async def successful_download(file, kwargs):
        assert file != requested.with_suffix("")
        assert file.is_relative_to(tmp_path)
        assert final_path.read_bytes() == b"old"
        kwargs["progress_callback"](3, 3)
        downloaded = Path(f"{file}.webp")
        downloaded.write_bytes(b"new")
        assert final_path.read_bytes() == b"old"
        return str(downloaded)

    client = _Client(message, successful_download)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    result = await _call_download(monkeypatch, requested, client, message)

    assert result == f"Media downloaded to {final_path.resolve()}."
    assert final_path.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [final_path]
