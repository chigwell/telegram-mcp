"""Per-session file lock guarding against concurrent Telegram connections.

MCP clients (Claude Desktop in particular) sometimes spawn more than one
instance of this server for the same configured session -- most commonly
when a connector is restarted and the old process hasn't exited yet before
the new one starts. Two processes calling ``TelegramClient.connect()`` /
``start()`` with the same auth key at the same time trips Telegram's abuse
protection (``AuthKeyDuplicatedError``), which can knock out *both*
connections rather than just the newcomer.

This module provides an OS-level advisory file lock (``flock`` on POSIX,
``msvcrt.locking`` on Windows) keyed by the session's actual identity (its
string value or file path), not just the account label, since two different
projects/labels could otherwise collide or one label could map to different
sessions across deployments. The lock is held for the lifetime of the
process and is released automatically by the OS if the process exits or is
killed, so there is no stale-lock file to clean up.

A second instance racing for the same session waits briefly (covers the
"restart replaces the old process" case) and, if the lock is still held once
the grace period elapses, raises :class:`SessionLockError` instead of ever
calling ``connect()`` -- so the live session is never disturbed.

The lock can only ever see processes on this host, and those only collide
at Telegram when they reach it from different IPs (dual-stack or
split-tunnel hosts). Where every instance shares one egress IP,
``acquire(shared=True)`` takes the lock in shared mode instead: shared
holders coexist with each other, but a shared holder and an exclusive
holder never overlap, so an operator who never opted in keeps the
exclusive guarantee. (On Windows, ``msvcrt`` byte-range locks have no
shared mode; a shared holder there takes no lock at all.) The exclusive
holder leaves its PID in the lock file so a refused contender can name it.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from typing import IO, Optional

if os.name == "nt":
    import msvcrt

    def _try_lock(fh: IO, shared: bool = False) -> bool:
        if shared:
            return True  # no shared byte-range locks on Windows; see module docstring
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh: IO) -> None:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fh: IO, shared: bool = False) -> bool:
        try:
            mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
            fcntl.flock(fh.fileno(), mode | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh: IO) -> None:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


# Public names for the primitives above. The session pool in ``runtime`` needs
# the same "try to take an exclusive lock, don't block" behaviour on both
# platforms, and duplicating the msvcrt/fcntl split there is how one of the two
# copies ends up POSIX-only.
try_lock_exclusive = _try_lock
release_lock = _unlock


DEFAULT_LOCK_DIR = Path(tempfile.gettempdir()) / "telegram-mcp-locks"
DEFAULT_GRACE_SECONDS = 20.0
DEFAULT_POLL_INTERVAL = 0.5


class SessionLockError(RuntimeError):
    """Raised when a session lock isn't acquired before its grace period elapses."""


class SessionLock:
    """Exclusive, OS-released lock for one Telegram session, held for process lifetime."""

    def __init__(self, label: str, session_identity: str, *, lock_dir: Path = DEFAULT_LOCK_DIR):
        digest = hashlib.sha256(session_identity.encode("utf-8")).hexdigest()[:16]
        lock_dir.mkdir(parents=True, exist_ok=True)
        self.path = lock_dir / f"{label}-{digest}.lock"
        self._fh: Optional[IO] = None

    def acquire(
        self,
        *,
        grace_seconds: float = DEFAULT_GRACE_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        shared: bool = False,
    ) -> None:
        """Block (up to ``grace_seconds``) until the lock is free, then take it.

        ``shared`` takes the lock in shared mode: any number of shared holders
        coexist, but a shared holder and an exclusive holder never overlap.
        Raises :class:`SessionLockError` if the lock is still held once the
        grace period elapses.
        """
        fh = open(self.path, "a+")
        deadline = time.monotonic() + grace_seconds
        while True:
            if _try_lock(fh, shared=shared):
                self._fh = fh
                self._record_holder(shared)
                return
            if time.monotonic() >= deadline:
                fh.close()
                holder = self.holder_pid()
                where = (
                    f"lock {self.path} held by PID {holder}"
                    if holder
                    else f"lock held: {self.path}"
                )
                raise SessionLockError(
                    "Another telegram-mcp process is already connected with this "
                    f"session ({where}). Refusing to connect a second time to "
                    "avoid Telegram's AuthKeyDuplicatedError. If that other "
                    "process already exited, this lock will clear on its own -- "
                    "retry."
                )
            time.sleep(poll_interval)

    def release(self) -> None:
        if self._fh is not None:
            self._clear_holder()
            _unlock(self._fh)
            self._fh.close()
            self._fh = None

    def holder_pid(self) -> Optional[int]:
        """PID left in the lock file by the current exclusive holder, if any.

        Best effort: Windows refuses to read a locked byte range, and a shared
        holder records nothing (there may be several).
        """
        try:
            text = self.path.read_text().strip()
        except OSError:
            return None
        return int(text) if text.isdigit() else None

    def _record_holder(self, shared: bool) -> None:
        # Exclusive holders leave their PID for a refused contender to report;
        # shared holders wipe whatever a previous exclusive holder left behind.
        self._clear_holder()
        if not shared:
            try:
                self._fh.write(str(os.getpid()))
                self._fh.flush()
            except OSError:
                pass

    def _clear_holder(self) -> None:
        try:
            self._fh.seek(0)
            self._fh.truncate()
        except OSError:
            pass


def session_identity(client: object) -> str:
    """Derive a stable identity string for a ``TelegramClient``'s session.

    File-based sessions key on the absolute file path; string sessions key on
    their serialized value (same auth key -> same identity, regardless of
    which process or working directory loaded it). Falls back to the
    client's object id for sessionless test doubles.
    """
    session = getattr(client, "session", None)
    if session is not None:
        filename = getattr(session, "filename", None)
        if filename:
            return f"file:{os.path.abspath(filename)}"
        save = getattr(session, "save", None)
        if callable(save):
            try:
                saved = save()
            except Exception:
                saved = None
            if saved:
                return f"string:{saved}"
    return f"anon:{id(client)}"
