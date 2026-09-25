"""Offline tests for export_unread_messages (Issue #162)."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import messages


def _fake_message(msg_id, text="hello"):
    """Minimal Telethon-like message that message_to_dict can serialize."""
    return SimpleNamespace(
        id=msg_id,
        date=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
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


class FakeExportClient:
    """Async Telethon client stub: get_messages + get_dialogs only."""

    def __init__(self, dialogs=None, messages_by_chat=None):
        self._dialogs = list(dialogs or [])
        self._messages_by_chat = dict(messages_by_chat or {})
        self.get_messages_calls = []
        self.get_dialogs_calls = []

    async def get_dialogs(self, limit=500):
        self.get_dialogs_calls.append({"limit": limit})
        return list(self._dialogs)

    async def get_messages(self, entity, limit=100, add_offset=0, **kwargs):
        self.get_messages_calls.append(
            {"entity": entity, "limit": limit, "add_offset": add_offset}
        )
        chat_id = getattr(entity, "id", entity)
        msgs = self._messages_by_chat.get(chat_id, [])
        return msgs[add_offset : add_offset + limit]


def _dialog(chat_id, unread_count):
    return SimpleNamespace(
        entity=SimpleNamespace(id=chat_id),
        unread_count=unread_count,
    )


def _patch_export(monkeypatch, client):
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "is_chat_allowlist_enabled", lambda: False)
    monkeypatch.setattr(messages, "get_marked_id", lambda entity: entity.id)

    async def fake_resolve(raw_id, cl):
        assert cl is client
        return SimpleNamespace(id=int(raw_id))

    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)


def _summary(result):
    payload = json.loads(result)
    assert "GEN-ERR" not in result
    return payload


@pytest.mark.asyncio
async def test_export_creates_output_file_with_messages(tmp_path, monkeypatch):
    chat_id = -100123
    msgs = [_fake_message(1, "first"), _fake_message(2, "second")]
    client = FakeExportClient(
        dialogs=[_dialog(chat_id, unread_count=2)],
        messages_by_chat={chat_id: msgs},
    )
    _patch_export(monkeypatch, client)

    out = tmp_path / "out.json"
    result = await messages.export_unread_messages(chat_ids=[chat_id], output_path=str(out))
    summary = _summary(result)

    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "chats" in data
    chat_entry = data["chats"][str(chat_id)]
    assert chat_entry["messages_exported"] == 2
    assert len(chat_entry["messages"]) == 2
    assert summary["messages_exported"] == 2
    assert summary["chats_processed"] == 1


@pytest.mark.asyncio
async def test_export_resume_skips_already_exported_chats(tmp_path, monkeypatch):
    existing_chat = -100123
    new_chat = -100456
    out = tmp_path / "out.json"
    prior = {
        "chats": {
            str(existing_chat): {
                "chat_id": existing_chat,
                "unread_count_at_export": 1,
                "messages_exported": 1,
                "messages": [{"id": 99, "text": "already-exported"}],
                "unique_marker": "keep-me",
            }
        }
    }
    out.write_text(json.dumps(prior), encoding="utf-8")

    client = FakeExportClient(
        dialogs=[
            _dialog(existing_chat, unread_count=5),
            _dialog(new_chat, unread_count=1),
        ],
        messages_by_chat={
            existing_chat: [_fake_message(1, "should-not-be-fetched")],
            new_chat: [_fake_message(10, "new-chat-msg")],
        },
    )
    _patch_export(monkeypatch, client)

    result = await messages.export_unread_messages(
        chat_ids=[existing_chat, new_chat],
        output_path=str(out),
        resume=True,
    )
    summary = _summary(result)

    assert summary["chats_skipped"] == 1
    assert summary["chats_processed"] == 1
    assert summary["messages_exported"] == 1

    data = json.loads(out.read_text(encoding="utf-8"))
    skipped = data["chats"][str(existing_chat)]
    assert skipped["unique_marker"] == "keep-me"
    assert skipped["messages"] == [{"id": 99, "text": "already-exported"}]

    processed = data["chats"][str(new_chat)]
    assert processed["messages_exported"] == 1
    assert processed["messages"][0]["text"] == "new-chat-msg"

    fetched_ids = [call["entity"].id for call in client.get_messages_calls]
    assert existing_chat not in fetched_ids
    assert new_chat in fetched_ids


@pytest.mark.asyncio
async def test_export_no_resume_overwrites(tmp_path, monkeypatch):
    chat_id = -100123
    out = tmp_path / "out.json"
    prior = {
        "chats": {
            str(chat_id): {
                "chat_id": chat_id,
                "unread_count_at_export": 99,
                "messages_exported": 1,
                "messages": [{"id": 999, "text": "PRIOR_DATA_SHOULD_NOT_SURVIVE"}],
                "unique_marker": "old-export",
            }
        }
    }
    out.write_text(json.dumps(prior), encoding="utf-8")

    client = FakeExportClient(
        dialogs=[_dialog(chat_id, unread_count=2)],
        messages_by_chat={chat_id: [_fake_message(1, "fresh-one"), _fake_message(2, "fresh-two")]},
    )
    _patch_export(monkeypatch, client)

    result = await messages.export_unread_messages(
        chat_ids=[chat_id],
        output_path=str(out),
        resume=False,
    )
    summary = _summary(result)

    assert summary["chats_skipped"] == 0
    assert summary["chats_processed"] == 1
    assert summary["messages_exported"] == 2

    data = json.loads(out.read_text(encoding="utf-8"))
    chat_entry = data["chats"][str(chat_id)]
    assert "unique_marker" not in chat_entry
    texts = [m.get("text") for m in chat_entry["messages"]]
    assert "PRIOR_DATA_SHOULD_NOT_SURVIVE" not in texts
    assert texts == ["fresh-one", "fresh-two"]
    assert chat_entry["messages_exported"] == 2
    assert {m["id"] for m in chat_entry["messages"]} == {1, 2}


@pytest.mark.asyncio
async def test_export_returns_json_summary(tmp_path, monkeypatch):
    chat_id = -100123
    client = FakeExportClient(
        dialogs=[_dialog(chat_id, unread_count=2)],
        messages_by_chat={
            chat_id: [_fake_message(1, "a"), _fake_message(2, "b")],
        },
    )
    _patch_export(monkeypatch, client)

    out = tmp_path / "out.json"
    result = await messages.export_unread_messages(chat_ids=[chat_id], output_path=str(out))
    summary = _summary(result)

    assert summary["status"] == "ok"
    assert "chats_processed" in summary
    assert "messages_exported" in summary
    assert "output_path" in summary
    assert summary["chats_processed"] == 1
    assert summary["messages_exported"] == 2
    assert summary["output_path"] == str(out.resolve())


@pytest.mark.asyncio
async def test_export_creates_parent_directories(tmp_path, monkeypatch):
    chat_id = -100123
    client = FakeExportClient(
        dialogs=[_dialog(chat_id, unread_count=1)],
        messages_by_chat={chat_id: [_fake_message(1, "nested")]},
    )
    _patch_export(monkeypatch, client)

    out = tmp_path / "nested" / "dir" / "out.json"
    assert not out.parent.exists()

    result = await messages.export_unread_messages(chat_ids=[chat_id], output_path=str(out))
    summary = _summary(result)

    assert out.parent.is_dir()
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert str(chat_id) in data["chats"]
    assert summary["status"] == "ok"
    assert summary["output_path"] == str(out.resolve())
