"""FloodWait (rate limit) errors must be surfaced with actionable instructions for LLMs.

When Telegram rate-limits a client with FloodWaitError, returning a generic error
prompts LLM agents to retry immediately, worsening the rate limit and risking account bans.
The error formatter must explicitly communicate the required wait duration and directive
not to retry immediately, while allowing flood_sleep_threshold to be configured via environment.
"""

from unittest.mock import patch
from telethon.errors import FloodWaitError
from telegram_mcp.runtime import (
    _get_flood_sleep_threshold,
    _is_flood_wait,
    _build_client,
    log_and_format_error,
    ErrorCategory,
)


def test_flood_wait_is_named_and_actionable():
    err = FloodWaitError(request=None, capture=45)
    msg = log_and_format_error("send_message", err, prefix=ErrorCategory.MSG, chat_id=123456)

    assert "Rate limit exceeded (FloodWait)" in msg
    assert "45 seconds" in msg
    assert "Do NOT retry immediately" in msg
    assert "code: MSG-ERR-" in msg


def test_flood_wait_zero_or_missing_seconds_formats_unknown_duration():
    err = FloodWaitError(request=None, capture=0)
    msg = log_and_format_error("send_message", err, prefix=ErrorCategory.MSG)

    assert "Rate limit exceeded (FloodWait)" in msg
    assert "an unknown duration" in msg
    assert "Do NOT retry immediately" in msg
    assert "0 seconds" not in msg


def test_flood_wait_is_detected_by_helper():
    err = FloodWaitError(request=None, capture=120)
    assert _is_flood_wait(err) is True
    assert _is_flood_wait(ValueError("other error")) is False


def test_flood_wait_logs_warning_instead_of_error():
    err = FloodWaitError(request=None, capture=30)
    with patch("telegram_mcp.runtime.logger") as mock_logger:
        log_and_format_error("get_history", err, chat_id=98765)
        mock_logger.warning.assert_called_once()
        warning_args = mock_logger.warning.call_args[0][0]
        assert "Telegram FloodWait in get_history" in warning_args
        assert "30s" in warning_args
        mock_logger.error.assert_not_called()


def test_flood_wait_with_custom_user_message():
    err = FloodWaitError(request=None, capture=15)
    msg = log_and_format_error("send_message", err, user_message="Custom rate limit message")
    assert msg == "Custom rate limit message"


def test_flood_sleep_threshold_default(monkeypatch):
    monkeypatch.delenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", raising=False)
    assert _get_flood_sleep_threshold() == 60


def test_flood_sleep_threshold_custom_valid(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "15")
    assert _get_flood_sleep_threshold() == 15

    monkeypatch.setenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "0")
    assert _get_flood_sleep_threshold() == 0


def test_flood_sleep_threshold_negative_clamped_with_warning(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "-5")
    with patch("telegram_mcp.runtime.logger") as mock_logger:
        assert _get_flood_sleep_threshold() == 0
        mock_logger.warning.assert_called_once()
        assert "Negative TELEGRAM_FLOOD_SLEEP_THRESHOLD" in mock_logger.warning.call_args[0][0]


def test_flood_sleep_threshold_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "not_a_number")
    with patch("telegram_mcp.runtime.logger") as mock_logger:
        assert _get_flood_sleep_threshold() == 60
        mock_logger.warning.assert_called_once()
        assert "Invalid TELEGRAM_FLOOD_SLEEP_THRESHOLD" in mock_logger.warning.call_args[0][0]


def test_build_client_passes_flood_sleep_threshold(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "25")
    with patch("telegram_mcp.runtime.TelegramClient") as mock_tc:
        _build_client("dummy_session", "default")
        mock_tc.assert_called_once()
        _, kwargs = mock_tc.call_args
        assert kwargs.get("flood_sleep_threshold") == 25


def test_ordinary_error_retains_generic_error_format():
    msg = log_and_format_error(
        "send_message", ValueError("network error"), prefix=ErrorCategory.MSG
    )
    assert "An error occurred (code: MSG-ERR-" in msg
    assert "Rate limit exceeded" not in msg
