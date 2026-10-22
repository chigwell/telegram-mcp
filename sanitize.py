"""
Sanitization utilities for telegram-mcp.

All user-controlled content (message text, display names, chat titles,
button labels, etc.) returned in MCP tool results MUST be sanitized
before inclusion. This prevents prompt injection attacks where malicious
Telegram content could manipulate the LLM consuming these tool results.

Defence strategy:
1. Structural boundary — tool results use JSON, so user content sits
   inside JSON string values and cannot be confused with field names
   or tool-level instructions.
2. Content sanitization (this module) — strips control characters,
   zero-width characters, and truncates excessively long content as
   defence-in-depth inside JSON values.
"""

import json
import re
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional

# Zero-width and invisible Unicode characters that can be used to hide content
_INVISIBLE_CHARS = re.compile(
    "["
    "\u200b"  # zero width space
    "\u200c"  # zero width non-joiner
    "\u200d"  # zero width joiner
    "\u200e"  # left-to-right mark
    "\u200f"  # right-to-left mark
    "\u2028"  # line separator
    "\u2029"  # paragraph separator
    "\u202a-\u202e"  # bidi embedding/override
    "\u2060"  # word joiner
    "\u2061-\u2064"  # invisible operators
    "\ufeff"  # zero width no-break space / BOM
    "\ufff9-\ufffb"  # interlinear annotations
    "]"
)

# Three or more consecutive newlines → collapse to two
_EXCESSIVE_NEWLINES = re.compile(r"\n{3,}")


# Scripts whose words need U+200C (ZWNJ): Arabic/Persian and the Indic blocks.
_ZWNJ_SCRIPT_RANGES = ((0x0600, 0x08FF), (0x0900, 0x0DFF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))


def _in_zwnj_script(ch: str) -> bool:
    return any(low <= ord(ch) <= high for low, high in _ZWNJ_SCRIPT_RANGES)


def _is_emoji_part(ch: str) -> bool:
    """A symbol that can sit next to a ZWJ inside an emoji sequence."""
    return (
        unicodedata.category(ch) == "So"
        or ch == "\ufe0f"  # emoji presentation selector
        or "\U0001f3fb" <= ch <= "\U0001f3ff"  # skin tone modifiers
    )


def _joiner_glues_text(text: str, index: int) -> bool:
    """True for a ZWJ/ZWNJ that is part of a word or emoji rather than a hidden character.

    A ZWJ between two emoji parts builds one composite emoji (family, flag, profession);
    a ZWJ/ZWNJ between two letters of a joining script (Persian, Hindi ...) is spelling.
    Anywhere else - e.g. between Latin letters, where it could split a word to hide it -
    they stay stripped.
    """
    if index == 0 or index + 1 >= len(text):
        return False
    before, after = text[index - 1], text[index + 1]
    if text[index] == "\u200d" and _is_emoji_part(before) and _is_emoji_part(after):
        return True
    return (
        _in_zwnj_script(before)
        and _in_zwnj_script(after)
        and unicodedata.category(before)[0] in "LM"
        and unicodedata.category(after)[0] in "LM"
    )


def sanitize_user_content(
    text: Optional[str], max_length: int = 4096, *, preserve_emoji: bool = False
) -> str:
    """Sanitize user-controlled text content before returning in tool results.

    - Returns "[empty]" for None / empty input
    - Strips Unicode control characters (Cc, Cf) except newline and tab
    - Strips zero-width / invisible characters, except a ZWJ inside an emoji sequence or a
      ZWNJ inside a word of a joining script, which would otherwise break the text
    - Collapses excessive consecutive newlines (>2) to 2
    - Truncates to max_length with a marker
    - With preserve_emoji, retains every ZWJ and subdivision-flag tag characters

    This does NOT attempt keyword-based injection detection (too brittle).
    The real defence is the structural JSON boundary in tool results.
    """
    if not text:
        return "[empty]"

    cleaned = []
    for i, ch in enumerate(text):
        if ch in ("\u200c", "\u200d") and _joiner_glues_text(text, i):
            cleaned.append(ch)
        elif unicodedata.category(ch) in ("Cc", "Cf"):
            # Strip control characters except \n (0x0a) and \t (0x09)
            if ch in ("\n", "\t"):
                cleaned.append(ch)
            elif preserve_emoji and (ch == "\u200d" or "\U000e0020" <= ch <= "\U000e007f"):
                cleaned.append(ch)
            # else: drop the character
        elif not _INVISIBLE_CHARS.match(ch):
            # Zero-width / invisible characters outside Cc/Cf (line/paragraph separators)
            cleaned.append(ch)
    result = "".join(cleaned)

    # Collapse excessive newlines
    result = _EXCESSIVE_NEWLINES.sub("\n\n", result)

    # Strip leading/trailing whitespace
    result = result.strip()

    if not result:
        return "[empty]"

    # Truncate
    if len(result) > max_length:
        result = result[:max_length] + "... [truncated]"

    return result


def sanitize_name(text: Optional[str], max_length: int = 256) -> str:
    """Sanitize a display name (username, chat title, sender name).

    Names should be single-line, so newlines are stripped entirely
    in addition to the standard sanitization.
    """
    result = sanitize_user_content(text, max_length=max_length)
    # Names must be single-line
    result = result.replace("\n", " ").replace("\r", " ")
    # Collapse multiple spaces that might result from newline replacement
    result = re.sub(r" {2,}", " ", result).strip()
    return result


def sanitize_dict(data: Any) -> Any:
    """Recursively sanitize all string and bytes values in a nested dict/list structure.

    Use this for raw Telegram API responses (e.g. to_dict()) where
    user-controlled content can appear at any nesting depth. Bytes are
    decoded (UTF-8, replacing errors) and sanitized as text, matching how
    they are later serialized to JSON.
    """
    if isinstance(data, dict):
        return {k: sanitize_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_dict(item) for item in data]
    if isinstance(data, bytes):
        # Decode at the sanitization point: _json_default only decodes at
        # serialization time, which would let control bytes slip through raw.
        return sanitize_user_content(data.decode("utf-8", errors="replace"), max_length=4096)
    if isinstance(data, str):
        return sanitize_user_content(data, max_length=4096)
    return data


def _json_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def format_tool_result(
    records: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Format tool output as a JSON string.

    All tool functions that return user-controlled content should use
    this formatter. The JSON structure provides an unambiguous boundary
    between trusted field names and untrusted user-generated values.
    """
    payload: Dict[str, Any] = {"results": records}
    if metadata:
        payload.update(metadata)
    return json.dumps(payload, ensure_ascii=False, default=_json_default)
