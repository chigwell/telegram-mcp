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


def sanitize_user_content(text: Optional[str], max_length: int = 4096) -> str:
    """Sanitize user-controlled text content before returning in tool results.

    - Returns "[empty]" for None / empty input
    - Strips Unicode control characters (Cc, Cf) except newline and tab
    - Strips zero-width / invisible characters
    - Collapses excessive consecutive newlines (>2) to 2
    - Truncates to max_length with a marker

    This does NOT attempt keyword-based injection detection (too brittle).
    The real defence is the structural JSON boundary in tool results.
    """
    if not text:
        return "[empty]"

    # Strip control characters except \n (0x0a) and \t (0x09)
    cleaned = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf"):
            if ch in ("\n", "\t"):
                cleaned.append(ch)
            # else: drop the character
        else:
            cleaned.append(ch)
    result = "".join(cleaned)

    # Strip invisible / zero-width characters
    result = _INVISIBLE_CHARS.sub("", result)

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
    """Recursively sanitize all string values in a nested dict/list structure.

    Use this for raw Telegram API responses (e.g. to_dict()) where
    user-controlled content can appear at any nesting depth.
    """
    if isinstance(data, dict):
        return {k: sanitize_dict(v) for k, v in data.items()}
    if isinstance(data, list):
        return [sanitize_dict(item) for item in data]
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
