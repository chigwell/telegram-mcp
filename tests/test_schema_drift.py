"""Schema drift must be reported as such, not as a generic error code.

A `TypeNotFoundError` means the installed TL schema is older than what the server
sends. Hidden behind the generic message, it reads like "no such user/chat" and
costs hours of debugging in the wrong direction.
"""

from telegram_mcp.runtime import log_and_format_error
from telethon.errors.common import TypeNotFoundError


def test_schema_drift_is_named_actionable_and_omits_exception_payload():
    error = TypeNotFoundError(0xD58A08C6, b"raw-payload-secret")

    msg = log_and_format_error("list_chats", error)

    assert "MTProto schema mismatch" in msg
    assert "NOT a missing user or chat" in msg
    assert str(error) not in msg
    assert "raw-payload-secret" not in msg


def test_ordinary_error_keeps_the_generic_format():
    msg = log_and_format_error("get_chat", ValueError("boom"))
    assert "code:" in msg
    assert "MTProto schema mismatch" not in msg
