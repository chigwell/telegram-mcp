"""Telegram media ↔ McplContentBlock conversion.

Conversion rules:
  - Text                  → McplTextContent
  - Photo / voice / doc   → McplTextContent placeholder (Phase 4 minimal)
                            plus the message URI in metadata, so the agent
                            can fetch with existing tools if needed

Media binary path (full inline base64 ≤1MB / telegram:// URI otherwise) is
deferred. For Phase 4 we focus on the event flow; media plumbing rides on
top of these primitives later.
"""

from __future__ import annotations

from typing import Any

from .types import McplContentBlock, McplResourceContent, McplTextContent

INLINE_MEDIA_LIMIT_BYTES = 1 * 1024 * 1024
"""Cutoff for inline base64 vs URI reference."""


def message_uri(account_label: str, chat_id: int | str, message_id: int) -> str:
    """Stable URI for a single Telegram message.

    Used as `telegram://message/{account}/{chat}/{message}` so the agent
    can pass it to the existing fetch tools (or, eventually, an MCP
    resources/read endpoint) when it needs the full content.
    """
    return f"telegram://message/{account_label}/{chat_id}/{message_id}"


def media_kind(message: Any) -> str | None:
    """Return a short human label for the message's media type, if any."""
    media = getattr(message, "media", None)
    if media is None:
        return None
    cls = type(media).__name__
    # Telethon types: MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
    # MessageMediaContact, MessageMediaPoll, MessageMediaGeo, etc.
    mapping = {
        "MessageMediaPhoto": "photo",
        "MessageMediaDocument": "document",
        "MessageMediaWebPage": "link",
        "MessageMediaContact": "contact",
        "MessageMediaPoll": "poll",
        "MessageMediaGeo": "location",
        "MessageMediaVenue": "location",
        "MessageMediaDice": "dice",
        "MessageMediaGame": "game",
        "MessageMediaInvoice": "invoice",
        "MessageMediaUnsupported": "unsupported",
    }
    short = mapping.get(cls, cls.removeprefix("MessageMedia").lower() or "media")
    if short == "document":
        # Voice notes and round videos are documents with attribute flags
        document = getattr(media, "document", None)
        if document is not None:
            attributes = getattr(document, "attributes", []) or []
            for attr in attributes:
                attr_cls = type(attr).__name__
                if attr_cls == "DocumentAttributeAudio" and getattr(attr, "voice", False):
                    return "voice"
                if attr_cls == "DocumentAttributeVideo":
                    return "video"
                if attr_cls == "DocumentAttributeAnimated":
                    return "gif"
                if attr_cls == "DocumentAttributeSticker":
                    return "sticker"
    return short


def message_to_content_blocks(
    message: Any,
    *,
    account_label: str,
    chat_id: int | str,
) -> list[McplContentBlock]:
    """Convert a Telethon Message into MCPL content blocks.

    Phase 4 minimal: a single text block. If media is present and there's
    no text, surface a `[<kind>]` placeholder. Media binary delivery comes
    in a later phase; for now we additionally include a McplResourceContent
    block pointing at the message URI so the host can fetch on demand.
    """
    blocks: list[McplContentBlock] = []
    text = getattr(message, "message", None) or ""
    kind = media_kind(message)

    if text:
        blocks.append(McplTextContent(type="text", text=text))
    if kind:
        if not text:
            blocks.append(
                McplTextContent(type="text", text=f"[{kind}]")
            )
        # Always attach the resource pointer when there's media — agent can
        # follow it for the binary or rich content later.
        blocks.append(
            McplResourceContent(
                type="resource",
                uri=message_uri(account_label, chat_id, message.id),
            )
        )

    if not blocks:
        # Fallback for service messages with neither text nor media (joins,
        # pin updates, etc.). The agent at least sees something.
        blocks.append(McplTextContent(type="text", text="[message]"))

    return blocks
