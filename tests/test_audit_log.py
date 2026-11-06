"""Audit storage tests use real files and independent writer processes."""

import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from telegram_mcp.audit_log import append_audit_record, audit_log_path


def _write_batch(path, batch):
    os.environ["TELEGRAM_AUDIT_LOG"] = path
    for index in range(20):
        append_audit_record({"batch": batch, "index": index, "reason": "я" * 8192})


def test_path_precedence(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_AUDIT_LOG", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert audit_log_path() == tmp_path / "state/telegram-mcp/audit_log.jsonl"
    assert audit_log_path(":memory:") == audit_log_path()
    assert (
        audit_log_path(tmp_path / "sessions/work.session") == tmp_path / "sessions/audit_log.jsonl"
    )
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(tmp_path / "custom.jsonl"))
    assert audit_log_path(tmp_path / "work.session") == tmp_path / "custom.jsonl"


def test_home_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_AUDIT_LOG", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert audit_log_path() == tmp_path / ".local/state/telegram-mcp/audit_log.jsonl"


def test_append_and_rotation(monkeypatch, tmp_path):
    path = tmp_path / "private/nested/audit_log.jsonl"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))
    records = [{"outcome": "deleted"}, {"reason": "line\nbreak — отказ"}]
    for record in records:
        append_audit_record(record)
    assert [json.loads(line) for line in path.read_text().splitlines()] == records
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.parent.parent.stat().st_mode & 0o777 == 0o700
    archived = path.with_suffix(".jsonl.1")
    path.rename(archived)
    append_audit_record({"outcome": "refused"})
    assert len(archived.read_text().splitlines()) == 2
    assert json.loads(path.read_text()) == {"outcome": "refused"}


def test_concurrent_processes(monkeypatch, tmp_path):
    path = tmp_path / "audit_log.jsonl"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))
    with ProcessPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(_write_batch, str(path), batch) for batch in range(3)]
        for future in futures:
            future.result(timeout=30)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 60
    assert {(r["batch"], r["index"]) for r in records} == {
        (batch, index) for batch in range(3) for index in range(20)
    }
    assert all(r["reason"] == "я" * 8192 for r in records)


def test_invalid_record_does_not_touch_file(monkeypatch, tmp_path):
    path = tmp_path / "audit_log.jsonl"
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))
    with pytest.raises(ValueError):
        append_audit_record({"count": float("nan")})
    assert not path.exists()


def test_io_failure_is_propagated(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(tmp_path))
    with pytest.raises(OSError):
        append_audit_record({"outcome": "deleted"})


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions and symlink protection")
def test_existing_file_is_private_and_symlinks_are_refused(monkeypatch, tmp_path):
    path = tmp_path / "audit_log.jsonl"
    path.write_text("")
    path.chmod(0o644)
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(path))
    append_audit_record({"outcome": "deleted"})
    assert path.stat().st_mode & 0o777 == 0o600
    link = tmp_path / "link.jsonl"
    link.symlink_to(path)
    monkeypatch.setenv("TELEGRAM_AUDIT_LOG", str(link))
    with pytest.raises(OSError):
        append_audit_record({"outcome": "refused"})
    assert len(path.read_text().splitlines()) == 1
