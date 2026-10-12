import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from telegram_mcp import connection_guard


def _env_for(path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["TELEGRAM_MCP_LOCK"] = str(path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _start_holder(path: Path, *, app: str = "test-holder") -> subprocess.Popen:
    script = """
import os
import time
from telegram_mcp.connection_guard import ConnectionLock, build_owner_metadata
lock = ConnectionLock.try_acquire(owner=build_owner_metadata(app=os.environ['HOLDER_APP']))
assert lock is not None
print('ready', flush=True)
time.sleep(60)
"""
    env = _env_for(path)
    env["HOLDER_APP"] = app
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)


def test_free_status_ignores_stale_lock_and_metadata(tmp_path, monkeypatch):
    path = tmp_path / "telegram.lock"
    path.write_text("stale lock file", encoding="utf-8")
    connection_guard._atomic_write_json(
        connection_guard.metadata_path(path),
        {"pid": 999999, "process_start_token": "old", "app": "old-client"},
    )
    monkeypatch.setenv("TELEGRAM_MCP_LOCK", str(path))

    status = connection_guard.connection_status()

    assert status["status"] == "free"
    assert status["busy"] is False
    assert status["stale_metadata"] is True
    assert status["owner"]["app"] == "old-client"


def test_free_status_has_no_filesystem_side_effects(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "telegram.lock"
    monkeypatch.setenv("TELEGRAM_MCP_LOCK", str(path))

    status = connection_guard.connection_status()

    assert status["status"] == "free"
    assert not path.exists()
    assert not connection_guard.metadata_path(path).exists()
    assert not path.parent.exists()


def test_free_status_does_not_chmod_existing_unlocked_file(tmp_path):
    path = tmp_path / "telegram.lock"
    path.write_text("", encoding="utf-8")
    path.chmod(0o640)
    mode_before = path.stat().st_mode & 0o777

    status = connection_guard.connection_status(path)

    assert status["status"] == "free"
    assert path.stat().st_mode & 0o777 == mode_before
    assert not connection_guard.metadata_path(path).exists()


def test_busy_status_reports_exact_owner_and_exit_75(tmp_path):
    path = tmp_path / "telegram.lock"
    holder = _start_holder(path, app="codex-test")
    try:
        status = connection_guard.connection_status(path)
        assert status["status"] == "busy"
        assert status["owner"]["pid"] == holder.pid
        assert status["owner"]["app"] == "codex-test"
        assert status["owner"]["process_exists"] is True
        # Sandboxed macOS test runners may deny `ps`; production status then
        # reports identity unknown rather than claiming a match.
        assert status["owner"]["process_identity_valid"] in (True, None)

        result = subprocess.run(
            [sys.executable, "-m", "telegram_mcp.launcher", "--status"],
            cwd=Path(__file__).resolve().parents[1],
            env=_env_for(path),
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == connection_guard.EXIT_BUSY
        assert json.loads(result.stdout)["owner"]["pid"] == holder.pid
        assert holder.poll() is None  # status is read-only; it never auto-kills
    finally:
        _stop(holder)


def test_pid_reuse_metadata_is_flagged_without_overriding_flock(tmp_path, monkeypatch):
    path = tmp_path / "telegram.lock"
    holder = _start_holder(path)
    try:
        metadata = connection_guard.read_metadata(path)
        assert metadata is not None
        metadata["process_start_token"] = "different process incarnation"
        connection_guard._atomic_write_json(connection_guard.metadata_path(path), metadata)
        monkeypatch.setattr(connection_guard, "process_start_token", lambda _pid: "observed")

        status = connection_guard.connection_status(path)

        assert status["busy"] is True
        assert status["owner"]["process_exists"] is True
        assert status["owner"]["process_identity_valid"] is False
    finally:
        _stop(holder)


def test_process_exit_releases_lock(tmp_path):
    path = tmp_path / "telegram.lock"
    holder = _start_holder(path)
    assert connection_guard.connection_status(path)["busy"] is True

    _stop(holder)

    assert connection_guard.connection_status(path)["status"] == "free"


def test_stdio_eof_releases_lock(tmp_path):
    path = tmp_path / "telegram.lock"
    script = """
import sys
from telegram_mcp.connection_guard import exec_guarded
raise SystemExit(exec_guarded(
    [sys.executable, '-c', 'import sys; sys.stdin.read()'],
    app='stdio-eof-test',
    transport='stdio',
))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=_env_for(path),
        stdin=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if connection_guard.connection_status(path)["busy"]:
                break
            time.sleep(0.02)
        else:
            pytest.fail("guarded stdio process did not acquire the lock")

        assert process.stdin is not None
        process.stdin.close()
        process.wait(timeout=5)
        assert connection_guard.connection_status(path)["status"] == "free"
    finally:
        _stop(process)


def test_exec_lifecycle_releases_lock_after_external_initialize_timeout(tmp_path):
    path = tmp_path / "telegram.lock"
    script = """
import sys
from telegram_mcp.connection_guard import exec_guarded
raise SystemExit(exec_guarded(
    [sys.executable, '-c', 'import time; time.sleep(60)'],
    app='initialize-timeout-test',
    transport='stdio',
))
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=_env_for(path),
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if connection_guard.connection_status(path)["busy"]:
                break
            time.sleep(0.02)
        else:
            pytest.fail("guarded process did not acquire the lock")

        process.terminate()  # what an MCP host does after initialize timeout
        process.wait(timeout=5)
        assert connection_guard.connection_status(path)["status"] == "free"
    finally:
        _stop(process)


def test_free_guarded_command_starts_and_finishes(tmp_path):
    path = tmp_path / "telegram.lock"
    script = """
import sys
from telegram_mcp.connection_guard import exec_guarded
raise SystemExit(exec_guarded([sys.executable, '-c', 'pass'], app='free-start'))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=_env_for(path),
        check=False,
    )
    assert result.returncode == 0
    assert connection_guard.connection_status(path)["status"] == "free"


def test_second_guarded_command_refuses_busy_without_killing_owner(tmp_path):
    path = tmp_path / "telegram.lock"
    holder = _start_holder(path)
    try:
        script = """
import sys
from telegram_mcp.connection_guard import exec_guarded
raise SystemExit(exec_guarded([sys.executable, '-c', 'pass'], app='second'))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1],
            env=_env_for(path),
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == connection_guard.EXIT_BUSY
        assert "No process was stopped" in result.stderr
        assert holder.poll() is None
    finally:
        _stop(holder)
