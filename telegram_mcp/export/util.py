"""Small shared helpers: JSON encoding, filenames, dates, progress."""

import base64
import datetime as dt
import re
import sys
import unicodedata
from typing import Any, Optional


def json_default(obj: Any) -> Any:
    """Make Telethon's ``to_dict()`` output JSON-serializable without loss.

    Telethon returns datetimes, raw bytes (file references, access hashes) and
    enum-ish objects. Bytes become base64 rather than a lossy repr, so a raw
    record can still be inspected byte for byte later.
    """
    if isinstance(obj, (dt.datetime, dt.date)):
        return obj.isoformat()
    if isinstance(obj, dt.timedelta):
        return obj.total_seconds()
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return {"__bytes_b64__": base64.b64encode(bytes(obj)).decode("ascii")}
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(value: str, limit: int = 60) -> str:
    """Filesystem-safe, human-readable slug. Keeps Cyrillic and spaces."""
    value = unicodedata.normalize("NFC", value or "")
    value = _UNSAFE.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    if len(value) > limit:
        value = value[:limit].rstrip(" .")
    return value or "untitled"


def parse_date(value: str) -> dt.datetime:
    """``YYYY-MM-DD`` or a full ISO timestamp, always returned tz-aware (UTC)."""
    raw = value.strip()
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise SystemExit(f"Cannot parse date '{value}'. Use YYYY-MM-DD or an ISO timestamp.")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def months_ago(months: int) -> dt.datetime:
    """Approximate month arithmetic without pulling in dateutil."""
    now = dt.datetime.now(dt.timezone.utc)
    year = now.year
    month = now.month - months
    while month <= 0:
        month += 12
        year -= 1
    day = min(
        now.day,
        [
            31,
            29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ][month - 1],
    )
    return now.replace(year=year, month=month, day=day)


def human_size(num: Optional[int]) -> str:
    if num is None:
        return "?"
    step = 1024.0
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < step or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} GB"


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "?:??"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def log(message: str) -> None:
    """Progress goes to stderr so stdout stays pipeable."""
    print(message, file=sys.stderr, flush=True)
