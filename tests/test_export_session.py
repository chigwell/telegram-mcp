"""Tests for telegram_mcp/export/session.py: no server session, no server import.

Two failures found in live use are pinned here:

* ``telegram_mcp.runtime`` discovers accounts at import time and calls
  ``sys.exit(1)`` when none is configured. Export authorises its own device and
  deliberately has no server session, so importing runtime killed the process
  before login could start.
* The same import reads the API credentials at import time, which made
  ``--help`` and the offline ``render`` command crash without a .env.

Both reduce to one structural rule: nothing under ``telegram_mcp.export`` may
import ``telegram_mcp.runtime``. ``test_export_cli_does_not_import_runtime``
enforces it in a subprocess, because once runtime is in ``sys.modules`` from
another test the in-process check would pass for the wrong reason.
"""

import os
import subprocess
import sys
import textwrap

import pytest

from telegram_mcp import client_factory
from telegram_mcp.export import session as export_session

SESSION_ENV = (
    "TELEGRAM_SESSION_STRING",
    "TELEGRAM_SESSION_STRINGS",
    "TELEGRAM_SESSION_STRING_PERSONAL",
    "TELEGRAM_SESSION_NAME",
)


class _FakeTelegramClient:
    def __init__(self, session, api_id, api_hash, **kwargs):
        self.session = session
        self.api_id = api_id
        self.kwargs = kwargs
        self.flood_sleep_threshold = 60


@pytest.fixture
def no_server_session(monkeypatch, tmp_path):
    for name in SESSION_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("TELEGRAM_PROXY_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "dummy_hash")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(client_factory, "TelegramClient", _FakeTelegramClient)
    return tmp_path


def test_build_client_without_any_server_session(no_server_session):
    """The reported failure: 'No Telegram session configured', exit code 1."""
    client = export_session.build_client()
    assert client.api_id == 12345


def test_build_client_drops_server_session_strings(no_server_session, monkeypatch):
    """Sharing one auth key with the server is what gets it revoked."""
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "server-session")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING_PERSONAL", "server-session-2")

    export_session.build_client()

    assert "TELEGRAM_SESSION_STRING" not in os.environ
    assert "TELEGRAM_SESSION_STRING_PERSONAL" not in os.environ


def test_build_client_uses_its_own_session_file(no_server_session):
    client = export_session.build_client()
    assert str(client.session).endswith("telegram-mcp/export")


def test_build_client_honours_proxy_configuration(no_server_session, monkeypatch):
    monkeypatch.setenv("TELEGRAM_PROXY_TYPE", "mtproxy")
    monkeypatch.setenv("TELEGRAM_PROXY_HOST", "mtproxy.example")
    monkeypatch.setenv("TELEGRAM_PROXY_PORT", "443")
    monkeypatch.setenv("TELEGRAM_PROXY_SECRET", "ee0123456789abcdef")

    client = export_session.build_client()

    assert client.kwargs["proxy"] == ("mtproxy.example", 443, "ee0123456789abcdef")


def test_missing_credentials_are_reported_not_raised_as_typeerror(no_server_session, monkeypatch):
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    with pytest.raises(SystemExit, match="TELEGRAM_API_ID"):
        export_session.build_client()


def test_export_cli_does_not_import_runtime(tmp_path):
    """Structural guard for both crashes: no runtime anywhere under export."""
    script = textwrap.dedent("""
        import sys
        import telegram_mcp.export.cli  # noqa: F401
        assert "telegram_mcp.runtime" not in sys.modules, "export imported the MCP runtime"
        print("clean")
        """)
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, cwd=os.getcwd()
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_export_help_works_without_any_configuration():
    """--help must not need credentials or a session."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_")}
    env["PYTHONPATH"] = os.getcwd()
    result = subprocess.run(
        [sys.executable, "-m", "telegram_mcp.export.cli", "export", "--help"],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.getcwd(),
    )
    assert result.returncode == 0, result.stderr
    assert "--transcribe" in result.stdout
