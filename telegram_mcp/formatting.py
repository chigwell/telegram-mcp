"""Formatting of outgoing text: one place for every tool that sends text to Telegram.

Message text and media captions accept the same options: a parse_mode, a
format_date chip and custom_emojis. prepare_text checks them together and says
how the text has to go out, so each tool only decides where to send it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from telethon.tl import types

from telegram_mcp.runtime import RICH_PARSE_MODES


@dataclass(frozen=True)
class PreparedText:
    """How one outgoing text has to be sent.

    entities is set when the text goes out verbatim with these entities (plain
    text with format_date or custom_emojis). rich_mode is set when the text asks
    for server-side rich formatting. With neither, the tool hands the text and
    its parse_mode to Telethon as before.
    """

    entities: Optional[List[types.TypeMessageEntity]] = None
    rich_mode: Optional[str] = None


def prepare_text(
    text: str,
    parse_mode: Optional[str],
    format_date: Optional[str],
    custom_emojis,
    *,
    rich_supported: bool = True,
    target: str = "messages",
):
    """Return (PreparedText, None) for one outgoing text, or (None, error).

    rich_supported is False where Telegram has no rich form of the text (media
    captions, scheduled messages); target names that text in the refusal.
    """
    conflict = _plain_text_conflict(parse_mode, format_date, custom_emojis)
    if conflict:
        return None, conflict
    if parse_mode and parse_mode.lower() in RICH_PARSE_MODES:
        if not rich_supported:
            return None, (
                f"parse_mode='{parse_mode}' is not supported for {target}. "
                "Use 'md', 'html' or 'plain'."
            )
        return PreparedText(rich_mode=parse_mode.lower()), None
    if format_date or custom_emojis:
        entities, error = _plain_entities(text, format_date, custom_emojis)
        if error:
            return None, error
        return PreparedText(entities=entities), None
    return PreparedText(), None


def telethon_parse_mode_kwargs(parse_mode: Optional[str]) -> dict:
    """Telethon kwargs for a classic parse_mode, for tools whose default is Markdown.

    Omitted parse_mode passes nothing, which keeps Telethon's client default
    (Markdown) for backward compatibility; 'plain' maps to None, which disables
    parsing.
    """
    if parse_mode is None:
        return {}
    return {"parse_mode": None if parse_mode.lower() == "plain" else parse_mode}


def _chip_conflict(parse_mode):
    """Return why format_date cannot combine with parse_mode, or None when it can."""
    if parse_mode:
        return "format_date needs plain-text messages (leave parse_mode unset)."
    return None


def _date_entity(message: str, format_date: str):
    """Return (entity, None) marking format_date as a tappable chip, or (None, error)."""
    idx = message.find(format_date)
    if idx < 0:
        return None, f"format_date '{format_date}' was not found in the message text."
    tokens = format_date.split()
    parts = tokens[0].split("/")
    if len(parts) not in (2, 3) or any(not p.isdigit() for p in parts):
        return (
            None,
            f"format_date '{format_date}' must look like 13/09, 13/09/2026 or 13/09 17:00.",
        )
    clock = None
    if len(tokens) == 2:
        clock = tokens[1].split(":")
        if len(clock) != 2 or any(not p.isdigit() for p in clock):
            return (
                None,
                f"format_date '{format_date}' must look like 13/09, 13/09/2026 or 13/09 17:00.",
            )
    if len(tokens) > 2:
        return (
            None,
            f"format_date '{format_date}' must look like 13/09, 13/09/2026 or 13/09 17:00.",
        )
    try:
        day, month = int(parts[0]), int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else datetime.now().year
        hour, minute = (int(clock[0]), int(clock[1])) if clock else (0, 0)
        date = datetime(year, month, day, hour, minute).astimezone()
    except ValueError:
        return None, f"format_date '{format_date}' is not a valid date."
    entity = types.MessageEntityFormattedDate(
        offset=len(message[:idx].encode("utf-16-le")) // 2,
        length=len(format_date.encode("utf-16-le")) // 2,
        date=date,
        short_date=len(parts) == 2,
        long_date=len(parts) == 3,
        short_time=clock is not None,
    )
    return entity, None


# Joiner, variation selectors and combining keycap: they extend the emoji before them.
_EMOJI_JOINERS = frozenset("\u200d\ufe0e\ufe0f\u20e3")
_CUSTOM_EMOJI_SHAPE = '{"emoji": "<fallback emoji>", "id": "<document id>"}'


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _extends_emoji(char: str) -> bool:
    """Code points that modify or join the emoji before them instead of starting one."""
    code = ord(char)
    return (
        char in _EMOJI_JOINERS
        or 0x1F3FB <= code <= 0x1F3FF  # skin tones
        or 0xE0020 <= code <= 0xE007F  # tag characters (subdivision flags)
    )


def _is_regional_indicator(char: str) -> bool:
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _is_keycap_base(text: str, i: int) -> bool:
    return text[i] in "#*0123456789" and text[i + 1 : i + 2] in ("\ufe0f", "\u20e3")


def _is_emoji_char(char: str) -> bool:
    """An emoji code point or an emoji-sequence component (VS16 or ZWJ).

    Emoji blocks and the RGI emoji scattered between them, listed instead of the
    old U+2100 floor: letterlike signs (\u2116 \u2122), math operators and control
    pictures are not emoji. Coarse on purpose \u2014 not a full Unicode emoji
    inventory. The keycap mark U+20E3 is deliberately absent: it only counts via
    its base.
    """
    code = ord(char)
    return char in "\u200d\ufe0f\u2139\u2934\u2935\u3030\u303d\u3297\u3299" or (
        0x2190 <= code <= 0x21FF  # arrows (\u2194 \u2195 \u2026, mostly with VS16)
        or 0x2300 <= code <= 0x23FF  # misc technical (\u231a \u231b \u23f0 \u2026)
        or 0x2460 <= code <= 0x24FF  # enclosed alphanumerics (\u24c2 \u2026)
        or 0x25A0 <= code <= 0x27BF  # geometric shapes, misc symbols, dingbats
        or 0x2B00 <= code <= 0x2BFF  # misc symbols and arrows (\u2b50 \u2b55 \u2026)
        or 0x1F000 <= code <= 0x1FAFF  # astral emoji blocks
    )


def _looks_like_emoji(text: str) -> bool:
    """Cheap fallback check, not a full emoji validation.

    Refuses spaces, letters and digits other than keycap bases, a leading modifier or
    joiner, a trailing joiner, an odd number of regional indicators, and text with no
    emoji code point or keycap base.
    """
    if not text or text[0] in "\u200d\ufe0f" or 0x1F3FB <= ord(text[0]) <= 0x1F3FF:
        return False
    if text[-1] == "\u200d" or sum(map(_is_regional_indicator, text)) % 2:
        return False
    if not any(_is_emoji_char(char) or _is_keycap_base(text, i) for i, char in enumerate(text)):
        return False  # plain punctuation and signs such as "\u2116" or "#" would just mark text
    return not any(
        char.isspace() or (char.isalnum() and char != "ℹ" and not _is_keycap_base(text, i))
        for i, char in enumerate(text)
    )  # U+2139 INFORMATION SOURCE is the only letter-form emoji (Ll, not So)


def _inside_longer_emoji(text: str, start: int, end: int) -> bool:
    """True when text[start:end] is only part of a longer emoji sequence in text."""
    if end < len(text) and _extends_emoji(text[end]):
        return True  # e.g. 👍 followed by a skin tone, or ❤ followed by U+FE0F
    if start and text[start - 1] == "\u200d":
        return True  # the second half of a ZWJ sequence
    if not _is_regional_indicator(text[start]):
        return False
    run = 0
    while start - run > 0 and _is_regional_indicator(text[start - run - 1]):
        run += 1
    return run % 2 == 1  # starts on the second letter of another flag


def _standalone_occurrences(text: str, fallback: str):
    """Yield each start index of fallback in text that is not inside a longer emoji."""
    start = text.find(fallback)
    while start >= 0:
        end = start + len(fallback)
        if _inside_longer_emoji(text, start, end):
            start = text.find(fallback, start + 1)
        else:
            yield start
            start = text.find(fallback, end)


def _custom_emoji_id(value):
    """Return value as a Telegram document ID (signed 64-bit, positive), or None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not (value.isascii() and value.isdigit()):
            return None
        value = int(value)
    if not isinstance(value, int) or not 0 < value < 2**63:
        return None
    return value


def _custom_emoji_item(item):
    """Return (fallback, document_id, None) for one custom_emojis entry, or an error."""
    if not isinstance(item, dict) or set(item) != {"emoji", "id"}:
        return None, None, f"Each custom_emojis entry must be {_CUSTOM_EMOJI_SHAPE}."
    fallback = item["emoji"]
    if not isinstance(fallback, str) or not _looks_like_emoji(fallback):
        return (
            None,
            None,
            f"custom_emojis emoji {fallback!r} must be the fallback emoji alone, "
            "without text or spaces.",
        )
    document_id = _custom_emoji_id(item["id"])
    if document_id is None:
        return (
            None,
            None,
            f"custom_emojis id {item['id']!r} must be a positive 64-bit integer "
            "(an int or a string of digits).",
        )
    return fallback, document_id, None


def _custom_emoji_entities(text: str, custom_emojis):
    """Return ([(entity, label)], None) per standalone fallback, or (None, error)."""
    if not isinstance(custom_emojis, list):
        return None, f"custom_emojis must be a list of {_CUSTOM_EMOJI_SHAPE} entries."
    spans = []
    for item in custom_emojis:
        fallback, document_id, error = _custom_emoji_item(item)
        if error:
            return None, error
        starts = list(_standalone_occurrences(text, fallback))
        if not starts:
            return (
                None,
                f"custom_emojis emoji '{fallback}' was not found as a standalone emoji "
                "in the message text (it may appear only inside a longer emoji such "
                "as 👍🏽).",
            )
        label = f"custom emoji '{fallback}' ({document_id})"
        spans.extend(
            (
                types.MessageEntityCustomEmoji(
                    offset=_utf16_len(text[:start]),
                    length=_utf16_len(fallback),
                    document_id=document_id,
                ),
                label,
            )
            for start in starts
        )
    return spans, None


def _plain_text_conflict(parse_mode, format_date, custom_emojis):
    """Return why format_date or custom_emojis cannot go with parse_mode, or None."""
    if parse_mode and custom_emojis:
        return "custom_emojis needs plain-text messages (leave parse_mode unset)."
    if parse_mode and format_date:
        return _chip_conflict(parse_mode)
    return None


def _plain_entities(text: str, format_date, custom_emojis):
    """Return (entities sorted by offset, None) for plain text, or (None, error)."""
    spans = []
    if custom_emojis:
        emojis, error = _custom_emoji_entities(text, custom_emojis)
        if error:
            return None, error
        spans.extend(emojis)
    if format_date:
        chip, error = _date_entity(text, format_date)
        if error:
            return None, error
        spans.append((chip, f"format_date '{format_date}'"))
    spans.sort(key=lambda span: span[0].offset)
    for (before, before_label), (after, after_label) in zip(spans, spans[1:]):
        if after.offset < before.offset + before.length:
            overlap = f"{before_label} and {after_label} overlap in the message text."
            return None, overlap
    return [entity for entity, _ in spans], None
