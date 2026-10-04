"""Offline test bootstrap, installed before application modules are collected."""

import logging
import os
from pathlib import Path
import socket
import tempfile

import pytest

# Do this before imports: runtime discovers accounts and constructs clients at
# import time. Never inherit the developer's sessions, policies or provider keys.
for key in list(os.environ):
    if key.startswith(("TELEGRAM_", "MCP_")) or key in ("GROQ_API_KEY", "OPENAI_API_KEY"):
        del os.environ[key]

_state = tempfile.TemporaryDirectory(prefix="telegram-mcp-tests-")
_state_path = Path(_state.name)
(_state_path / "tmp").mkdir()
_original_tempdir = tempfile.tempdir
tempfile.tempdir = str(_state_path / "tmp")
os.environ.update(
    PYTHON_DOTENV_DISABLED="1",
    TELEGRAM_API_ID="12345",
    TELEGRAM_API_HASH="dummy_hash",
    TELEGRAM_SESSION_NAME=":memory:",
    XDG_STATE_HOME=str(_state_path / "state"),
    TELEGRAM_ALIASES_FILE=str(_state_path / "aliases.json"),
    TELEGRAM_TRANSCRIPT_CACHE_DIR=str(_state_path / "transcripts"),
    TELEGRAM_EVENT_FEED_FILE=str(_state_path / "feed.jsonl"),
)

_bootstrap_patch = pytest.MonkeyPatch()
_socket_connect = socket.socket.connect
_socket_connect_ex = socket.socket.connect_ex


def _offline_connect(self, address):
    if self.family == getattr(socket, "AF_UNIX", None):
        return _socket_connect(self, address)
    raise AssertionError("Unexpected network connection in offline tests")


def _offline_connect_ex(self, address):
    if self.family == getattr(socket, "AF_UNIX", None):
        return _socket_connect_ex(self, address)
    raise AssertionError("Unexpected network connection in offline tests")


_bootstrap_patch.setattr(socket.socket, "connect", _offline_connect)
_bootstrap_patch.setattr(socket.socket, "connect_ex", _offline_connect_ex)

# Runtime's legacy log path is fixed next to main.py. Redirect only this
# handler during collection, keeping its real type/format/encoding intact.
_file_handler_init = logging.FileHandler.__init__
_repo_log = Path(__file__).resolve().parents[1] / "mcp_errors.log"


def _isolated_file_handler(self, filename, *args, **kwargs):
    if Path(filename).resolve() == _repo_log:
        filename = _state_path / "mcp_errors.log"
    _file_handler_init(self, filename, *args, **kwargs)


_bootstrap_patch.setattr(logging.FileHandler, "__init__", _isolated_file_handler)


def pytest_unconfigure(config):
    for handler in list(logging.getLogger("telegram_mcp").handlers):
        handler.close()
        logging.getLogger("telegram_mcp").removeHandler(handler)
    _bootstrap_patch.undo()
    tempfile.tempdir = _original_tempdir
    _state.cleanup()


@pytest.fixture(autouse=True)
def _isolated_persistent_state(tmp_path, monkeypatch):
    """Each test gets separate state without changing production defaults."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(tmp_path / "aliases.json"))
    monkeypatch.setenv("TELEGRAM_TRANSCRIPT_CACHE_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setenv("TELEGRAM_EVENT_FEED_FILE", str(tmp_path / "feed.jsonl"))


@pytest.fixture
def transcript_cache_dir(tmp_path, monkeypatch):
    """Isolated, per-test SQLite transcript cache directory."""
    d = tmp_path / "transcripts"
    monkeypatch.setenv("TELEGRAM_TRANSCRIPT_CACHE_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def _clear_expected_username(monkeypatch):
    """Keep a developer's TELEGRAM_EXPECTED_USERNAME* (.env) away from fake clients."""
    for key in list(os.environ):
        if key.startswith("TELEGRAM_EXPECTED_USERNAME"):
            monkeypatch.delenv(key, raising=False)
