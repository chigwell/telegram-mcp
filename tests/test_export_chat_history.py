"""Offline tests for export_chat_history (Issue #306)."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import messages


def _fake_message(msg_id, text="hello", date=None):
    """Minimal Telethon-like message that message_to_dict can serialize."""
    msg_date = date or datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        id=msg_id,
        date=msg_date,
        message=text,
        sender=SimpleNamespace(
            first_name="Alice", last_name="Smith", title=None, username="alice"
        ),
        sender_id=42,
        out=False,
        reply_to=None,
        fwd_from=None,
        media=None,
        photo=None,
        document=None,
        web_preview=None,
        grouped_id=None,
        reactions=None,
        entities=None,
        rich_message=None,
        sticker=None,
        voice=None,
        video_note=None,
        video=None,
        audio=None,
        gif=None,
        file=None,
        via_bot_id=None,
        edit_date=None,
        pinned=False,
        views=None,
        forwards=None,
        replies=None,
        buttons=None,
        action=None,
        ttl_period=None,
        forward=None,
    )


class FakeChatHistoryClient:
    """Async Telethon client stub: get_messages with pagination and offset support."""

    def __init__(self, messages_by_chat=None):
        self._messages_by_chat = dict(messages_by_chat or {})
        self.get_messages_calls = []

    async def get_messages(self, entity, limit=100, offset_id=0, offset_date=None, **kwargs):
        self.get_messages_calls.append(
            {
                "entity": entity,
                "limit": limit,
                "offset_id": offset_id,
                "offset_date": offset_date,
            }
        )
        chat_id = getattr(entity, "id", entity)
        all_msgs = self._messages_by_chat.get(chat_id, [])

        # Filter by offset_date if provided
        filtered = all_msgs
        if offset_date is not None:
            filtered = [m for m in filtered if m.date < offset_date]

        # Filter by offset_id: messages older than offset_id (assuming newest to oldest, IDs descending)
        if offset_id > 0:
            filtered = [m for m in filtered if m.id < offset_id]

        return filtered[:limit]


def _patch_export(monkeypatch, client, root):
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root.resolve()])
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "is_chat_allowlist_enabled", lambda: False)
    monkeypatch.setattr(messages, "get_marked_id", lambda entity: entity.id)

    async def fake_resolve(raw_id, cl):
        assert cl is client
        return SimpleNamespace(id=int(raw_id), title=f"Chat {raw_id}")

    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)


def _summary(result):
    payload = json.loads(result)
    assert "GEN-ERR" not in result
    return payload


@pytest.mark.asyncio
async def test_export_chat_history_full_pagination(monkeypatch, tmp_path):
    # 250 messages: pages 100 + 100 + 50
    msgs = [_fake_message(i) for i in range(250, 0, -1)]
    client = FakeChatHistoryClient(messages_by_chat={1001: msgs})
    _patch_export(monkeypatch, client, tmp_path)

    out_file = tmp_path / "history.json"
    raw_res = await messages.export_chat_history(chat_id=1001, output_path=str(out_file))
    res = _summary(raw_res)

    assert res["status"] == "ok"
    assert res["chats_processed"] == 1
    assert res["chats_skipped"] == 0
    assert res["messages_exported"] == 250
    assert len(client.get_messages_calls) == 3

    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    chat_data = data["chats"]["1001"]
    assert chat_data["chat_id"] == 1001
    assert chat_data["chat_title"] == "Chat 1001"
    assert chat_data["messages_exported"] == 250
    assert len(chat_data["messages"]) == 250


@pytest.mark.asyncio
async def test_export_chat_history_with_limit(monkeypatch, tmp_path):
    msgs = [_fake_message(i) for i in range(100, 0, -1)]
    client = FakeChatHistoryClient(messages_by_chat={1002: msgs})
    _patch_export(monkeypatch, client, tmp_path)

    out_file = tmp_path / "limited.json"
    raw_res = await messages.export_chat_history(chat_id=1002, output_path=str(out_file), limit=25)
    res = _summary(raw_res)

    assert res["messages_exported"] == 25
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert len(data["chats"]["1002"]["messages"]) == 25


@pytest.mark.asyncio
async def test_export_chat_history_offset_date(monkeypatch, tmp_path):
    d1 = datetime(2026, 3, 1, tzinfo=timezone.utc)
    d2 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    msgs = [_fake_message(10, date=d1), _fake_message(9, date=d2)]
    client = FakeChatHistoryClient(messages_by_chat={1003: msgs})
    _patch_export(monkeypatch, client, tmp_path)

    out_file = tmp_path / "date_filtered.json"
    raw_res = await messages.export_chat_history(
        chat_id=1003,
        output_path=str(out_file),
        offset_date="2026-02-01T00:00:00Z",
    )
    res = _summary(raw_res)

    assert res["messages_exported"] == 1
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["chats"]["1003"]["messages"][0]["id"] == 9


@pytest.mark.asyncio
async def test_export_chat_history_invalid_date(monkeypatch, tmp_path):
    client = FakeChatHistoryClient()
    _patch_export(monkeypatch, client, tmp_path)

    raw_res = await messages.export_chat_history(
        chat_id=1004,
        output_path=str(tmp_path / "err.json"),
        offset_date="invalid-date-format",
    )
    assert "Error: offset_date must be in ISO format" in raw_res


@pytest.mark.asyncio
async def test_export_chat_history_resume(monkeypatch, tmp_path):
    msgs = [_fake_message(1)]
    client = FakeChatHistoryClient(messages_by_chat={1005: msgs})
    _patch_export(monkeypatch, client, tmp_path)

    out_file = tmp_path / "resume.json"
    # First export
    await messages.export_chat_history(chat_id=1005, output_path=str(out_file), resume=True)

    # Second export with resume=True
    raw_res2 = await messages.export_chat_history(
        chat_id=1005, output_path=str(out_file), resume=True
    )
    res2 = _summary(raw_res2)

    assert res2["chats_processed"] == 0
    assert res2["chats_skipped"] == 1
    assert res2["messages_exported"] == 0


@pytest.mark.asyncio
async def test_export_chat_history_empty_chat(monkeypatch, tmp_path):
    client = FakeChatHistoryClient(messages_by_chat={1006: []})
    _patch_export(monkeypatch, client, tmp_path)

    out_file = tmp_path / "empty.json"
    raw_res = await messages.export_chat_history(chat_id=1006, output_path=str(out_file))
    res = _summary(raw_res)

    assert res["status"] == "ok"
    assert res["messages_exported"] == 0
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["chats"]["1006"]["messages"] == []


@pytest.mark.asyncio
async def test_export_chat_history_multiple_chats(monkeypatch, tmp_path):
    client = FakeChatHistoryClient(
        messages_by_chat={
            2001: [_fake_message(1)],
            2002: [_fake_message(2), _fake_message(3)],
        }
    )
    _patch_export(monkeypatch, client, tmp_path)

    out_file = tmp_path / "multi.json"
    raw_res = await messages.export_chat_history(chat_id=[2001, 2002], output_path=str(out_file))
    res = _summary(raw_res)

    assert res["chats_processed"] == 2
    assert res["messages_exported"] == 3
    with open(out_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert "2001" in data["chats"]
    assert "2002" in data["chats"]
