"""Append-only JSONL audit storage, independent of Telegram connections."""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping


def audit_log_path(session_path: str | Path | None = None) -> Path:
    """Use an explicit override, a file session's directory, or XDG state storage.

    Callers should pass the resolved session filename, never a session string.
    """
    override = os.getenv("TELEGRAM_AUDIT_LOG")
    if override:
        return Path(override).expanduser()
    if session_path is not None and str(session_path) != ":memory:":
        return Path(session_path).parent / "audit_log.jsonl"
    base = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return base / "telegram-mcp" / "audit_log.jsonl"


def _make_parent(path: Path) -> None:
    """Create private directories without changing existing directories' modes."""
    if path.is_dir():
        return
    _make_parent(path.parent)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not path.is_dir():
            raise


@contextmanager
def _exclusive_lock(fd: int):
    if os.name == "nt":
        import msvcrt

        # Lock a byte beyond EOF without adding anything to the JSONL file.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def append_audit_record(record: Mapping, *, session_path: str | Path | None = None) -> None:
    """Append one metadata record; propagate failures for the caller to report.

    Serialization happens before opening the file. Cooperating writers lock each
    append, and opening afresh lets subsequent writes follow external rotation.
    This storage primitive does not filter secrets: callers must supply metadata
    only. It never logs failures to stdout or retries application operations.
    """
    data = (json.dumps(dict(record), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    path = audit_log_path(session_path)
    _make_parent(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with _exclusive_lock(fd):
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            remaining = memoryview(data)
            while remaining:
                written = os.write(fd, remaining)
                if written == 0:
                    raise OSError("Audit append made no progress")
                remaining = remaining[written:]
    finally:
        os.close(fd)
