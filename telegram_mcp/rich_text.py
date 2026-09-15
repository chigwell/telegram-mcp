"""Telegram rich-text parsing, rendering, and Premium capability helpers."""

import json
from typing import List, get_args

from telethon import types

# Parse modes that request server-side rich formatting (tables, headings,
# formulas, collapsible sections — the June 2026 "Rich Messages" feature).
# Sending rich messages requires Telegram Premium on the account.
RICH_PARSE_MODES = {"rich", "rich_md", "rich_markdown", "rich_html"}


async def account_is_premium(client) -> bool:
    """Fresh Premium check at call time — Premium can expire or be bought anytime."""
    me = await client.get_me()
    return bool(getattr(me, "premium", False))


def make_rich_input(parse_mode: str, text: str):
    """Build the InputRichMessage payload for a rich parse mode."""
    if parse_mode == "rich_html":
        return types.InputRichMessageHTML(html=text)
    return types.InputRichMessageMarkdown(markdown=text)


# Reading a rich message is the other direction, and it needs its own walk: a
# channel posting in this format leaves msg.message empty and carries every word
# as Instant-View page blocks, so a reader that only looks at msg.message
# reports the whole post as empty.
_RICH_TEXT_TYPES = tuple(get_args(types.TypeRichText))

# RichText is a recursive tree: a node either holds a plain string, wraps
# another node, or concatenates a list of them. Dispatching on the field rather
# than on the class keeps a node type Telegram adds later flattening instead of
# vanishing.
_RICH_TEXT_FIELDS = ("texts", "text", "alt", "source")

# Where a page block, list item, table row or caption keeps its words. Same walk
# covers the blocks nested inside details, collages and embedded posts.
_PAGE_TEXT_FIELDS = (
    "title",
    "subtitle",
    "author",
    "text",
    "caption",
    "credit",
    "items",
    "blocks",
    "rows",
    "articles",
)


def rich_text_to_str(node) -> str:
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


def _page_lines(node) -> List[str]:
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


def rich_message_text(msg) -> str:
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


def premium_required_result(action: str) -> str:
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
