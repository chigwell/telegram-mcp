"""Tests for local audit logging of destructive message operations (Closes #321)."""

import json
from unittest.mock import AsyncMock

import pytest
import telethon.errors.rpcerrorlist
from telethon.tl import types

from telegram_mcp import audit
from telegram_mcp.tools import messages


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    """Provide an isolated audit log path for tests."""
    log_file = tmp_path / "audit_test.jsonl"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(log_file))
    return log_file


@pytest.fixture
def mock_telethon(monkeypatch):
    """Mock Telethon client and entity resolution."""
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        messages, "resolve_entity", AsyncMock(return_value=types.InputPeerUser(42, 0))
    )
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    return cl


def test_record_audit_event_appends_jsonl(audit_log):
    """Directly recording an audit event appends valid JSONL."""
    audit.record_audit_event(
        tool="test_tool",
        chat_id=12345,
        outcome="tested",
        account="work_account",
        message_ids=[10, 20],
        revoke=True,
        extra={"reason": "unit_test"},
    )

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])

    assert data["tool"] == "test_tool"
    assert data["chat_id"] == 12345
    assert data["account"] == "work_account"
    assert data["message_ids"] == [10, 20]
    assert data["revoke"] is True
    assert data["outcome"] == "tested"
    assert data["extra"] == {"reason": "unit_test"}
    assert "timestamp" in data
    # Guarantee no message text leaks into audit
    assert "text" not in data
    assert "message" not in data


def test_record_audit_event_catches_io_errors(monkeypatch):
    """Audit logging never crashes even if file writing fails."""
    monkeypatch.setattr(audit, "get_audit_log_path", lambda: "/non/existent/path/never_write.log")
    # Should complete cleanly without raising
    audit.record_audit_event(tool="tool", chat_id=1, outcome="fail_safe")


@pytest.mark.asyncio
async def test_delete_message_audit_logged_on_success(audit_log, mock_telethon, monkeypatch):
    """Successful delete_message writes a valid audit record."""
    monkeypatch.setattr(messages, "_ids_outside_chat", AsyncMock(return_value=[]))

    res = await messages.delete_message(chat_id=1001, message_id=55, account="user1")
    assert "Message 55 deleted." in res

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "delete_message"
    assert entry["chat_id"] == 1001
    assert entry["message_ids"] == [55]
    assert entry["account"] == "user1"
    assert entry["outcome"] == "deleted"


@pytest.mark.asyncio
async def test_delete_message_audit_logged_on_foreign_refusal(
    audit_log, mock_telethon, monkeypatch
):
    """When message belongs to another chat, refusal is recorded in the audit log."""
    monkeypatch.setattr(messages, "_ids_outside_chat", AsyncMock(return_value=[999]))

    res = await messages.delete_message(chat_id=1001, message_id=999, account="user1")
    assert "nothing was deleted" in res

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "delete_message"
    assert entry["outcome"] == "refused: foreign message ID"
    assert entry["message_ids"] == [999]


@pytest.mark.asyncio
async def test_delete_messages_bulk_audit(audit_log, mock_telethon, monkeypatch):
    """Bulk deletion writes audit records for both success and foreign ID refusals."""
    monkeypatch.setattr(messages, "_ids_outside_chat", AsyncMock(return_value=[]))
    mock_res = AsyncMock()
    mock_res.pts_count = 3
    mock_telethon.return_value = mock_res

    res = await messages.delete_messages_bulk(
        chat_id=2002, message_ids=[1, 2, 3], revoke=True, account="admin"
    )
    assert "Deleted 3 of 3 messages" in res

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "delete_messages_bulk"
    assert entry["chat_id"] == 2002
    assert entry["message_ids"] == [1, 2, 3]
    assert entry["revoke"] is True
    assert entry["outcome"] == "deleted 3 of 3 messages"


@pytest.mark.asyncio
async def test_delete_messages_bulk_refused_audit(audit_log, mock_telethon, monkeypatch):
    """Bulk deletion foreign ID refusal is recorded in audit log."""
    monkeypatch.setattr(messages, "_ids_outside_chat", AsyncMock(return_value=[50]))

    res = await messages.delete_messages_bulk(chat_id=2002, message_ids=[49, 50])
    assert "nothing was deleted" in res

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "delete_messages_bulk"
    assert "refused: foreign message IDs" in entry["outcome"]


@pytest.mark.asyncio
async def test_delete_chat_history_audit(audit_log, mock_telethon):
    """delete_chat_history writes audit records for success and admin refusal."""
    mock_res = AsyncMock()
    mock_res.pts_count = 100
    mock_res.offset = 0
    mock_telethon.return_value = mock_res

    res = await messages.delete_chat_history(chat_id=3003, max_id=0, revoke=True, account="userA")
    assert "100 messages deleted" in res

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "delete_chat_history"
    assert entry["chat_id"] == 3003
    assert entry["revoke"] is True
    assert "cleared for both parties" in entry["outcome"]


@pytest.mark.asyncio
async def test_delete_chat_history_admin_refusal_audit(audit_log, mock_telethon):
    """Admin required refusal is recorded in audit log."""
    mock_telethon.side_effect = telethon.errors.rpcerrorlist.ChatAdminRequiredError(request=None)

    res = await messages.delete_chat_history(chat_id=3003, max_id=0)
    assert "admin privileges are required" in res

    lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "delete_chat_history"
    assert entry["outcome"] == "refused: ChatAdminRequiredError"
