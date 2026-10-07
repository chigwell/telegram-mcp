import io
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfWriter
from mcp.server.fastmcp import Image

from telegram_mcp.tools import media

_inspect_document = inspect.unwrap(media.inspect_document)


def _make_dummy_pdf(text: str = "") -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


class MockClient:
    def __init__(self, message=None, media_bytes=b""):
        self.message = message
        self.media_bytes = media_bytes

    async def get_messages(self, entity, ids):
        return self.message

    async def download_media(self, msg, file):
        assert file is bytes
        return self.media_bytes


@pytest.mark.asyncio
async def test_inspect_document_no_media(monkeypatch):
    msg = SimpleNamespace(id=1, media=None)
    client = MockClient(message=msg)
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="entity"))

    result = await _inspect_document(123, 1)
    assert "нет прикрепленного документа" in result


@pytest.mark.asyncio
async def test_inspect_document_image(monkeypatch):
    img_data = b"\x89PNG\r\n\x1a\nfakeimage"
    msg = SimpleNamespace(
        id=2,
        media=object(),
        file=SimpleNamespace(name="photo.png", mime_type="image/png"),
    )
    client = MockClient(message=msg, media_bytes=img_data)
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="entity"))

    result = await _inspect_document(123, 2)
    assert isinstance(result, Image)
    assert result.data == img_data
    assert result._format == "png"


@pytest.mark.asyncio
async def test_inspect_document_text(monkeypatch):
    txt_data = "Hello, world! Привет мир!".encode("utf-8")
    msg = SimpleNamespace(
        id=3,
        media=object(),
        file=SimpleNamespace(name="notes.txt", mime_type="text/plain"),
    )
    client = MockClient(message=msg, media_bytes=txt_data)
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="entity"))

    result = await _inspect_document(123, 3)
    assert result == "Hello, world! Привет мир!"


@pytest.mark.asyncio
async def test_inspect_document_pdf(monkeypatch):
    pdf_bytes = _make_dummy_pdf()
    msg = SimpleNamespace(
        id=4,
        media=object(),
        file=SimpleNamespace(name="contract.pdf", mime_type="application/pdf"),
    )
    client = MockClient(message=msg, media_bytes=pdf_bytes)
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="entity"))

    # Test PDF without text layer
    result = await _inspect_document(123, 4)
    assert "не содержит текстового слоя" in result

    # Test PDF with extracted text by mocking PdfReader.pages
    class MockPage:
        def extract_text(self):
            return "Sample Contract Text Line 1"

    class MockReader:
        def __init__(self, stream):
            self.pages = [MockPage()]

    monkeypatch.setattr(media, "PdfReader", MockReader)
    result = await _inspect_document(123, 4)
    assert "Содержимое документа 'contract.pdf' (1 стр.):" in result
    assert "Sample Contract Text Line 1" in result


@pytest.mark.asyncio
async def test_inspect_document_unsupported(monkeypatch):
    msg = SimpleNamespace(
        id=5,
        media=object(),
        file=SimpleNamespace(name="archive.zip", mime_type="application/zip"),
    )
    client = MockClient(message=msg, media_bytes=b"zipdata")
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="entity"))

    result = await _inspect_document(123, 5)
    assert "пока не поддерживается" in result


def test_inspect_document_is_registered_and_read_only():
    from telegram_mcp import runtime

    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    assert "inspect_document" in registered
    assert registered["inspect_document"].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_inspect_document_zero_disk_footprint(monkeypatch, tmp_path):
    download_targets = []

    async def mock_download_media(msg, file):
        download_targets.append(file)
        return "Line 1\nLine 2".encode("utf-8")

    client = SimpleNamespace(
        get_messages=AsyncMock(
            return_value=SimpleNamespace(
                id=10,
                media=object(),
                file=SimpleNamespace(name="doc.txt", mime_type="text/plain"),
            )
        ),
        download_media=mock_download_media,
    )
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "resolve_entity", AsyncMock(return_value="entity"))

    before_files = list(tmp_path.iterdir())
    res = await _inspect_document(123, 10)
    after_files = list(tmp_path.iterdir())

    assert res == "Line 1\nLine 2"
    assert download_targets == [bytes]  # Strict RAM-only download
    assert before_files == after_files  # Zero disk pollution
