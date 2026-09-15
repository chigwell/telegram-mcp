"""Extracted helpers must not accidentally initialize a Telegram session."""

import os
import subprocess
import sys

import pytest


def run_isolated(code):
    env = {key: value for key, value in os.environ.items() if not key.startswith("TELEGRAM_")}
    env["PYTHON_DOTENV_DISABLED"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr


def test_helpers_import_without_runtime_or_credentials():
    run_isolated(
        "import importlib, sys\n"
        "for name in ('serialization', 'entity_formatting', 'rich_text', 'alias_matching', "
        "'alias_store', 'path_helpers', 'inline_buttons', 'folder_filters', 'forum_requests', "
        "'message_rendering'):\n"
        "    importlib.import_module('telegram_mcp.' + name)\n"
        "assert 'telegram_mcp.runtime' not in sys.modules\n"
    )


@pytest.mark.parametrize("module_name", ["main", "telegram_mcp.runner"])
def test_installation_guard_runs_before_runtime_initialization(module_name):
    run_isolated(
        "import importlib, sys\n"
        "from telegram_mcp import install_guard\n"
        "def reject():\n"
        "    raise install_guard.UnsafeInstallationError('rejected test installation')\n"
        "install_guard.assert_safe_distribution = reject\n"
        "try:\n"
        f"    importlib.import_module({module_name!r})\n"
        "except SystemExit as error:\n"
        "    assert str(error) == 'rejected test installation'\n"
        "else:\n"
        "    raise AssertionError('installation guard was skipped')\n"
        "assert 'telegram_mcp.runtime' not in sys.modules\n"
    )
