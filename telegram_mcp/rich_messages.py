"""Reading Telegram Rich Messages.

Rich messages (Telegram, June 2026) carry their content as Instant View page
blocks in ``Message.rich_message``, not in ``Message.message``. A reader that
only looks at ``.message`` therefore shows a rich message as **empty** - the
tables, headings, formulas and collapsible sections are simply lost. This
module renders those blocks back to Markdown so listings and exports show what
was actually sent.

Sending is the other half and lives in ``premium`` (``make_rich_input``); it
requires Premium. Reading does not.

Dispatch is by class name rather than ``isinstance`` so a block type added by a
newer Telegram layer degrades to a labelled placeholder instead of raising.
"""

from typing import Any, Optional

# Rich text nodes that wrap an inner node and map onto Markdown emphasis.
_TEXT_WRAPPERS = {
    "TextBold": ("**", "**"),
    "TextItalic": ("_", "_"),
    "TextUnderline": ("__", "__"),
    "TextStrike": ("~~", "~~"),
    "TextFixed": ("`", "`"),
    "TextSpoiler": ("||", "||"),
    "TextMarked": ("==", "=="),
    "TextSubscript": ("~", "~"),
    "TextSuperscript": ("^", "^"),
}

# Heading blocks, in Markdown heading depth.
_HEADINGS = {
    "PageBlockTitle": 1,
    "PageBlockHeader": 1,
    "PageBlockHeading1": 1,
    "PageBlockSubtitle": 2,
    "PageBlockSubheader": 2,
    "PageBlockHeading2": 2,
    "PageBlockHeading3": 3,
    "PageBlockHeading4": 4,
    "PageBlockHeading5": 5,
    "PageBlockHeading6": 6,
}

# Blocks whose payload is a media object we do not inline when reading.
_MEDIA_PLACEHOLDERS = {
    "PageBlockPhoto": "photo",
    "PageBlockVideo": "video",
    "PageBlockAudio": "audio",
    "PageBlockCollage": "collage",
    "PageBlockSlideshow": "slideshow",
    "PageBlockEmbed": "embedded content",
    "PageBlockEmbedPost": "embedded post",
    "PageBlockMap": "map",
    "PageBlockCover": "cover",
    "PageBlockChannel": "channel",
    "PageBlockRelatedArticles": "related articles",
}


def rich_text_to_markdown(node: Any) -> str:
    """Render a ``TypeRichText`` tree to Markdown."""
    if node is None:
        return ""
    name = type(node).__name__

    if name in ("TextEmpty",):
        return ""
    if name == "TextPlain":
        return getattr(node, "text", "") or ""
    if name == "TextConcat":
        return "".join(rich_text_to_markdown(part) for part in getattr(node, "texts", []) or [])

    inner = rich_text_to_markdown(getattr(node, "text", None))

    if name in _TEXT_WRAPPERS:
        if not inner:
            return ""
        open_tag, close_tag = _TEXT_WRAPPERS[name]
        return f"{open_tag}{inner}{close_tag}"
    if name == "TextUrl":
        url = getattr(node, "url", "") or ""
        return f"[{inner}]({url})" if url else inner
    if name == "TextEmail":
        return f"[{inner}](mailto:{getattr(node, 'email', '')})"
    if name == "TextPhone":
        return f"[{inner}](tel:{getattr(node, 'phone', '')})"
    if name == "TextMath":
        return f"${inner}$"
    if name == "TextImage":
        return "[image]"

    # TextAnchor, TextMention, TextHashtag, TextUrl-ish autodetected nodes and
    # anything newer: the inner text is the content, the decoration is not.
    return inner


def _list_items_to_markdown(items: list, ordered: bool) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items or [], start=1):
        marker = f"{index}." if ordered else "*"
        if getattr(item, "blocks", None):
            nested = "\n".join(page_block_to_markdown(block) for block in item.blocks).strip()
            first, _, rest = nested.partition("\n")
            lines.append(f"{marker} {first}")
            for line in rest.splitlines():
                lines.append(f"  {line}")
        else:
            text = rich_text_to_markdown(getattr(item, "text", None))
            lines.append(f"{marker} {text}")
    return lines


def _table_to_markdown(block: Any) -> str:
    rows = getattr(block, "rows", []) or []
    if not rows:
        return ""
    rendered: list[list[str]] = []
    header_index: Optional[int] = None
    for row_index, row in enumerate(rows):
        cells = getattr(row, "cells", []) or []
        rendered.append([rich_text_to_markdown(getattr(c, "text", None)) for c in cells])
        if header_index is None and any(getattr(c, "header", False) for c in cells):
            header_index = row_index

    width = max(len(row) for row in rendered)
    rendered = [row + [""] * (width - len(row)) for row in rendered]

    lines = []
    title = rich_text_to_markdown(getattr(block, "title", None))
    if title:
        lines.append(f"**{title}**")
        lines.append("")

    # Markdown needs a header row; a table sent without header cells gets an
    # empty one rather than losing its first row of data.
    if header_index is None:
        lines.append("| " + " | ".join([""] * width) + " |")
        lines.append("|" + "|".join([" --- "] * width) + "|")
        body = rendered
    else:
        lines.append("| " + " | ".join(rendered[header_index]) + " |")
        lines.append("|" + "|".join([" --- "] * width) + "|")
        body = [row for i, row in enumerate(rendered) if i != header_index]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def page_block_to_markdown(block: Any) -> str:
    """Render one ``TypePageBlock`` to Markdown."""
    if block is None:
        return ""
    name = type(block).__name__

    if name in _HEADINGS:
        text = rich_text_to_markdown(getattr(block, "text", None))
        return f"{'#' * _HEADINGS[name]} {text}" if text else ""
    if name in ("PageBlockParagraph", "PageBlockKicker", "PageBlockFooter"):
        return rich_text_to_markdown(getattr(block, "text", None))
    if name == "PageBlockPreformatted":
        language = getattr(block, "language", "") or ""
        return f"```{language}\n{rich_text_to_markdown(getattr(block, 'text', None))}\n```"
    if name in ("PageBlockBlockquote", "PageBlockPullquote"):
        text = rich_text_to_markdown(getattr(block, "text", None))
        quoted = "\n".join(f"> {line}" for line in text.splitlines()) or "> "
        caption = rich_text_to_markdown(getattr(block, "caption", None))
        return f"{quoted}\n> - {caption}" if caption else quoted
    if name == "PageBlockBlockquoteBlocks":
        inner = "\n".join(page_block_to_markdown(b) for b in getattr(block, "blocks", []) or [])
        return "\n".join(f"> {line}" for line in inner.splitlines())
    if name == "PageBlockList":
        return "\n".join(_list_items_to_markdown(getattr(block, "items", []), ordered=False))
    if name == "PageBlockOrderedList":
        return "\n".join(_list_items_to_markdown(getattr(block, "items", []), ordered=True))
    if name == "PageBlockTable":
        return _table_to_markdown(block)
    if name == "PageBlockDivider":
        return "---"
    if name == "PageBlockDetails":
        # A collapsible section. Its open/closed state is preserved because the
        # sender chose it, and a reader that flattens it loses that intent.
        title = rich_text_to_markdown(getattr(block, "title", None)) or "Details"
        state = "open" if getattr(block, "open", False) else "collapsed"
        inner = "\n".join(page_block_to_markdown(b) for b in getattr(block, "blocks", []) or [])
        return f"**{title}** ({state})\n{inner}".rstrip()
    if name == "PageBlockMath":
        return f"$$\n{rich_text_to_markdown(getattr(block, 'text', None))}\n$$"
    if name == "PageBlockThinking":
        inner = rich_text_to_markdown(getattr(block, "text", None))
        if not inner:
            inner = "\n".join(
                page_block_to_markdown(b) for b in getattr(block, "blocks", []) or []
            )
        return f"_[thinking]_ {inner}".rstrip()
    if name == "PageBlockAnchor":
        return ""
    if name in _MEDIA_PLACEHOLDERS:
        caption = getattr(block, "caption", None)
        caption_text = rich_text_to_markdown(getattr(caption, "text", None)) if caption else ""
        label = f"[{_MEDIA_PLACEHOLDERS[name]}]"
        return f"{label} {caption_text}".strip()
    if name == "PageBlockAuthorDate":
        author = rich_text_to_markdown(getattr(block, "author", None))
        return f"_{author}_" if author else ""

    # Unknown or explicitly unsupported: say so rather than dropping content
    # silently, so a new Telegram layer shows up as a visible gap.
    text = rich_text_to_markdown(getattr(block, "text", None))
    label = name.replace("PageBlock", "").lower()
    return f"[{label}] {text}".strip() if text else f"[{label}]"


def rich_message_to_markdown(rich: Any) -> str:
    """Render ``Message.rich_message`` to a Markdown document."""
    blocks = getattr(rich, "blocks", None) or []
    rendered = [page_block_to_markdown(block) for block in blocks]
    return "\n\n".join(part for part in rendered if part).strip()


def rich_message_to_dict(rich: Any) -> dict:
    """Compact view of a rich message for tool output.

    ``part`` marks a partial (streaming) rich message: the sender is still
    producing it, so the text is not final.
    """
    payload: dict[str, Any] = {"markdown": rich_message_to_markdown(rich)}
    photos = getattr(rich, "photos", None) or []
    documents = getattr(rich, "documents", None) or []
    if photos:
        payload["photos"] = len(photos)
    if documents:
        payload["documents"] = len(documents)
    if getattr(rich, "rtl", False):
        payload["rtl"] = True
    if getattr(rich, "part", False):
        payload["partial"] = True
    return payload


def message_rich_payload(msg: Any) -> Optional[dict]:
    """``rich_message`` view for a message, or None when it carries none."""
    rich = getattr(msg, "rich_message", None)
    if rich is None:
        return None
    return rich_message_to_dict(rich)
