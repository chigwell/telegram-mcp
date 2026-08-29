"""Shared exception types.

Kept in a module of its own so helpers can raise them without importing
``runtime``, which builds the MCP server and discovers Telegram accounts as an
import side effect.
"""


class ValidationError(Exception):
    """Custom exception for validation errors."""

    pass
