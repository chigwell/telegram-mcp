"""Canonical account-label spelling shared by the server and the CLI tools.

A label appears in env var names (``TELEGRAM_SESSION_STRING_<LABEL>``), in
session filenames and as the ``account`` argument of MCP tools. Dashes fold to
underscores because ``export TELEGRAM_SESSION_STRING_MY-WORK=...`` is not valid
shell syntax, so every consumer must derive the same name from the same label —
otherwise the generator suggests one variable, the migrator reads another and
the server discovers a third.
"""


def canonical_account_label(label: str) -> str:
    """Fold an account label to its canonical form: lowercase, underscores."""
    return label.strip().lower().replace("-", "_")
