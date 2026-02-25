import os
from pathlib import Path

import pytest
from mcp import types
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

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


class _FailingSession:
    def __init__(self, error):
        self._error = error

    async def list_roots(self):
        raise self._error


class _FailingContext:
    def __init__(self, error):
        self.session = _FailingSession(error)


class _MissingRootsSession:
    pass


class _MissingRootsContext:
    def __init__(self):
        self.session = _MissingRootsSession()


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
async def test_empty_client_roots_disable_file_tools(tmp_path, monkeypatch):
    server_root = (tmp_path / "server_root").resolve()
    server_root.mkdir(parents=True)

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [server_root])
    ctx = _DummyContext([])

    roots = await main._get_effective_allowed_roots(ctx)
    assert roots == []

    resolved, error = await main._resolve_readable_file_path(
        raw_path="server.txt",
        ctx=ctx,
        tool_name="send_file",
    )
    assert resolved is None
    assert error is not None
    assert "empty MCP Roots list" in error
    assert "deny-all" in error


@pytest.mark.asyncio
async def test_mcp_method_not_found_falls_back_to_server_allowlist(tmp_path, monkeypatch):
    server_root = (tmp_path / "server_root").resolve()
    server_root.mkdir(parents=True)

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [server_root])
    ctx = _FailingContext(McpError(ErrorData(code=-32601, message="Method not found")))

    roots = await main._get_effective_allowed_roots(ctx)
    assert roots == [server_root]


@pytest.mark.asyncio
async def test_missing_list_roots_method_falls_back_to_server_allowlist(tmp_path, monkeypatch):
    server_root = (tmp_path / "server_root").resolve()
    server_root.mkdir(parents=True)

    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [server_root])
    ctx = _MissingRootsContext()

    roots = await main._get_effective_allowed_roots(ctx)
    assert roots == [server_root]


@pytest.mark.asyncio
async def test_unexpected_roots_error_disables_file_path_tools(tmp_path, monkeypatch):
    server_root = (tmp_path / "server_root").resolve()
    server_root.mkdir(parents=True)
    monkeypatch.setattr(main, "SERVER_ALLOWED_ROOTS", [server_root])

    ctx = _FailingContext(RuntimeError("transport failure"))
    roots = await main._get_effective_allowed_roots(ctx)
    assert roots == []

    resolved, error = await main._resolve_readable_file_path(
        raw_path="anything.txt",
        ctx=ctx,
        tool_name="send_file",
    )
    assert resolved is None
    assert error is not None
    assert "disabled" in error


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
