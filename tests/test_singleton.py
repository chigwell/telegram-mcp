from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from telegram_mcp.singleton import SessionLock, SessionLockError, _PID_OFFSET, session_identity


def test_holder_pid_nonexistent(tmp_path: Path):
    lock = SessionLock("default", "test-absent", lock_dir=tmp_path)
    assert lock.holder_pid() is None


def test_holder_pid_empty_or_short(tmp_path: Path):
    lock = SessionLock("default", "test-empty", lock_dir=tmp_path)
    lock.path.write_bytes(b"")
    assert lock.holder_pid() is None

    lock.path.write_bytes(b"123")  # less than _PID_OFFSET on Windows
    if _PID_OFFSET > 0:
        assert lock.holder_pid() is None
    else:
        assert lock.holder_pid() == 123


def test_holder_pid_corrupt_content(tmp_path: Path):
    lock = SessionLock("default", "test-corrupt", lock_dir=tmp_path)
    lock.path.write_bytes(b" " * _PID_OFFSET + b"not_a_number")
    assert lock.holder_pid() is None

    lock.path.write_bytes(b" " * _PID_OFFSET + b"-42")
    assert lock.holder_pid() is None


def test_exclusive_lock_records_and_reports_pid(tmp_path: Path):
    lock = SessionLock("default", "test-exclusive", lock_dir=tmp_path)
    lock.acquire()
    try:
        assert lock.holder_pid() == os.getpid()
    finally:
        lock.release()
    assert lock.holder_pid() is None


def test_shared_lock_clears_holder_pid(tmp_path: Path):
    lock = SessionLock("default", "test-shared", lock_dir=tmp_path)
    lock.path.write_bytes(b" " * _PID_OFFSET + b"99999\n")

    lock.acquire(shared=True)
    try:
        assert lock.holder_pid() is None
    finally:
        lock.release()


def test_multiprocess_collision_reports_child_pid(tmp_path: Path):
    script = f"""
import os, sys, time
from telegram_mcp.singleton import SessionLock
from pathlib import Path

lock = SessionLock("default", "mp-session", lock_dir=Path(r"{tmp_path}"))
lock.acquire()
print(f"ACQUIRED:{{os.getpid()}}", flush=True)
time.sleep(3)
lock.release()
"""
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("ACQUIRED:")
        child_pid = int(line.split(":")[1])

        # Contender fails to acquire and correctly names the child's PID
        contender = SessionLock("default", "mp-session", lock_dir=tmp_path)
        with pytest.raises(SessionLockError, match=f"held by PID {child_pid}"):
            contender.acquire(grace_seconds=0.2, poll_interval=0.05)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_session_identity_variants():
    class DummySession:
        def __init__(self, filename=None, save_val=None):
            self.filename = filename
            self.save_val = save_val

        def save(self):
            return self.save_val

    class DummyClient:
        def __init__(self, session=None):
            self.session = session

    c1 = DummyClient(DummySession(filename="foo.session"))
    assert session_identity(c1).startswith("file:")

    c2 = DummyClient(DummySession(save_val="auth_token_xyz"))
    assert session_identity(c2) == "string:auth_token_xyz"

    c3 = DummyClient(None)
    assert session_identity(c3).startswith("anon:")
