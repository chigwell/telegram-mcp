import json
import os
import plistlib
import subprocess
from pathlib import Path

from telegram_mcp import launcher


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shell_launcher_status_works_from_tmp_without_creating_lock(tmp_path):
    lock_path = tmp_path / "state" / "telegram.lock"
    env = os.environ.copy()
    env.update(
        {
            "TELEGRAM_MCP_REPO": str(PROJECT_ROOT),
            "TELEGRAM_MCP_LOCK": str(lock_path),
            "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"),
        }
    )

    result = subprocess.run(
        ["bash", str(PROJECT_ROOT / "scripts" / "telegram-mcp"), "--status"],
        cwd="/tmp",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "free"
    assert not lock_path.exists()
    assert not lock_path.parent.exists()


def test_broker_cli_sets_explicit_broker_mode(monkeypatch):
    captured = {}

    def fake_exec(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return 0

    monkeypatch.delenv("MCP_BROKER_MODE", raising=False)
    monkeypatch.setattr(launcher, "exec_guarded", fake_exec)

    assert launcher.main(["--broker", "--client-app", "test"]) == 0
    assert captured["transport"] == "http"
    assert captured["environ"]["MCP_BROKER_MODE"] == "1"


def test_legacy_launcher_does_not_enable_broker_mode(monkeypatch):
    captured = {}

    def fake_exec(_command, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.delenv("MCP_BROKER_MODE", raising=False)
    monkeypatch.setattr(launcher, "exec_guarded", fake_exec)

    assert launcher.main([]) == 0
    assert "MCP_BROKER_MODE" not in captured["environ"]


def test_launch_agent_is_single_attempt_not_restart_loop():
    path = PROJECT_ROOT / "contrib" / "ru.ontico.telegram-mcp-broker.plist"
    with path.open("rb") as handle:
        config = plistlib.load(handle)

    assert config["RunAtLoad"] is True
    assert "KeepAlive" not in config
    assert config["ExitTimeOut"] == 20
