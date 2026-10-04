"""Private access_policy implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from typing import Any
from typing import Iterable
from typing import Optional
from typing import Union


def _parse_allowed_chat_ids(
    raw: Optional[Union[str, Iterable[Union[int, str]]]], *, Union
) -> Optional[set[Union[int, str]]]:
    """Parse TELEGRAM_ALLOWED_CHAT_IDS into a set of allowed IDs and usernames.

    Supports comma-separated integer IDs (e.g. '12345678,-100123456789') and
    usernames/handles (e.g. '@mychat,other_channel').
    Also automatically indexes marked variants for bare integers and vice versa
    so that both marked IDs (-100...) and bare positive IDs match.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return None
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
    elif isinstance(raw, (list, tuple, set)):
        tokens = [str(t).strip() for t in raw if str(t).strip()]
    else:
        return None

    if not tokens:
        return None

    allowed: set[Union[int, str]] = set()
    for token in tokens:
        try:
            val = int(token)
            allowed.add(val)
            # If negative supergroup: -100XXXXXXXXXX
            if str(val).startswith("-100") and len(str(val)) > 4:
                try:
                    channel_id = int(str(val)[4:])
                    allowed.add(channel_id)
                except ValueError:
                    pass
            # If positive bare ID: add supergroup (-100...) and group (-) variants
            elif val > 0:
                allowed.add(-1000000000000 - val)
                allowed.add(-val)
            elif val < 0:
                # Basic group negative ID: -XXXXXX
                allowed.add(-val)
        except ValueError:
            clean_handle = token.lstrip("@").strip().lower()
            if clean_handle:
                allowed.add(clean_handle)

    return allowed if allowed else None


def _load_allowed_chat_ids(*, _parse_allowed_chat_ids, os) -> Optional[set[Union[int, str]]]:
    """Load ALLOWED_CHAT_IDS from the TELEGRAM_ALLOWED_CHAT_IDS environment variable."""
    return _parse_allowed_chat_ids(os.getenv("TELEGRAM_ALLOWED_CHAT_IDS"))


def get_effective_allowed_chat_ids(
    *, ALLOWED_CHAT_IDS, _parse_allowed_chat_ids, os
) -> Optional[set[Union[int, str]]]:
    """Return the currently effective set of allowed chat IDs, or None if allowlist is disabled."""
    env_raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS")
    if env_raw is not None:
        return _parse_allowed_chat_ids(env_raw)
    return ALLOWED_CHAT_IDS


def is_chat_allowlist_enabled(*, get_effective_allowed_chat_ids) -> bool:
    """Return True if chat allowlist filtering is active."""
    return get_effective_allowed_chat_ids() is not None


def is_chat_allowed(
    chat_identifier: Any, entity: Any, *, get_effective_allowed_chat_ids, get_marked_id
) -> bool:
    """Check whether a chat identifier or entity is permitted by the allowlist.

    If allowlist is not enabled, always returns True.
    """
    allowed = get_effective_allowed_chat_ids()
    if allowed is None:
        return True

    if chat_identifier is not None:
        if isinstance(chat_identifier, int):
            if chat_identifier in allowed:
                return True
        elif isinstance(chat_identifier, str):
            try:
                int_id = int(chat_identifier)
                if int_id in allowed:
                    return True
            except ValueError:
                clean = chat_identifier.lstrip("@").strip().lower()
                if clean and clean in allowed:
                    return True

    if entity is not None:
        try:
            marked_id = get_marked_id(entity)
            if marked_id in allowed:
                return True
        except Exception:
            pass

        bare_id = getattr(entity, "id", None)
        if isinstance(bare_id, int) and bare_id in allowed:
            return True

        username = getattr(entity, "username", None)
        if username and str(username).lower() in allowed:
            return True

    return False


def check_chat_access(chat_identifier: Any, entity: Any, *, is_chat_allowed) -> Optional[str]:
    """Return an error message if chat access is restricted, or None if allowed."""
    if not is_chat_allowed(chat_identifier, entity):
        return (
            f"Access to chat '{chat_identifier}' is restricted by privacy policy "
            "(TELEGRAM_ALLOWED_CHAT_IDS)."
        )
    return None
