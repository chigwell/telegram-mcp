from datetime import datetime, timedelta, timezone

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import media


class _RecordingClient:
    def __init__(self):
        self.calls = []

    async def send_file(self, entity, file, **kwargs):
        self.calls.append({"entity": entity, "file": file, **kwargs})


@pytest.fixture
def client_and_root(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    for name in ("shot.png", "one.png", "two.png"):
        (root / name).write_bytes(b"png")

    client = _RecordingClient()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": client})
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        return f"entity:{chat_id}"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    return client, root


@pytest.mark.asyncio
async def test_single_file_omits_force_document_by_default(client_and_root):
    client, root = client_and_root

    result = await media.send_file("Chat", "shot.png", caption="cap")

    assert result == f"File sent to chat Chat from {root / 'shot.png'}."
    assert client.calls == [
        {
            "entity": "entity:Chat",
            "file": str(root / "shot.png"),
            "caption": "cap",
            "reply_to": None,
            "schedule": None,
        }
    ]


@pytest.mark.asyncio
async def test_single_file_force_document_true_is_passed_through(client_and_root):
    client, root = client_and_root

    result = await media.send_file("Chat", "shot.png", caption="cap", force_document=True)

    assert result == f"File sent to chat Chat from {root / 'shot.png'}."
    assert client.calls == [
        {
            "entity": "entity:Chat",
            "file": str(root / "shot.png"),
            "caption": "cap",
            "reply_to": None,
            "schedule": None,
            "force_document": True,
        }
    ]


@pytest.mark.asyncio
async def test_album_omits_force_document_by_default(client_and_root):
    client, root = client_and_root

    result = await media.send_file("Chat", ["one.png", "two.png"])

    assert result == "Album sent to chat Chat with 2 files."
    assert "force_document" not in client.calls[0]
    assert client.calls[0]["file"] == [str(root / "one.png"), str(root / "two.png")]


@pytest.mark.asyncio
async def test_album_force_document_true_is_passed_through(client_and_root):
    client, root = client_and_root

    result = await media.send_file(
        "Chat", ["one.png", "two.png"], caption="pair", topic_id=9, force_document=True
    )

    assert result == "Album sent to chat Chat with 2 files."
    assert client.calls == [
        {
            "entity": "entity:Chat",
            "file": [str(root / "one.png"), str(root / "two.png")],
            "caption": "pair",
            "reply_to": 9,
            "schedule": None,
            "force_document": True,
        }
    ]


@pytest.mark.asyncio
async def test_force_document_works_with_topic_and_schedule(client_and_root):
    client, _ = client_and_root
    when = datetime.now(timezone.utc) + timedelta(hours=1)

    result = await media.send_file(
        "Chat",
        "shot.png",
        topic_id=5,
        schedule_date=when.isoformat(),
        force_document=True,
    )

    assert "scheduled for" in result
    assert client.calls[0]["reply_to"] == 5
    assert client.calls[0]["schedule"] == when
    assert client.calls[0]["force_document"] is True


@pytest.mark.asyncio
async def test_album_force_document_works_with_schedule(client_and_root):
    client, _ = client_and_root
    when = datetime.now(timezone.utc) + timedelta(hours=1)

    result = await media.send_file(
        "Chat", ["one.png", "two.png"], schedule_date=when.isoformat(), force_document=True
    )

    assert result.startswith("Album of 2 files scheduled for ")
    assert client.calls[0]["schedule"] == when
    assert client.calls[0]["force_document"] is True


@pytest.mark.asyncio
async def test_force_document_does_not_bypass_path_validation(client_and_root):
    client, _ = client_and_root

    result = await media.send_file("Chat", "../outside.png", force_document=True)

    assert result == "Path traversal is not allowed."
    assert client.calls == []
