"""Private error_formatting implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from telegram_mcp.core_types import ErrorCategory
from typing import Optional
from typing import Union


def _is_flood_wait(error: Exception, *, FloodWaitError) -> bool:
    """True for Telethon FloodWaitError."""
    try:
        return isinstance(error, FloodWaitError)
    except Exception:  # telethon missing or moved — not this helper's problem
        return False


def _is_schema_drift(error: Exception) -> bool:
    """True for TypeNotFoundError — the installed TL schema is older than what the server sends."""
    try:
        from telethon.errors.common import TypeNotFoundError
    except Exception:  # telethon missing or moved — not this helper's problem
        return False
    return isinstance(error, TypeNotFoundError)


def log_and_format_error(
    function_name: str,
    error: Exception,
    prefix: Optional[Union[ErrorCategory, str]],
    user_message: str,
    *,
    AliasNeedsUser,
    ErrorCategory,
    _is_flood_wait,
    _is_schema_drift,
    logger,
    **kwargs,
) -> str:
    """
    Centralized error handling function.

    Logs an error and returns a formatted, user-friendly message.

    Args:
        function_name: Name of the function where the error occurred.
        error: The exception that was raised.
        prefix: Error code prefix (e.g., ErrorCategory.CHAT, "VALIDATION-001").
            If None, it will be derived from the function_name.
        user_message: A custom user-facing message to return. If None, a generic one is created.
        **kwargs: Additional context parameters. These are never written to persistent logs.

    Returns:
        A user-friendly error message with an error code.
    """
    # An ask-the-user instruction is normal control flow, not a failure: return it
    # verbatim and never log the user's nickname at ERROR level.
    if isinstance(error, AliasNeedsUser):
        return error.payload

    # Generate a consistent error code
    if isinstance(prefix, str) and prefix == "VALIDATION-001":
        # Special case for validation errors
        error_code = prefix
    else:
        if prefix is None:
            # Try to derive prefix from function name
            for category in ErrorCategory:
                if category.name.lower() in function_name.lower():
                    prefix = category
                    break

        prefix_str = prefix.value if isinstance(prefix, ErrorCategory) else (prefix or "GEN")
        error_code = f"{prefix_str}-ERR-{abs(hash(function_name)) % 1000:03d}"

    # Telegram FloodWait (Rate Limiting) must be explicitly formatted for LLM agents.
    # LLMs will blindly retry generic errors, escalating the flood penalty and risking bans.
    # Log only a categorical warning; the user-facing response below carries the
    # actionable wait duration. The persistent error-file handler intentionally
    # does not store WARNING records.
    if _is_flood_wait(error):
        seconds = getattr(error, "seconds", None) or 0
        logger.warning("Telegram FloodWait; retry only after the reported delay.")
        if user_message:
            return user_message
        wait_clause = f"{seconds} seconds" if seconds > 0 else "an unknown duration"
        return (
            f"Rate limit exceeded (FloodWait): Telegram requires waiting {wait_clause} "
            f"before repeating this operation. Do NOT retry immediately (code: {error_code})."
        )

    # Keep persistent logs useful without recording exception text, tracebacks,
    # identifiers, user content, provider payloads, or local paths.
    logger.error("Telegram MCP operation failed; see the returned stable error code.")

    # Return a user-friendly message
    if user_message:
        return user_message

    # MTProto schema drift must not hide behind the generic code. Telethon releases lag
    # behind production Telegram, and when the server sends an object whose constructor
    # the installed schema does not know, the read buffer desynchronises: some tools fail
    # while their neighbours keep working. Reported as a generic error, that pattern is
    # indistinguishable from "no such user/chat" and sends debugging the wrong way.
    if _is_schema_drift(error):
        return (
            f"MTProto schema mismatch: the installed Telethon does not know an object the "
            f"server sent. This is NOT a missing user or chat — the data arrived, "
            f"parsing it failed. Upgrade Telethon; if it is already the latest release, its "
            f"schema is behind the current layer (code: {error_code})."
        )

    return f"An error occurred (code: {error_code})."
