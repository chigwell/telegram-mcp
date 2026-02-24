import os
from pathlib import Path

import pytest
from mcp import types

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "dummy_hash")

import main


class _DummySession:
    def __init__(self, roots):
        self._roots = roots

    async def list_roots(self):
        return types.ListRootsResult(roots=self._roots)


class _DummyContext:
    def __init__(self, roots):
        self.session = _DummySession(roots)


@pytest.mark.asyncio
async def test_readable_relative_path_resolves_inside_first_server_root(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir(parents=True)
    target = root / "document.txt"
    target.write_text("ok", encoding="utf-8")

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [root])

    resolved, error = await main._resolve_readable_file_path(
        raw_path="document.txt",
        ctx=None,
        tool_name="send_file",
    )

    assert error is None
    assert resolved == target.resolve()


@pytest.mark.asyncio
async def test_readable_path_rejects_traversal(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir(parents=True)
    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [root])

    resolved, error = await main._resolve_readable_file_path(
        raw_path="../etc/passwd",
        ctx=None,
        tool_name="send_file",
    )

    assert resolved is None
    assert error == "Path traversal is not allowed."


@pytest.mark.asyncio
async def test_readable_path_rejects_outside_root(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    outside_root = (tmp_path / "outside").resolve()
    root.mkdir(parents=True)
    outside_root.mkdir(parents=True)

    outside_file = outside_root / "outside.txt"
    outside_file.write_text("no", encoding="utf-8")

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [root])

    resolved, error = await main._resolve_readable_file_path(
        raw_path=str(outside_file),
        ctx=None,
        tool_name="send_file",
    )

    assert resolved is None
    assert error == "Path is outside allowed roots."


@pytest.mark.asyncio
async def test_client_roots_replace_server_allowlist(tmp_path, monkeypatch):
    server_root = (tmp_path / "server_root").resolve()
    client_root = (tmp_path / "client_root").resolve()
    server_root.mkdir(parents=True)
    client_root.mkdir(parents=True)

    (server_root / "server.txt").write_text("server", encoding="utf-8")
    client_file = client_root / "client.txt"
    client_file.write_text("client", encoding="utf-8")

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [server_root])
    ctx = _DummyContext([types.Root(uri=client_root.as_uri())])

    roots = await main._get_effective_allowed_roots(ctx)
    assert roots == [client_root]

    resolved, error = await main._resolve_readable_file_path(
        raw_path="client.txt",
        ctx=ctx,
        tool_name="send_file",
    )
    assert error is None
    assert resolved == client_file.resolve()


@pytest.mark.asyncio
async def test_writable_default_path_uses_downloads_subdir(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir(parents=True)
    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [root])

    resolved, error = await main._resolve_writable_file_path(
        raw_path=None,
        default_filename="example.bin",
        ctx=None,
        tool_name="download_media",
    )

    assert error is None
    assert resolved == (root / "downloads" / "example.bin").resolve()
    assert resolved.parent.exists()


@pytest.mark.asyncio
async def test_extension_allowlist_is_enforced_for_sticker(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir(parents=True)
    file_path = root / "sticker.txt"
    file_path.write_text("bad", encoding="utf-8")

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [root])

    resolved, error = await main._resolve_readable_file_path(
        raw_path=str(file_path),
        ctx=None,
        tool_name="send_sticker",
    )

    assert resolved is None
    assert error is not None
    assert "extension is not allowed" in error


@pytest.mark.asyncio
async def test_file_tools_disabled_without_any_roots(monkeypatch):
    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [])

    resolved, error = await main._resolve_readable_file_path(
        raw_path="anything.txt",
        ctx=None,
        tool_name="send_file",
    )

    assert resolved is None
    assert error is not None
    assert "disabled" in error
