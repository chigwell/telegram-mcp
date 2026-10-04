"""Frozen public contracts from remote main before the modular refactor.

Regenerate only for an explicitly approved API change, never to fix a failing
refactor test. Registration metadata is captured without calling Telegram.
"""

import ast
import copy
import inspect
import json
import os
from pathlib import Path
import re
import socket

import pytest

import main
from telegram_mcp import runtime, tools

FIXTURE = Path(__file__).parent / "fixtures" / "refactor_contract.json"


def public_exports(module):
    result = {}
    declared = set(getattr(module, "__all__", ()))
    # runtime historically exports private helpers through __all__; main also
    # exposes facade wrappers through direct imports and monkeypatch seams.
    if module is main:
        declared.update(runtime.__all__)
        declared.update(
            name
            for name, value in vars(module).items()
            if inspect.isfunction(value) and value.__module__ in ("main", "telegram_mcp.runner")
        )
    for name, value in sorted(vars(module).items()):
        if name.startswith("_") and name not in declared:
            continue
        entry = {"kind": type(value).__name__}
        if inspect.isfunction(value) or inspect.isclass(value):
            try:
                entry["signature"] = re.sub(
                    r" at 0x[0-9a-fA-F]+(?=>)", "", str(inspect.signature(value))
                )
            except (TypeError, ValueError):
                pass
        result[name] = entry
    return result


async def tool_catalogue():
    return [
        tool.model_dump(mode="json", exclude_none=True)
        for tool in sorted(await runtime.mcp.list_tools(), key=lambda tool: tool.name)
    ]


def exposure_sets():
    result = {}
    original = {tool.name for tool in runtime.mcp._tool_manager.list_tools()}
    for mode in ("all", "read-only", "read-only+send_file,send_message"):
        # Exposure prunes its manager. Copy both layers to leave the process's
        # singleton and registered callables intact for all other tests.
        server = copy.copy(runtime.mcp)
        server._tool_manager = copy.copy(runtime.mcp._tool_manager)
        server._tool_manager._tools = dict(runtime.mcp._tool_manager._tools)
        hidden = runtime._apply_exposed_tools_mode(server, mode)
        result[mode] = {
            "exposed": sorted(tool.name for tool in server._tool_manager.list_tools()),
            "hidden": sorted(hidden),
        }
    assert {tool.name for tool in runtime.mcp._tool_manager.list_tools()} == original
    return result


def export_catalogue():
    return {
        "main": public_exports(main),
        "telegram_mcp.runtime": public_exports(runtime),
        "telegram_mcp.tools": public_exports(tools),
    }


# Capture at collection, before CLI/security tests legitimately mutate runtime
# settings from None to configured values. The contract is the imported surface.
_IMPORTED_EXPORTS = export_catalogue()
_DECLARED_EXPORTS = {
    "telegram_mcp.runtime": sorted(runtime.__all__),
    "telegram_mcp.tools": sorted(tools.__all__),
}


@pytest.mark.asyncio
async def test_registered_mcp_contract_is_unchanged():
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert await tool_catalogue() == baseline["tools"]
    assert exposure_sets() == baseline["exposure"]


def test_public_exports_and_signatures_are_unchanged():
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = _IMPORTED_EXPORTS
    for module_name, expected in baseline["exports"].items():
        entries = actual[module_name]
        assert entries.keys() == expected.keys(), module_name
        for name, contract in expected.items():
            assert entries[name] == contract, f"{module_name}.{name}"


def test_declared_wildcard_exports_are_unchanged():
    baseline = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert _DECLARED_EXPORTS == baseline["declared_exports"]


@pytest.mark.parametrize(
    "name",
    [
        "ValidationError",
        "ChatAccessDeniedError",
        "ErrorCategory",
        "AliasStoreUnreadable",
        "AliasID",
        "AliasNeedsUser",
    ],
)
def test_facade_reexports_canonical_exception_and_enum_identity(name):
    assert getattr(main, name) is getattr(runtime, name)


def test_no_shadowed_test_functions():
    """A repeated name silently hides an earlier test from pytest collection."""
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        scopes = [module, *(node for node in ast.walk(module) if isinstance(node, ast.ClassDef))]
        for scope in scopes:
            seen = set()
            for node in scope.body:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and node.name.startswith("test_"):
                    assert node.name not in seen, f"{path.name}:{node.lineno}: {node.name}"
                    seen.add(node.name)


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_offline_bootstrap_rejects_network_connections(method):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        with pytest.raises(AssertionError, match="Unexpected network connection"):
            getattr(connection, method)(("127.0.0.1", 9))


def test_bootstrap_uses_dummy_credentials_and_isolated_state(tmp_path):
    assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
    assert runtime.TELEGRAM_API_ID == 12345
    assert runtime.TELEGRAM_API_HASH == "dummy_hash"
    assert os.environ["TELEGRAM_SESSION_NAME"] == ":memory:"
    for variable in (
        "XDG_STATE_HOME",
        "TELEGRAM_ALIASES_FILE",
        "TELEGRAM_TRANSCRIPT_CACHE_DIR",
        "TELEGRAM_EVENT_FEED_FILE",
    ):
        assert Path(os.environ[variable]).is_relative_to(tmp_path)
    assert Path(runtime.file_handler.baseFilename).parent != Path(runtime.PROJECT_ROOT)
