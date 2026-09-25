from types import SimpleNamespace

import pytest

import migrate_session


def test_env_names_support_default_and_labeled_accounts():
    assert migrate_session._env_names(None) == (
        "TELEGRAM_SESSION_STRING",
        "TELEGRAM_SESSION_NAME",
        "telegram_mcp_session",
    )
    assert migrate_session._env_names("Work-2") == (
        "TELEGRAM_SESSION_STRING_WORK_2",
        "TELEGRAM_SESSION_NAME_WORK_2",
        "telegram_mcp_session_work_2",
    )


def test_env_names_reject_invalid_account_label():
    with pytest.raises(ValueError, match="account label"):
        migrate_session._env_names("work account")


def test_target_paths_accepts_optional_session_suffix(tmp_path):
    without_suffix = tmp_path / "telegram"
    with_suffix = tmp_path / "telegram.session"

    assert migrate_session._target_paths(str(without_suffix)) == (
        str(without_suffix),
        with_suffix.resolve(),
    )
    assert migrate_session._target_paths(str(with_suffix)) == (
        str(without_suffix),
        with_suffix.resolve(),
    )


@pytest.mark.asyncio
async def test_migrate_copies_auth_key_verifies_and_hardens_file(tmp_path, monkeypatch):
    target = tmp_path / "telegram"
    session_file = tmp_path / "telegram.session"
    source = SimpleNamespace(
        dc_id=2,
        server_address="149.154.167.40",
        port=443,
        auth_key=object(),
    )
    saved = {}

    class FakeSQLiteSession:
        def __init__(self, name):
            saved["name"] = name
            session_file.touch()

        def set_dc(self, dc_id, address, port):
            saved["dc"] = (dc_id, address, port)

        def save(self):
            saved["saved"] = True

        def close(self):
            saved["closed"] = True

        @property
        def auth_key(self):
            return saved.get("auth_key")

        @auth_key.setter
        def auth_key(self, value):
            saved["auth_key"] = value

    class FakeClient:
        def __init__(self, session, api_id, api_hash, **kwargs):
            saved["client"] = (session, api_id, api_hash, kwargs)

        async def connect(self):
            saved["connected"] = True

        async def is_user_authorized(self):
            return True

        async def get_me(self):
            return SimpleNamespace(id=42, first_name="Ada")

        async def disconnect(self):
            saved["disconnected"] = True

    monkeypatch.setattr(migrate_session, "StringSession", lambda value: source)
    monkeypatch.setattr(migrate_session, "SQLiteSession", FakeSQLiteSession)
    monkeypatch.setattr(migrate_session, "TelegramClient", FakeClient)
    monkeypatch.setattr(
        migrate_session, "client_identity_kwargs", lambda: {"device_model": "test"}
    )

    created, user = await migrate_session.migrate("secret", 123, "hash", str(target))

    assert created == session_file.resolve()
    assert user.id == 42
    assert saved["dc"] == (2, "149.154.167.40", 443)
    assert saved["auth_key"] is source.auth_key
    assert saved["saved"] is True
    assert saved["client"][1:] == (123, "hash", {"device_model": "test"})
    assert saved["connected"] is True
    assert saved["disconnected"] is True
    assert session_file.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_migrate_refuses_to_overwrite_existing_session(tmp_path):
    session_file = tmp_path / "telegram.session"
    session_file.touch()

    with pytest.raises(FileExistsError, match="already exists"):
        await migrate_session.migrate("secret", 123, "hash", str(tmp_path / "telegram"))


@pytest.mark.asyncio
async def test_migrate_removes_new_file_when_verification_fails(tmp_path, monkeypatch):
    target = tmp_path / "telegram"
    session_file = tmp_path / "telegram.session"
    source = SimpleNamespace(dc_id=2, server_address="server", port=443, auth_key=object())

    class FakeSQLiteSession:
        def __init__(self, name):
            session_file.touch()

        def set_dc(self, *args):
            return None

        def save(self):
            return None

        def close(self):
            return None

        auth_key = None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            return None

        async def connect(self):
            return None

        async def is_user_authorized(self):
            return False

        async def disconnect(self):
            return None

    monkeypatch.setattr(migrate_session, "StringSession", lambda value: source)
    monkeypatch.setattr(migrate_session, "SQLiteSession", FakeSQLiteSession)
    monkeypatch.setattr(migrate_session, "TelegramClient", FakeClient)

    with pytest.raises(RuntimeError, match="not authorized"):
        await migrate_session.migrate("secret", 123, "hash", str(target))

    assert not session_file.exists()


@pytest.mark.asyncio
async def test_run_reports_missing_source_session(monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_API_ID", "123")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash")
    monkeypatch.delenv("TELEGRAM_SESSION_STRING", raising=False)

    result = await migrate_session._run(SimpleNamespace(account=None, target=None))

    assert result == 1
    assert "TELEGRAM_SESSION_STRING must be set" in capsys.readouterr().err
