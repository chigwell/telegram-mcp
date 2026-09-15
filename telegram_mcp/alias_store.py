"""Atomic alias persistence and locking; no Telegram client initialization."""

from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict

from sanitize import sanitize_name
from telegram_mcp.alias_matching import alias_key

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

_ALIASES_ENV = "TELEGRAM_ALIASES_FILE"
logger = logging.getLogger("telegram_mcp")


def aliases_file_path() -> Path:
    """Runtime data location, never the install directory (may be read-only)."""
    override = os.getenv(_ALIASES_ENV)
    if override:
        return Path(override)
    base = os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "telegram-mcp" / "aliases.json"


def load_aliases(strict: bool = False, *, legacy_path: Path) -> Dict[str, Dict[str, Any]]:
    """Return {key: {"id": int, "name": str|None, "account": str|None}}.

    Legacy `{alias: id}` files upgrade on read. Reads tolerate damaged files by
    default; strict reads reject unreadable data before a read-modify-write cycle.
    """
    path = aliases_file_path()
    if not path.exists() and not os.getenv(_ALIASES_ENV):
        path = legacy_path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("aliases file must be a JSON object")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as error:
        logger.warning("Ignoring unreadable aliases file; saved aliases were not changed.")
        if strict:
            # Refuse to write over data we could not read: a degraded read plus a
            # write-back would silently delete every alias in the file.
            raise AliasStoreUnreadable(
                "Saved contacts could not be read; no changes were written. "
                "Check the aliases file and retry."
            ) from error
        return {}

    records: Dict[str, Dict[str, Any]] = {}
    for alias, value in raw.items():
        record = {"id": value} if not isinstance(value, dict) else dict(value)
        try:
            record["id"] = int(record["id"])
        except (KeyError, TypeError, ValueError):
            continue  # skip the bad row, keep every good one
        record["name"] = sanitize_name(str(record["name"])) if record.get("name") else None
        record.setdefault("account", None)  # uniform shape for legacy rows
        records[alias_key(str(alias))] = record
    return records


def save_aliases(aliases: Dict[str, Any]) -> None:
    """Atomically persist aliases 0600 — the file maps nicknames to real people."""
    path = aliases_file_path()
    if not os.getenv(_ALIASES_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            recoverable = isinstance(existing, dict)
        except (OSError, ValueError):
            recoverable = False
        if not recoverable:
            # Never overwrite a file we could not parse; it may be hand-recoverable.
            path.replace(path.with_suffix(f".corrupt-{int(time.time())}"))

    payload = {
        alias_key(str(k)): (v if isinstance(v, dict) else {"id": int(v)})
        for k, v in aliases.items()
    }
    # mkstemp creates a fresh 0600 file with an unpredictable name: a fixed
    # ".tmp" is both a symlink target and a collision point between processes.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # atomic: a crash leaves the previous file intact
    except BaseException:
        os.unlink(tmp)
        raise


class AliasStoreUnreadable(Exception):
    """The alias file exists but could not be read, so writing would destroy it."""


@contextmanager
def _alias_lock(path: Path):
    """Serialize read-modify-write cycles across processes (best effort)."""
    if fcntl is None:  # pragma: no cover - Windows
        yield
        return
    lock_fd = os.open(str(path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock_fd)


def update_aliases(mutate, *, legacy_path: Path):
    """Apply `mutate(aliases)` to the alias file under an exclusive lock.

    Two tool calls that each load, change and save the whole map would otherwise
    lose one of the two writes — including a delete silently coming back.
    """
    path = aliases_file_path()
    if not os.getenv(_ALIASES_ENV):
        path.parent.mkdir(parents=True, exist_ok=True)
    with _alias_lock(path):
        aliases = load_aliases(strict=True, legacy_path=legacy_path)
        result = mutate(aliases)
        save_aliases(aliases)
        return result
