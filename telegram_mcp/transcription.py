"""Voice/video-note transcription: engines, SQLite cache, and batch budget.

Two engines behind one interface (see ``transcribe``):

* ``telegram`` - native Premium ``messages.transcribeAudio``. Free, but drops
  the last speech segment in roughly 2 of 3 real recordings (proven with
  per-segment timestamps against a local Whisper run on 2026-08-20 - see
  ``~/Workspace/AI Playground/docs/2026-08-20-telegram-voice-transcripts.md``).
  Async: a long recording comes back ``pending=True`` and must be polled.
* ``groq`` - Groq-hosted ``whisper-large-v3-turbo``. Does not drop the tail,
  but costs a download+upload per voice message and sends the audio to a
  third party. Primary engine by owner decision (2026-08-20); ``telegram``
  is the free/private fallback.

Every transcript is a machine reading, not a verbatim quote (proper names and
punctuation drift under both engines) - callers must surface ``source``
alongside ``text`` rather than presenting it as exact speech.
"""

import asyncio
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from telethon.tl import functions

from telegram_mcp.premium import account_is_premium, is_premium_rpc_error

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_TRANSCRIBE_MODES = {"off", "on-demand", "auto"}
ENGINES = {"telegram", "groq"}

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL = "whisper-large-v3-turbo"


def transcribe_mode() -> str:
    """``TELEGRAM_TRANSCRIBE``: off | on-demand | auto (default on-demand).

    ``off`` disables the dedicated tool and all auto-mixing. ``on-demand``
    (default) keeps the dedicated tool and fills cache hits into listings,
    but never spends a batch call fetching a new one. ``auto`` additionally
    prefetches missing transcripts into listings, bounded by
    :class:`TranscribeBudget`.
    """
    raw = os.getenv("TELEGRAM_TRANSCRIBE", "on-demand").strip().lower()
    if raw not in _TRANSCRIBE_MODES:
        accepted = ", ".join(sorted(_TRANSCRIBE_MODES))
        raise SystemExit(f"Invalid TELEGRAM_TRANSCRIBE '{raw}'. Expected one of: {accepted}.")
    return raw


def default_engine() -> str:
    """``TELEGRAM_TRANSCRIBE_ENGINE``: telegram | groq (default groq).

    Groq is the primary engine (owner decision 2026-08-20: native Telegram
    transcription drops the last speech segment in ~2 of 3 recordings).
    Override to ``telegram`` for chats that should never leave the server,
    or when GROQ_API_KEY / quota is unavailable. Per-call ``engine=`` on
    transcribe_voice always takes precedence over this default.
    """
    raw = os.getenv("TELEGRAM_TRANSCRIBE_ENGINE", "groq").strip().lower()
    if raw not in ENGINES:
        accepted = ", ".join(sorted(ENGINES))
        raise SystemExit(
            f"Invalid TELEGRAM_TRANSCRIBE_ENGINE '{raw}'. Expected one of: {accepted}."
        )
    return raw


def validate_transcription_config() -> None:
    """Fail loudly at startup on a bad toggle, matching TELEGRAM_EXPOSED_TOOLS."""
    transcribe_mode()
    default_engine()


# ---------------------------------------------------------------------------
# SQLite cache
# ---------------------------------------------------------------------------
#
# This is a personal-chat transcript store in plaintext on the VPS - mode 600,
# its own directory (not the session/app directory), mounted as a volume so a
# rebuild doesn't silently drop it (docker-compose.yml has no volume by
# default).


def cache_dir() -> Path:
    raw = os.getenv("TELEGRAM_TRANSCRIPT_CACHE_DIR", "data/transcripts")
    d = Path(raw)
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _cache_db_path() -> Path:
    return cache_dir() / "transcripts.db"


def _connect() -> sqlite3.Connection:
    path = _cache_db_path()
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transcripts (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            text TEXT NOT NULL,
            duration INTEGER,
            lang TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id, source)
        )
        """)
    _migrate_source_into_key(conn)
    conn.commit()
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _migrate_source_into_key(conn: sqlite3.Connection) -> None:
    """Older builds keyed the cache on (chat_id, message_id) alone, so a cheap
    telegram transcript permanently shadowed the groq one - including for
    callers that asked for groq explicitly. Rebuild such a table in place."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transcripts'"
    ).fetchone()
    if not row or not row[0]:
        return
    if "PRIMARY KEY (chat_id, message_id, source)" in row[0]:
        return
    conn.executescript("""
        ALTER TABLE transcripts RENAME TO transcripts_legacy;
        CREATE TABLE transcripts (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            text TEXT NOT NULL,
            duration INTEGER,
            lang TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY (chat_id, message_id, source)
        );
        INSERT OR IGNORE INTO transcripts
            (chat_id, message_id, source, text, duration, lang, created_at)
        SELECT chat_id, message_id, source, text, duration, lang, created_at
        FROM transcripts_legacy;
        DROP TABLE transcripts_legacy;
        """)


def get_cached_transcript(
    chat_id: int, message_id: int, source: Optional[str] = None
) -> Optional[dict]:
    """Cached transcript for a message.

    ``source`` pins the engine: asking for groq must never be answered with a
    telegram transcript, because the native engine drops the recording's last
    segment and the loss is invisible in the text. Callers that only want to
    display whatever exists (listings, backfill skip checks) pass None and get
    the default engine's row when there is one, any row otherwise.
    """
    conn = _connect()
    try:
        if source is not None:
            row = conn.execute(
                "SELECT source, text, duration, lang, created_at FROM transcripts "
                "WHERE chat_id = ? AND message_id = ? AND source = ?",
                (chat_id, message_id, source),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT source, text, duration, lang, created_at FROM transcripts "
                "WHERE chat_id = ? AND message_id = ? "
                "ORDER BY source = ? DESC, created_at DESC LIMIT 1",
                (chat_id, message_id, default_engine()),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    source, text, duration, lang, created_at = row
    return {
        "source": source,
        "text": text,
        "duration": duration,
        "lang": lang,
        "created_at": created_at,
    }


def save_transcript(
    chat_id: int,
    message_id: int,
    source: str,
    text: str,
    *,
    duration: Optional[int] = None,
    lang: Optional[str] = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO transcripts "
            "(chat_id, message_id, source, text, duration, lang, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id,
                message_id,
                source,
                text,
                duration,
                lang,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Voice/video-note detection and formatting helpers
# ---------------------------------------------------------------------------


def is_transcribable(msg) -> bool:
    """True for voice notes and video notes (video circles) - both go through
    the same transcribeAudio/Groq path."""
    return getattr(msg, "voice", None) is not None or getattr(msg, "video_note", None) is not None


def voice_duration(msg) -> Optional[int]:
    """Seconds, from the already-fetched message - no extra API call."""
    f = getattr(msg, "file", None)
    if f is None:
        return None
    d = getattr(f, "duration", None)
    return int(d) if d is not None else None


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "?:??"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------

_POLL_ATTEMPTS = 10
_POLL_INTERVAL_SECONDS = 2.0


async def _transcribe_via_telegram(cl, entity, msg) -> dict:
    """Native Premium transcription. Polls while pending=True - proven live on
    2026-08-20: an 83s recording matured after 3 polls (~6.6s); without
    polling, long voice messages come back empty."""
    if not await account_is_premium(cl):
        return {"status": "premium_required"}
    peer = await cl.get_input_entity(entity)
    try:
        result = await cl(functions.messages.TranscribeAudioRequest(peer=peer, msg_id=msg.id))
    except Exception as e:
        if is_premium_rpc_error(e):
            return {"status": "premium_required"}
        return {"status": "error", "error": str(e)}

    attempts = 0
    while getattr(result, "pending", False) and attempts < _POLL_ATTEMPTS:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
        attempts += 1
        try:
            result = await cl(functions.messages.TranscribeAudioRequest(peer=peer, msg_id=msg.id))
        except Exception as e:
            return {"status": "error", "error": str(e)}

    if getattr(result, "pending", False):
        return {"status": "pending"}
    return {"status": "ok", "text": result.text}


async def _transcribe_via_groq(cl, msg) -> dict:
    """Groq whisper-large-v3-turbo. Downloads the voice note into memory
    (never touches disk) and deletes the bytes as soon as the request
    returns."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return {"status": "error", "error": "GROQ_API_KEY is not configured"}

    try:
        data = await cl.download_media(msg, file=bytes)
    except Exception as e:
        return {"status": "error", "error": f"download failed: {e}"}
    if not data:
        return {"status": "error", "error": "empty media download"}

    file_attr = getattr(msg, "file", None)
    ext = (getattr(file_attr, "ext", None) or ".oga").lstrip(".")
    mime = getattr(file_attr, "mime_type", None) or "audio/ogg"

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                GROQ_TRANSCRIBE_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                data={"model": GROQ_MODEL, "response_format": "verbose_json"},
                files={"file": (f"voice.{ext}", bytes(data), mime)},
            )
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        return {"status": "error", "error": f"groq request failed: {e}"}
    finally:
        del data

    text = (payload.get("text") or "").strip()
    if not text:
        return {"status": "error", "error": "empty transcript from groq"}
    return {"status": "ok", "text": text, "lang": payload.get("language")}


async def transcribe(cl, entity, msg, engine: str) -> dict:
    """Dispatch to one engine. Returns a status dict:
    {"status": "ok", "text": ..., "lang": ...} |
    {"status": "pending"} | {"status": "premium_required"} |
    {"status": "error", "error": ...}
    """
    if engine == "telegram":
        return await _transcribe_via_telegram(cl, entity, msg)
    if engine == "groq":
        return await _transcribe_via_groq(cl, msg)
    return {"status": "error", "error": f"Unknown engine '{engine}'"}


# ---------------------------------------------------------------------------
# Batch budget for auto-mixing (mode="auto" only)
# ---------------------------------------------------------------------------
#
# Groq is not a free call like native transcription: each voice message costs
# a download + upload. Without a budget, get_history(limit=100) on a chat with
# a few dozen voice messages would turn into a few dozen downloads/uploads on
# every read. See TELEGRAM_TRANSCRIBE_MAX_VOICES / _MAX_SECONDS.


class TranscribeBudget:
    def __init__(
        self,
        max_voices: Optional[int] = None,
        max_seconds: Optional[int] = None,
    ):
        self.max_voices = max_voices if max_voices is not None else _max_voices_env()
        self.max_seconds = max_seconds if max_seconds is not None else _max_seconds_env()
        self.used_voices = 0
        self.used_seconds = 0

    def can_afford(self, duration: Optional[int]) -> bool:
        if self.used_voices >= self.max_voices:
            return False
        if self.used_seconds + (duration or 0) > self.max_seconds:
            return False
        return True

    def charge(self, duration: Optional[int]) -> None:
        self.used_voices += 1
        self.used_seconds += duration or 0


def _max_voices_env() -> int:
    try:
        return int(os.getenv("TELEGRAM_TRANSCRIBE_MAX_VOICES", "5"))
    except ValueError:
        return 5


def _max_seconds_env() -> int:
    try:
        return int(os.getenv("TELEGRAM_TRANSCRIBE_MAX_SECONDS", "300"))
    except ValueError:
        return 300


async def prefetch_transcripts(cl, entity, chat_id: int, messages) -> None:
    """Fill the cache for cache-miss voice/video-note messages, within budget.

    Only called for TELEGRAM_TRANSCRIBE=auto. on-demand mode leaves cache
    misses to render as "transcript pending" - callers must invoke
    transcribe_voice explicitly to pay for them.
    """
    if transcribe_mode() != "auto":
        return
    budget = TranscribeBudget()
    engine = default_engine()
    for msg in messages:
        if not is_transcribable(msg):
            continue
        if getattr(msg, "message", None):
            continue  # already has text, nothing to fill
        if get_cached_transcript(chat_id, msg.id) is not None:
            continue
        duration = voice_duration(msg)
        if not budget.can_afford(duration):
            continue
        budget.charge(duration)
        try:
            result = await transcribe(cl, entity, msg, engine)
        except Exception:
            continue
        if result.get("status") == "ok":
            save_transcript(
                chat_id,
                msg.id,
                engine,
                result["text"],
                duration=duration,
                lang=result.get("lang"),
            )


# ---------------------------------------------------------------------------
# Render helper shared by message_to_dict / format_message_line / list_messages
# ---------------------------------------------------------------------------


def voice_attachment_info(msg, chat_id: Optional[int]) -> Optional[dict]:
    """None for non-voice-like messages. Otherwise:
    {"duration": int|None,
     "transcript": str|None, "transcript_source": str|None,
     "transcript_status": "ready"|"pending"|None}

    transcript_status is None when transcription is off or no chat_id is
    available (duration still fills in - it's free, already on the fetched
    message). "pending" covers both "never attempted" (on-demand mode,
    budget exhausted) and "attempted but Telegram is still processing" -
    callers don't need to tell those apart, both mean "call transcribe_voice
    later".
    """
    if not is_transcribable(msg):
        return None
    info = {
        "duration": voice_duration(msg),
        "transcript": None,
        "transcript_source": None,
        "transcript_status": None,
    }
    if chat_id is None or transcribe_mode() == "off":
        return info
    cached = get_cached_transcript(chat_id, msg.id)
    if cached is not None:
        info["transcript"] = cached["text"]
        info["transcript_source"] = cached["source"]
        info["transcript_status"] = "ready"
    else:
        info["transcript_status"] = "pending"
    return info


def render_voice_text(info: dict) -> str:
    """`voice 0:23` / `voice 0:23 | transcript: ... (source: groq)` / with a
    pending marker - never presented as a verbatim quote."""
    label = f"voice {format_duration(info['duration'])}"
    status = info["transcript_status"]
    if status == "ready":
        return f"{label} | transcript ({info['transcript_source']}, not verbatim): {info['transcript']}"
    if status == "pending":
        return f"{label} | transcript pending"
    return label
