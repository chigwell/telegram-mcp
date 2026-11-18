"""Telegram RPC errors are named in the returned message; other errors stay generic."""

import re

import pytest
from telethon.errors import (
    ChatAdminRequiredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    PeerFloodError,
    RPCError,
    UserBannedInChannelError,
    UserPrivacyRestrictedError,
)
from telethon.tl.functions.messages import SendMessageRequest

from telegram_mcp import runtime
from telegram_mcp.runtime import ErrorCategory, log_and_format_error

SECRET_TEXT = "request-body-secret"


def _request():
    return SendMessageRequest(peer="someone", message=SECRET_TEXT)


@pytest.mark.parametrize(
    "error_class, telegram_name",
    [
        (PeerFloodError, "PEER_FLOOD"),
        (ChatWriteForbiddenError, "CHAT_WRITE_FORBIDDEN"),
        (UserPrivacyRestrictedError, "USER_PRIVACY_RESTRICTED"),
        (ChatAdminRequiredError, "CHAT_ADMIN_REQUIRED"),
        (UserBannedInChannelError, "USER_BANNED_IN_CHANNEL"),
    ],
)
def test_rpc_error_is_named(error_class, telegram_name):
    result = log_and_format_error(
        "send_message", error_class(request=_request()), ErrorCategory.MSG
    )

    assert re.fullmatch(
        rf"An error occurred \(code: MSG-ERR-\d{{3}}\): Telegram returned "
        rf"{telegram_name} \({error_class.__name__}\)\.",
        result,
    )


def test_unknown_rpc_error_uses_telegram_message():
    error = RPCError(_request(), "SOME_NEW_ERROR", 400)

    result = log_and_format_error("send_message", error, ErrorCategory.MSG)

    assert result.endswith(": Telegram returned SOME_NEW_ERROR (RPCError).")


def test_unnamed_rpc_error_falls_back_to_class_name():
    error = RPCError(_request(), "not a constant; with details", 400)

    result = log_and_format_error("send_message", error, ErrorCategory.MSG)

    assert result.endswith(": Telegram returned RPCError.")
    assert "with details" not in result


def test_error_code_is_unchanged():
    plain = log_and_format_error("send_message", ValueError("boom"), ErrorCategory.MSG)
    named = log_and_format_error(
        "send_message", PeerFloodError(request=_request()), ErrorCategory.MSG
    )

    code = re.match(r"An error occurred \(code: (MSG-ERR-\d{3})\)", plain).group(1)
    assert named.startswith(f"An error occurred (code: {code}): ")


def test_plain_exception_keeps_generic_message():
    result = log_and_format_error("send_message", ValueError("boom"), ErrorCategory.MSG)

    assert re.fullmatch(r"An error occurred \(code: MSG-ERR-\d{3}\)\.", result)


def test_returned_text_never_includes_request_repr():
    result = log_and_format_error(
        "send_message", PeerFloodError(request=_request()), ErrorCategory.MSG, chat_id=123456
    )

    assert SECRET_TEXT not in result
    assert "SendMessageRequest" not in result
    assert "caused by" not in result
    assert "123456" not in result


def test_user_message_still_overrides_rpc_error():
    result = log_and_format_error(
        "send_message", PeerFloodError(request=_request()), user_message="Custom message"
    )

    assert result == "Custom message"


def test_flood_wait_is_unchanged():
    result = log_and_format_error(
        "send_message", FloodWaitError(request=None, capture=45), ErrorCategory.MSG
    )

    assert "Rate limit exceeded (FloodWait)" in result
    assert "45 seconds" in result
    assert "Telegram returned" not in result


def test_persistent_log_carries_no_error_text(caplog):
    with caplog.at_level("ERROR", logger=runtime.logger.name):
        log_and_format_error("send_message", PeerFloodError(request=_request()), ErrorCategory.MSG)

    assert "PEER_FLOOD" not in caplog.text
    assert SECRET_TEXT not in caplog.text
