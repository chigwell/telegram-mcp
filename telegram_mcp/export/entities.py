"""Message formatting: Telegram entities -> HTML / Markdown / plain text.

Telegram entity offsets are counted in UTF-16 code units, not Python
characters. An emoji or any astral-plane character shifts every later offset by
one, so slicing the Python string directly corrupts formatting in exactly the
messages people paste screenshots of. Everything here works on the UTF-16-LE
encoding and decodes each slice back.
"""

import html
from typing import Any, Iterable, Optional

# Entity class names (Telethon types) mapped to a neutral kind. Names are used
# rather than imports so an unknown/newer entity degrades to plain text instead
# of raising.
_KIND_BY_CLASS = {
    "MessageEntityBold": "bold",
    "MessageEntityItalic": "italic",
    "MessageEntityUnderline": "underline",
    "MessageEntityStrike": "strike",
    "MessageEntitySpoiler": "spoiler",
    "MessageEntityCode": "code",
    "MessageEntityPre": "pre",
    "MessageEntityBlockquote": "quote",
    "MessageEntityTextUrl": "text_url",
    "MessageEntityUrl": "url",
    "MessageEntityEmail": "email",
    "MessageEntityPhone": "phone",
    "MessageEntityMention": "mention",
    "MessageEntityMentionName": "mention_name",
    "MessageEntityHashtag": "hashtag",
    "MessageEntityCashtag": "hashtag",
    "MessageEntityBotCommand": "plain",
    "MessageEntityCustomEmoji": "plain",
    "MessageEntityBankCard": "plain",
    "MessageEntityUnknown": "plain",
}


def _u16(text: str) -> bytes:
    return text.encode("utf-16-le")


def _slice(buf: bytes, start: int, end: int) -> str:
    return buf[start * 2 : end * 2].decode("utf-16-le", errors="replace")


def entity_kind(entity: Any) -> str:
    return _KIND_BY_CLASS.get(type(entity).__name__, "plain")


def entities_to_json(entities: Optional[Iterable[Any]]) -> list[dict]:
    """A compact, engine-independent description kept in the JSONL record."""
    out: list[dict] = []
    for entity in entities or []:
        item = {
            "type": entity_kind(entity),
            "class": type(entity).__name__,
            "offset": getattr(entity, "offset", None),
            "length": getattr(entity, "length", None),
        }
        url = getattr(entity, "url", None)
        if url:
            item["url"] = url
        user_id = getattr(entity, "user_id", None)
        if user_id:
            item["user_id"] = user_id
        language = getattr(entity, "language", None)
        if language:
            item["language"] = language
        document_id = getattr(entity, "document_id", None)
        if document_id:
            item["document_id"] = str(document_id)
        out.append(item)
    return out


def _segments(text: str, entities: list[dict]) -> list[tuple[str, list[dict]]]:
    """Split the text at every entity boundary.

    Each segment carries the entities covering it, outermost first. Nested and
    overlapping entities (bold inside a link, say) therefore re-open per segment
    instead of producing crossed tags.
    """
    buf = _u16(text)
    total = len(buf) // 2
    usable = [
        e
        for e in entities
        if isinstance(e.get("offset"), int)
        and isinstance(e.get("length"), int)
        and e["length"] > 0
    ]
    bounds = {0, total}
    for e in usable:
        bounds.add(max(0, min(total, e["offset"])))
        bounds.add(max(0, min(total, e["offset"] + e["length"])))
    ordered = sorted(bounds)
    segments: list[tuple[str, list[dict]]] = []
    for start, end in zip(ordered, ordered[1:]):
        if end <= start:
            continue
        covering = [e for e in usable if e["offset"] <= start and e["offset"] + e["length"] >= end]
        covering.sort(key=lambda e: (e["offset"], -e["length"]))
        segments.append((_slice(buf, start, end), covering))
    return segments


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def _html_wrap(kind: str, entity: dict, inner: str) -> str:
    if kind == "bold":
        return f"<strong>{inner}</strong>"
    if kind == "italic":
        return f"<em>{inner}</em>"
    if kind == "underline":
        return f"<u>{inner}</u>"
    if kind == "strike":
        return f"<s>{inner}</s>"
    if kind == "spoiler":
        return f'<span class="spoiler">{inner}</span>'
    if kind == "code":
        return f"<code>{inner}</code>"
    if kind == "pre":
        return f"<pre>{inner}</pre>"
    if kind == "quote":
        return f"<blockquote>{inner}</blockquote>"
    if kind == "text_url":
        return f'<a href="{html.escape(entity.get("url") or "", quote=True)}">{inner}</a>'
    if kind == "url":
        return f'<a href="{html.escape(inner, quote=True)}">{inner}</a>'
    if kind == "email":
        return f'<a href="mailto:{html.escape(inner, quote=True)}">{inner}</a>'
    if kind == "phone":
        return f'<a href="tel:{html.escape(inner, quote=True)}">{inner}</a>'
    if kind == "mention":
        return f'<a href="https://t.me/{html.escape(inner.lstrip("@"), quote=True)}">{inner}</a>'
    if kind == "mention_name":
        return f'<a href="https://t.me/user?id={entity.get("user_id")}">{inner}</a>'
    if kind == "hashtag":
        return f'<span class="tag">{inner}</span>'
    return inner


def to_html(text: Optional[str], entities: Optional[list[dict]]) -> str:
    if not text:
        return ""
    if not entities:
        return html.escape(text).replace("\n", "<br>")
    parts: list[str] = []
    for chunk, covering in _segments(text, entities):
        rendered = html.escape(chunk)
        for entity in reversed(covering):
            rendered = _html_wrap(entity["type"], entity, rendered)
        parts.append(rendered)
    return "".join(parts).replace("\n", "<br>")


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

_MD_WRAP = {
    "bold": ("**", "**"),
    "italic": ("_", "_"),
    "underline": ("__", "__"),
    "strike": ("~~", "~~"),
    "spoiler": ("||", "||"),
    "code": ("`", "`"),
}


def to_markdown(text: Optional[str], entities: Optional[list[dict]]) -> str:
    if not text:
        return ""
    if not entities:
        return text
    parts: list[str] = []
    for chunk, covering in _segments(text, entities):
        rendered = chunk
        for entity in reversed(covering):
            kind = entity["type"]
            if kind in _MD_WRAP:
                open_tag, close_tag = _MD_WRAP[kind]
                rendered = f"{open_tag}{rendered}{close_tag}"
            elif kind == "pre":
                language = entity.get("language") or ""
                rendered = f"\n```{language}\n{rendered}\n```\n"
            elif kind == "quote":
                rendered = "\n".join(f"> {line}" for line in rendered.splitlines()) or "> "
            elif kind == "text_url":
                rendered = f"[{rendered}]({entity.get('url') or ''})"
        parts.append(rendered)
    return "".join(parts)


def to_plain(text: Optional[str], entities: Optional[list[dict]] = None) -> str:
    return text or ""
