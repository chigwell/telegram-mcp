"""Private formatting implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union
from datetime import datetime


def get_entity_type(entity: Any, *, Channel, Chat, User) -> str:
    """Return a normalized, human-readable chat/entity type."""
    if isinstance(entity, User):
        return "User"
    if isinstance(entity, Chat):
        return "Group (Basic)"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False):
            return "Supergroup"
        return "Channel" if getattr(entity, "broadcast", False) else "Group"
    return type(entity).__name__


def get_marked_id(entity: Any, *, Channel, Chat) -> int:
    """Return a Telethon-compatible marked ID for an entity."""
    if isinstance(entity, Channel):
        return -1000000000000 - entity.id
    if isinstance(entity, Chat):
        return -entity.id
    return entity.id


def get_entity_filter_type(entity: Any, *, get_entity_type) -> Optional[str]:
    """Return list_chats-compatible filter type: user/group/channel."""
    entity_type = get_entity_type(entity)
    if entity_type == "User":
        return "user"
    if entity_type in ("Group (Basic)", "Group", "Supergroup"):
        return "group"
    if entity_type == "Channel":
        return "channel"
    return None


def parse_schedule_date(
    schedule_date: Union[str, int], *, datetime, timezone
) -> tuple[Optional[datetime], Optional[str]]:
    """Return (datetime, None) for a usable schedule_date, or (None, error message).

    Accepts an ISO-8601 string or a Unix timestamp; naive datetimes are UTC.
    """
    try:
        if isinstance(schedule_date, int):
            dt = datetime.fromtimestamp(schedule_date, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(schedule_date).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None, (
            "schedule_date could not be parsed. Use an ISO-8601 date/time or Unix timestamp."
        )

    now = datetime.now(timezone.utc)
    if dt <= now:
        return None, (
            f"schedule_date must be in the future (got {dt.isoformat()}, now {now.isoformat()})."
        )
    return dt, None


def format_entity(entity, *, Chat, get_marked_id, sanitize_name) -> Dict[str, Any]:
    """Helper function to format entity information consistently.

    Names and titles are sanitized to prevent prompt injection.
    """
    result = {"id": get_marked_id(entity)}

    if hasattr(entity, "title"):
        result["name"] = sanitize_name(entity.title)
        result["type"] = "group" if isinstance(entity, Chat) else "channel"
    elif hasattr(entity, "first_name"):
        name_parts = []
        if entity.first_name:
            name_parts.append(entity.first_name)
        if hasattr(entity, "last_name") and entity.last_name:
            name_parts.append(entity.last_name)
        result["name"] = sanitize_name(" ".join(name_parts))
        result["type"] = "user"
        if hasattr(entity, "username") and entity.username:
            result["username"] = entity.username
        if hasattr(entity, "phone") and entity.phone:
            result["phone"] = entity.phone

    return result


async def account_is_premium(client) -> bool:
    """Fresh Premium check at call time — Premium can expire or be bought anytime."""
    me = await client.get_me()
    return bool(getattr(me, "premium", False))


def make_rich_input(parse_mode: str, text: str, *, types):
    """Build the InputRichMessage payload for a rich parse mode."""
    if parse_mode == "rich_html":
        return types.InputRichMessageHTML(html=text)
    return types.InputRichMessageMarkdown(markdown=text)


def rich_text_to_str(node, *, _RICH_TEXT_FIELDS, rich_text_to_str) -> str:
    """Flatten one RichText node into plain text.

    TextCustomEmoji contributes its alt character - dropping it would silently
    eat the emoji a channel used as a bullet or a heading marker.
    """
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, (list, tuple)):
        return "".join(rich_text_to_str(item) for item in node)
    for field in _RICH_TEXT_FIELDS:
        value = getattr(node, field, None)
        if value is not None:
            return rich_text_to_str(value)
    return ""  # TextEmpty, TextImage and anything else carrying no text


def _page_lines(
    node, *, _PAGE_TEXT_FIELDS, _RICH_TEXT_TYPES, _page_lines, rich_text_to_str
) -> List[str]:
    """Text lines carried by a page block, list item, table row or caption."""
    if node is None:
        return []
    if isinstance(node, (list, tuple)):
        return [line for item in node for line in _page_lines(item)]
    if isinstance(node, _RICH_TEXT_TYPES):
        text = rich_text_to_str(node).strip()
        return [text] if text else []
    cells = getattr(node, "cells", None)
    if cells is not None:  # a table row reads as one line, not one line per cell
        row = " | ".join(line for cell in cells for line in _page_lines(cell))
        return [row] if row else []
    return [line for f in _PAGE_TEXT_FIELDS for line in _page_lines(getattr(node, f, None))]


def rich_message_text(msg, *, _page_lines) -> str:
    """Plain text of a rich (block-format) message, "" when there is none.

    Each block becomes a paragraph and the lines within one block stay together,
    so a list reads as a list instead of one run-on line. An unknown block type
    yields nothing rather than breaking the whole message.
    """
    blocks = getattr(getattr(msg, "rich_message", None), "blocks", None)
    if not blocks:
        return ""
    paragraphs = ("\n".join(_page_lines(block)) for block in blocks)
    return "\n\n".join(p for p in paragraphs if p)


def premium_required_result(action: str, *, json) -> str:
    """Structured refusal so the agent can degrade gracefully instead of sending garbage."""
    return json.dumps(
        {
            "sent": False,
            "reason": "telegram_premium_required",
            "detail": (
                f"{action} with rich formatting requires Telegram Premium on this account. "
                "Nothing was sent. Reformat without rich-only blocks (tables, headings, "
                "formulas) and retry with parse_mode='md' or 'html'."
            ),
        },
        ensure_ascii=False,
    )


def is_premium_rpc_error(error: Exception) -> bool:
    """True when Telegram rejected a call because the account lacks Premium."""
    return "PREMIUM" in getattr(error, "message", str(error)).upper()


def format_message(message, *, sanitize_user_content, utils) -> Dict[str, Any]:
    """Helper function to format message information consistently.

    Message text is sanitized to prevent prompt injection.
    """
    result = {
        "id": message.id,
        "date": message.date.isoformat(),
        "text": sanitize_user_content(message.message),
    }

    if message.from_id:
        result["from_id"] = utils.get_peer_id(message.from_id)

    if message.media:
        result["has_media"] = True
        result["media_type"] = type(message.media).__name__

    return result


def get_sender_name(message, *, sanitize_name) -> str:
    """Helper function to get sender name from a message.

    Returns a sanitized single-line display name to prevent prompt injection
    via crafted Telegram display names.
    """
    if not message.sender:
        return "Unknown"

    # Check for group/channel title first
    if hasattr(message.sender, "title") and message.sender.title:
        return sanitize_name(message.sender.title)
    elif hasattr(message.sender, "first_name"):
        # User sender
        first_name = getattr(message.sender, "first_name", "") or ""
        last_name = getattr(message.sender, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip()
        return sanitize_name(full_name) if full_name else "Unknown"
    else:
        return "Unknown"


def get_sender_username(message, *, sanitize_name) -> Optional[str]:
    """Public @username of the message sender, if any (sanitized)."""
    sender = getattr(message, "sender", None)
    username = getattr(sender, "username", None) if sender else None
    return sanitize_name(username) if username else None


def get_sender_info(message, *, get_sender_name, get_sender_username) -> str:
    """Sender display string: name (@username) [id=NNN].

    Always exposes a numeric id (sender or from_id) so a user can be reached via
    tg://user?id=<id> even when no public @username exists.
    """
    name = get_sender_name(message)
    username = get_sender_username(message)
    sid = getattr(message, "sender_id", None)
    suffix = ""
    if username:
        suffix += f" (@{username})"
    if sid:
        suffix += f" [id={sid}]"
    return f"{name}{suffix}"


def get_engagement_info(message) -> str:
    """Helper function to get engagement metrics (views, forwards, reactions) from a message."""
    engagement_parts = []
    views = getattr(message, "views", None)
    if views is not None:
        engagement_parts.append(f"views:{views}")
    forwards = getattr(message, "forwards", None)
    if forwards is not None:
        engagement_parts.append(f"forwards:{forwards}")
    reactions = getattr(message, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        total_reactions = sum(getattr(r, "count", 0) or 0 for r in results) if results else 0
        engagement_parts.append(f"reactions:{total_reactions}")
    return f" | {', '.join(engagement_parts)}" if engagement_parts else ""


def get_engagement_dict(message) -> Optional[Dict[str, Any]]:
    """Return engagement metrics as a dict for JSON-formatted tool results."""
    result = {}
    views = getattr(message, "views", None)
    if views is not None:
        result["views"] = views
    forwards = getattr(message, "forwards", None)
    if forwards is not None:
        result["forwards"] = forwards
    reactions = getattr(message, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        result["reactions"] = sum(getattr(r, "count", 0) or 0 for r in results) if results else 0
    return result if result else None
