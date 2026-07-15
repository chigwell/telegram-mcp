import hashlib
import os

import pytest

from telegram_mcp import runner, runtime


# --- _parse_session_pool -----------------------------------------------------


def test_parse_session_pool_splits_and_dedupes(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SESSION_STRINGS", "s1, s2 ;s3\n s1  s2")
    assert runtime._parse_session_pool() == ["s1", "s2", "s3"]


def test_parse_session_pool_empty_when_unset(monkeypatch):
    monkeypatch.delenv("TELEGRAM_SESSION_STRINGS", raising=False)
    assert runtime._parse_session_pool() == []


# --- _acquire_session --------------------------------------------------------


@pytest.fixture
def isolated_lock_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(runtime, "_SESSION_LOCKS", [])
    return tmp_path


def _lock_slot(lock_dir, session):
    """Simulate another live client holding the slot for ``session``."""
    import fcntl

    digest = hashlib.sha1(session.encode("utf-8")).hexdigest()[:16]
    path = os.path.join(str(lock_dir), "telegram-mcp-session-locks", f"session-{digest}.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "w")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fh


def test_acquire_session_claims_first_free_slot(isolated_lock_dir):
    assert runtime._acquire_session(["AAA", "BBB", "CCC"]) == "AAA"


def test_acquire_session_skips_slot_locked_by_another_client(isolated_lock_dir):
    foreign = _lock_slot(isolated_lock_dir, "AAA")
    try:
        assert runtime._acquire_session(["AAA", "BBB", "CCC"]) == "BBB"
    finally:
        foreign.close()


def test_acquire_session_falls_back_to_first_when_pool_exhausted(isolated_lock_dir):
    held = [_lock_slot(isolated_lock_dir, s) for s in ("AAA", "BBB")]
    try:
        # Only two slots exist and both are taken -> reuse the first.
        assert runtime._acquire_session(["AAA", "BBB"]) == "AAA"
    finally:
        for fh in held:
            fh.close()


def test_acquire_session_without_fcntl_uses_first(isolated_lock_dir, monkeypatch):
    monkeypatch.setattr(runtime, "fcntl", None)
    assert runtime._acquire_session(["AAA", "BBB"]) == "AAA"


# --- _discover_accounts prefers the pool for the default account -------------


def test_discover_accounts_uses_pool_for_default(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SESSION_STRINGS", "pooled-1 pooled-2")
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)
    monkeypatch.delenv("TELEGRAM_SESSION_NAME", raising=False)
    monkeypatch.setattr(runtime, "_acquire_session", lambda pool: pool[0])
    monkeypatch.setattr(runtime, "StringSession", lambda value=None: f"str::{value}")
    captured = {}

    def _fake_build_client(session, label):
        captured[label] = session
        return object()

    monkeypatch.setattr(runtime, "_build_client", _fake_build_client)

    accounts = runtime._discover_accounts()

    assert "default" in accounts
    assert captured["default"] == "str::pooled-1"


# --- runner retries a transient AuthKeyDuplicatedError -----------------------


class _FlakyClient:
    def __init__(self, fail_times):
        self.fail_times = fail_times
        self.connects = 0
        self.disconnects = 0

    async def connect(self):
        self.connects += 1
        if self.connects <= self.fail_times:
            from telethon.errors import AuthKeyDuplicatedError

            raise AuthKeyDuplicatedError(request=None)

    async def disconnect(self):
        self.disconnects += 1

    async def is_user_authorized(self):
        return True


@pytest.fixture
def no_sleep(monkeypatch):
    async def _noop(_delay):
        return None

    monkeypatch.setattr(runner.asyncio, "sleep", _noop)


@pytest.mark.asyncio
async def test_connect_recovers_after_transient_authkey_duplicated(no_sleep):
    client = _FlakyClient(fail_times=2)

    await runner._connect_authorized_client("default", client)

    assert client.connects == 3
    assert client.disconnects == 2


@pytest.mark.asyncio
async def test_connect_reraises_authkey_duplicated_after_max_attempts(no_sleep):
    from telethon.errors import AuthKeyDuplicatedError

    client = _FlakyClient(fail_times=99)

    with pytest.raises(AuthKeyDuplicatedError):
        await runner._connect_authorized_client("default", client)

    assert client.connects == 4
