"""Deletion outcomes are recorded without sending Telegram requests."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import errors, types
from telethon.sessions import StringSession

from telegram_mcp import runtime
from telegram_mcp.tools import messages


@pytest.fixture
def deletion(monkeypatch, tmp_path):
    path = tmp_path / "audit_log.jsonl"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))
    client = AsyncMock()
    client.session = StringSession()
    client.get_messages.return_value = [SimpleNamespace(message="PRIVATE TEXT")]
    client.return_value = SimpleNamespace(pts_count=1, offset=0)
    monkeypatch.setattr(runtime, "clients", {"personal": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(
        messages, "resolve_entity", AsyncMock(return_value=types.InputPeerChat(12))
    )
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    return client, path


async def invoke(tool, **extra):
    args = {"chat_id": 12}
    args.update({"message_id": 7} if tool == "delete_message" else {})
    args.update({"message_ids": [7], "revoke": False} if tool == "delete_messages_bulk" else {})
    args.update({"max_id": 9, "revoke": True} if tool == "delete_chat_history" else {})
    return await getattr(messages, tool)(**(args | extra))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete_message", "delete_messages_bulk", "delete_chat_history"])
async def test_success_metadata(deletion, tool):
    client, path = deletion
    result = await invoke(tool)
    record = json.loads(path.read_text())
    assert record["outcome"] == "success"
    assert record["reason"] is None
    assert record["account"] == "personal"
    assert record["tool"] == tool
    assert record["chat_id"] == 12
    assert datetime.fromisoformat(record["timestamp"]).utcoffset().total_seconds() == 0
    assert record["revoke"] is (tool != "delete_messages_bulk")
    if tool == "delete_chat_history":
        assert record["max_id"] == 9
        assert "message_ids" not in record
    else:
        assert record["message_ids"] == [7]
    assert "PRIVATE TEXT" not in path.read_text()
    assert "Warning" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete_message", "delete_messages_bulk"])
async def test_foreign_ids_are_logged_without_deletion(deletion, tool):
    client, path = deletion
    client.get_messages.return_value = [None]
    assert "nothing was deleted" in await invoke(tool)
    record = json.loads(path.read_text())
    assert record["outcome"] == "refused"
    assert record["reason"] == "foreign_message_ids"
    assert record["foreign_message_ids"] == [7]
    client.assert_not_awaited()
    client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete_message", "delete_messages_bulk", "delete_chat_history"])
@pytest.mark.parametrize(
    "error_class", [errors.ChatAdminRequiredError, errors.MessageIdInvalidError]
)
async def test_telegram_errors_are_logged(deletion, tool, error_class):
    client, path = deletion
    error = error_class(request=None)
    client.side_effect = error
    client.delete_messages.side_effect = error
    await invoke(tool)
    record = json.loads(path.read_text())
    assert record["outcome"] == "failure"
    assert record["reason"] == error_class.__name__
    assert len(path.read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_raw_error_text_is_excluded(deletion):
    client, path = deletion
    client.delete_messages.side_effect = RuntimeError("SECRET TOKEN PRIVATE TEXT")
    await invoke("delete_message")
    assert json.loads(path.read_text())["reason"] == "RuntimeError"
    assert "SECRET" not in path.read_text()


@pytest.mark.asyncio
async def test_audit_failure_preserves_success_and_does_not_retry(deletion, monkeypatch, caplog):
    client, path = deletion
    monkeypatch.setattr(
        messages.audit_log,
        "append_audit_record",
        lambda *a, **kw: (_ for _ in ()).throw(PermissionError("SECRET")),
    )
    result = await invoke("delete_message")
    assert result.startswith("Message 7 deleted.")
    assert "Warning: the audit record could not be saved." in result
    client.delete_messages.assert_awaited_once()
    assert "Deletion audit write failed" in caplog.text
    assert "SECRET" not in caplog.text


@pytest.mark.asyncio
async def test_file_session_uses_session_directory(deletion, monkeypatch, tmp_path):
    client, _ = deletion
    monkeypatch.delenv("TELEGRAM_AUDIT_LOG")
    client.session = SimpleNamespace(filename=str(tmp_path / "sessions/personal.session"))
    await invoke("delete_message", account="personal")
    record = json.loads((tmp_path / "sessions/audit_log.jsonl").read_text())
    assert record["account"] == "personal"
