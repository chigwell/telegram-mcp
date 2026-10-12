"""Single-owner process lock and diagnostics for the Telegram connection.

The lock itself is the source of truth.  The adjacent JSON file is diagnostic
metadata only: it may outlive a crashed process and therefore must never be
used on its own to decide that Telegram is busy.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


EXIT_BUSY = 75
DEFAULT_LOCK_PATH = Path.home() / ".local" / "state" / "telegram-mcp.lock"


def lock_path() -> Path:
    return Path(os.environ.get("TELEGRAM_MCP_LOCK", str(DEFAULT_LOCK_PATH))).expanduser()


def metadata_path(path: Path | None = None) -> Path:
    selected = path or lock_path()
    return selected.with_name(f"{selected.name}.owner.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_process_field(pid: int, field: str) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", f"{field}="],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def process_start_token(pid: int) -> str | None:
    """Return an OS-observed process start marker used to detect PID reuse."""
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        fields = proc_stat.read_text(encoding="utf-8").split()
        if len(fields) > 21:
            return f"proc:{fields[21]}"
    except OSError:
        pass
    return _read_process_field(pid, "lstart")


def process_command(pid: int) -> str | None:
    return _read_process_field(pid, "command")


def process_ancestry(pid: int, *, limit: int = 8) -> list[dict[str, Any]]:
    ancestry: list[dict[str, Any]] = []
    current = pid
    seen: set[int] = set()
    for _ in range(limit):
        if current <= 0 or current in seen:
            break
        seen.add(current)
        ppid_raw = _read_process_field(current, "ppid")
        try:
            ppid = int(ppid_raw) if ppid_raw else 0
        except ValueError:
            ppid = 0
        ancestry.append(
            {
                "pid": current,
                "ppid": ppid,
                "command": process_command(current),
                "start_token": process_start_token(current),
            }
        )
        if ppid <= 0 or ppid == current:
            break
        current = ppid
    return ancestry


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read_metadata(path: Path | None = None) -> dict[str, Any] | None:
    try:
        value = json.loads(metadata_path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _metadata_identity(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {
            "process_exists": None,
            "process_identity_valid": None,
            "observed_start_token": None,
        }
    try:
        pid = int(metadata["pid"])
    except (KeyError, TypeError, ValueError):
        return {
            "process_exists": False,
            "process_identity_valid": False,
            "observed_start_token": None,
        }
    try:
        os.kill(pid, 0)
        exists = True
    except ProcessLookupError:
        exists = False
    except PermissionError:
        exists = True
    observed = process_start_token(pid) if exists else None
    expected = metadata.get("process_start_token")
    identity_valid = None
    if exists and expected and observed:
        identity_valid = observed == expected
    elif not exists:
        identity_valid = False
    return {
        "process_exists": exists,
        "process_identity_valid": identity_valid,
        "observed_start_token": observed,
    }


def build_owner_metadata(
    *,
    app: str | None = None,
    transport: str | None = None,
    thread: str | None = None,
) -> dict[str, Any]:
    pid = os.getpid()
    return {
        "schema_version": 1,
        "pid": pid,
        "ppid": os.getppid(),
        "started_at": _utc_now(),
        "process_start_token": process_start_token(pid),
        "ancestry": process_ancestry(pid),
        "app": app or os.environ.get("TELEGRAM_MCP_CLIENT_APP") or "unknown",
        "cwd": os.getcwd(),
        "transport": transport or os.environ.get("MCP_TRANSPORT") or "stdio",
        "thread": thread or os.environ.get("TELEGRAM_MCP_THREAD_ID"),
        "command": shlex.join(sys.argv),
    }


@dataclass
class ConnectionLock:
    path: Path
    handle: Any

    @classmethod
    def try_acquire(
        cls,
        *,
        path: Path | None = None,
        owner: Mapping[str, Any] | None = None,
    ) -> "ConnectionLock | None":
        selected = path or lock_path()
        selected.parent.mkdir(parents=True, exist_ok=True)
        handle = selected.open("a+", encoding="utf-8")
        os.chmod(selected, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return None
            raise
        os.set_inheritable(handle.fileno(), True)
        if owner is not None:
            try:
                _atomic_write_json(metadata_path(selected), dict(owner))
            except Exception:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                raise
        return cls(selected, handle)

    def close(self) -> None:
        if self.handle.closed:
            return
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()

    def __enter__(self) -> "ConnectionLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _lock_is_busy_read_only(path: Path) -> bool:
    """Probe an existing lock without creating or changing filesystem state."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return True
            raise
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
    return False


def connection_status(path: Path | None = None) -> dict[str, Any]:
    selected = path or lock_path()
    busy = _lock_is_busy_read_only(selected)
    diagnostic = read_metadata(selected)
    if not busy:
        return {
            "status": "free",
            "busy": False,
            "lock_path": str(selected),
            "metadata_path": str(metadata_path(selected)),
            "stale_metadata": diagnostic is not None,
            "owner": diagnostic,
        }

    identity = _metadata_identity(diagnostic)
    owner = dict(diagnostic or {})
    owner.update(identity)
    return {
        "status": "busy",
        "busy": True,
        "lock_path": str(selected),
        "metadata_path": str(metadata_path(selected)),
        "stale_metadata": False,
        "owner": owner or None,
    }


def print_status(*, pretty: bool = False, stream: Any = sys.stdout) -> int:
    status = connection_status()
    json.dump(status, stream, ensure_ascii=False, indent=2 if pretty else None, sort_keys=True)
    stream.write("\n")
    return EXIT_BUSY if status["busy"] else 0


def exec_guarded(
    command: Sequence[str],
    *,
    app: str | None = None,
    transport: str | None = None,
    thread: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Acquire the one-machine lock and replace this process with ``command``.

    ``exec`` deliberately keeps the lock in the actual server process.  EOF,
    normal exit, signals, crashes, and an external initialize timeout all close
    the inherited descriptor in the kernel; no cleanup daemon or PID kill is
    needed.
    """
    if not command:
        raise ValueError("A server command is required")
    owner = build_owner_metadata(app=app, transport=transport, thread=thread)
    acquired = ConnectionLock.try_acquire(owner=owner)
    if acquired is None:
        status = connection_status()
        print(
            "Telegram connection is already owned by another live process. "
            "No process was stopped.",
            file=sys.stderr,
        )
        json.dump(status, sys.stderr, ensure_ascii=False, sort_keys=True)
        sys.stderr.write("\n")
        return EXIT_BUSY

    env = dict(os.environ if environ is None else environ)
    if transport:
        env["MCP_TRANSPORT"] = transport
    try:
        os.execvpe(command[0], list(command), env)
    except Exception:
        acquired.close()
        raise
