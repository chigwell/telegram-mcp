"""Canonical legacy exceptions and value types, re-exported by runtime."""

from enum import Enum


class ValidationError(Exception):
    """Custom exception for validation errors."""

    pass


class ErrorCategory(str, Enum):
    CHAT = "CHAT"
    MSG = "MSG"
    CONTACT = "CONTACT"
    GROUP = "GROUP"
    MEDIA = "MEDIA"
    PROFILE = "PROFILE"
    AUTH = "AUTH"
    ADMIN = "ADMIN"
    FOLDER = "FOLDER"
    PRIVACY = "PRIVACY"


class ChatAccessDeniedError(Exception):
    """Exception raised when access to a chat is restricted by privacy policy."""

    pass


class AliasStoreUnreadable(Exception):
    """The alias file exists but could not be read, so writing would destroy it."""


class AliasID(int):
    """An int that remembers the wording it was resolved from.

    @validate_id substitutes the stored id before a tool body runs, so without this
    a resolver could only report an opaque number and never tell the user which of
    their nicknames has gone stale.
    """

    def __new__(cls, value: int, wording: str):
        obj = super().__new__(cls, value)
        obj.wording = wording
        return obj


class AliasNeedsUser(Exception):
    """Carries an agent-facing instruction to ask the human which contact is meant.

    Deliberately NOT a ValueError: several tools wrap resolution in
    `except ValueError` and would mangle the instruction into their own message.
    """

    def __init__(self, payload: str):
        super().__init__(payload)
        self.payload = payload


# Preserve legacy import/pickling paths while sharing one canonical object.
ValidationError.__module__ = "telegram_mcp.runtime"
ErrorCategory.__module__ = "telegram_mcp.runtime"
ChatAccessDeniedError.__module__ = "telegram_mcp.runtime"
AliasStoreUnreadable.__module__ = "telegram_mcp.runtime"
AliasID.__module__ = "telegram_mcp.runtime"
AliasNeedsUser.__module__ = "telegram_mcp.runtime"
AliasID.__new__.__module__ = "telegram_mcp.runtime"
AliasNeedsUser.__init__.__module__ = "telegram_mcp.runtime"
