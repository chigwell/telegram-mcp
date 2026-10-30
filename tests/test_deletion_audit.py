"""Deletion outcomes are recorded without sending Telegram requests."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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


@pytest.fixture
def broken_audit(monkeypatch):
    writer = Mock(side_effect=PermissionError("SECRET TOKEN /private/audit.jsonl"))
    monkeypatch.setattr(messages.audit_log, "append_audit_record", writer)
    return writer


def assert_safe_audit_warning(result, writer, caplog):
    assert result.endswith(" Warning: the audit record could not be saved.")
    assert result.count("Warning:") == 1
    writer.assert_called_once()
    warnings = [r for r in caplog.records if r.getMessage() == "Deletion audit write failed"]
    assert len(warnings) == 1
    assert warnings[0].levelname == "ERROR"
    assert warnings[0].exc_info is None
    for secret in ("SECRET", "TOKEN", "/private/audit.jsonl"):
        assert secret not in result
        assert secret not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, expected",
    [
        ("delete_message", "Message 7 deleted."),
        ("delete_messages_bulk", "Deleted 1 of 1 messages from chat 12."),
        (
            "delete_chat_history",
            "Chat 12 history cleared for both parties: 1 messages deleted (offset=0).",
        ),
    ],
)
async def test_audit_failure_preserves_success_and_does_not_retry(
    deletion, broken_audit, caplog, tool, expected
):
    client, _ = deletion
    result = await invoke(tool)
    assert result == expected + " Warning: the audit record could not be saved."
    assert_safe_audit_warning(result, broken_audit, caplog)
    assert broken_audit.call_args.args[0]["outcome"] == "success"
    if tool == "delete_message":
        client.delete_messages.assert_awaited_once()
        client.assert_not_awaited()
    else:
        client.assert_awaited_once()
        client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete_message", "delete_messages_bulk"])
async def test_audit_failure_preserves_cross_chat_refusal(deletion, broken_audit, caplog, tool):
    client, _ = deletion
    client.get_messages.return_value = [None]
    result = await invoke(tool)
    assert "nothing was deleted." in result
    assert_safe_audit_warning(result, broken_audit, caplog)
    record = broken_audit.call_args.args[0]
    assert record["outcome"] == "refused"
    assert record["reason"] == "foreign_message_ids"
    client.assert_not_awaited()
    client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete_message", "delete_messages_bulk", "delete_chat_history"])
@pytest.mark.parametrize(
    "error_class", [errors.ChatAdminRequiredError, errors.MessageIdInvalidError]
)
async def test_audit_failure_preserves_telegram_failure_without_retry(
    deletion, broken_audit, caplog, tool, error_class
):
    client, _ = deletion
    client.side_effect = error_class(request=None)
    client.delete_messages.side_effect = error_class(request=None)
    result = await invoke(tool)
    assert_safe_audit_warning(result, broken_audit, caplog)
    assert result.startswith(("Cannot delete", "An error occurred"))
    record = broken_audit.call_args.args[0]
    assert record["outcome"] == "failure"
    assert record["reason"] == error_class.__name__
    if tool == "delete_message":
        client.delete_messages.assert_awaited_once()
        client.assert_not_awaited()
    else:
        client.assert_awaited_once()
        client.delete_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["delete_message", "delete_messages_bulk", "delete_chat_history"])
async def test_real_filesystem_audit_failure_is_reported(deletion, monkeypatch, tmp_path, tool):
    client, _ = deletion
    # Opening a directory as a log fails even when tests run as root.
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(tmp_path))
    result = await invoke(tool)
    assert result.endswith(" Warning: the audit record could not be saved.")
    assert "deleted" in result.lower()
    if tool == "delete_message":
        client.delete_messages.assert_awaited_once()
    else:
        client.assert_awaited_once()


@pytest.mark.asyncio
async def test_file_session_uses_session_directory(deletion, monkeypatch, tmp_path):
    client, _ = deletion
    monkeypatch.delenv("TELEGRAM_AUDIT_LOG")
    client.session = SimpleNamespace(filename=str(tmp_path / "sessions/personal.session"))
    await invoke("delete_message", account="personal")
    record = json.loads((tmp_path / "sessions/audit_log.jsonl").read_text())
    assert record["account"] == "personal"
