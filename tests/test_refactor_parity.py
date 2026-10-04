"""Frozen public contracts from remote main before the modular refactor.

Regenerate only for an explicitly approved API change, never to fix a failing
refactor test. Registration metadata is captured without calling Telegram.
"""

import ast
import copy
import hashlib
import inspect
from importlib.util import resolve_name
import json
import os
from pathlib import Path
import re
import socket
import sys

import pytest

import main
from telegram_mcp import runtime, tools

FIXTURE = Path(__file__).parent / "fixtures" / "refactor_contract.json"
PYTHON_FIXTURE = FIXTURE.with_name("refactor_contract_python_versions.json")
BASELINE_COMMIT = "4b77f38262b761c04b25cae8c992f26ecafb9274"
PRODUCTION_COMMIT = "e6dd352f286f71464fc19946a8d601bff51d8fd1"
BASELINE_FIXTURE_SHA256 = "f39938674775709fd3310b11883ddb3c0c5c468043ceac3a8486a11f68c699e9"


def contract_for_current_python():
    """Select exact pristine captures; never normalize descriptions or signatures."""
    fixture_bytes = FIXTURE.read_bytes()
    baseline = json.loads(fixture_bytes)
    versions = json.loads(PYTHON_FIXTURE.read_text(encoding="utf-8"))
    assert versions["format_version"] == 1
    assert versions["baseline_commit"] == BASELINE_COMMIT
    assert versions["production_commit"] == PRODUCTION_COMMIT
    assert (
        versions["canonical_fixture_sha256"]
        == hashlib.sha256(fixture_bytes).hexdigest()
        == BASELINE_FIXTURE_SHA256
    )
    assert (
        versions["uv_lock_sha256"]
        == hashlib.sha256(
            (Path(__file__).resolve().parents[1] / "uv.lock").read_bytes()
        ).hexdigest()
    )
    canonical_capture = versions["canonical_capture"]
    assert canonical_capture["python_version"].startswith("3.13.")
    assert canonical_capture["implementation"] == "CPython"
    tool_names = {tool["name"] for tool in baseline["tools"]}
    assert len(tool_names) == len(baseline["tools"]) == 132
    profiles = versions["profiles"]
    assert set(profiles) == {"3.10", "3.11", "3.12"}
    for minor, profile in profiles.items():
        assert profile["python_version"].startswith(f"{minor}.")
        assert profile["implementation"] == "CPython"
        assert profile["dependency_versions"] == canonical_capture["dependency_versions"]
        descriptions = profile["tool_description_sha256"]
        assert descriptions.keys() == tool_names, minor
        assert all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in descriptions.values())
        for module_name, overrides in profile["export_overrides"].items():
            assert module_name in baseline["exports"], module_name
            for name, entry in overrides.items():
                assert name in baseline["exports"][module_name], f"{module_name}.{name}"
                # An entire pristine entry also preserves absent signatures,
                # e.g. typing.Any on Python 3.10. Never add new exports here.
                assert "kind" in entry and entry.keys() <= {"kind", "signature"}
                assert all(isinstance(value, str) for value in entry.values())
                assert entry != baseline["exports"][module_name][name]
    assert sys.implementation.name == "cpython", "Only CPython pristine captures are available"
    minor = f"{sys.version_info.major}.{sys.version_info.minor}"
    if minor == "3.13":
        return baseline, None
    assert minor in profiles, f"Capture the pristine baseline contract for Python {minor} first"
    return baseline, profiles[minor]


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
    baseline, profile = contract_for_current_python()
    actual = await tool_catalogue()
    expected = copy.deepcopy(baseline["tools"])
    if profile is not None:
        assert [tool["name"] for tool in actual] == [tool["name"] for tool in expected]
        for tool in actual:
            name = tool["name"]
            assert (
                hashlib.sha256(tool["description"].encode("utf-8")).hexdigest()
                == profile["tool_description_sha256"][name]
            ), f"{name}: raw description differs from the pristine Python capture"
            del tool["description"]
        for tool in expected:
            del tool["description"]
    assert actual == expected
    assert exposure_sets() == baseline["exposure"]


def test_public_exports_and_signatures_are_unchanged():
    baseline, profile = contract_for_current_python()
    expected_exports = copy.deepcopy(baseline["exports"])
    if profile is not None:
        for module_name, overrides in profile["export_overrides"].items():
            expected_exports[module_name].update(overrides)
    actual = _IMPORTED_EXPORTS
    for module_name, expected in expected_exports.items():
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


def test_extracted_implementations_do_not_import_state_owning_facades():
    """Keep invocation-time dependency injection from becoming circular imports."""
    package = Path(runtime.__file__).parent
    core_modules = (
        "core_types",
        "serialization",
        "formatting",
        "validation",
        "error_formatting",
        "file_policy",
        "access_policy",
        "alias_store",
        "account_config",
        "connections",
        "mcp_policy",
        "startup",
    )
    paths = [*(package / f"{name}.py" for name in core_modules)]
    paths.extend(path for path in (package / "tools").glob("_*.py") if path.stem != "__init__")
    facades = {"main", "telegram_mcp.runtime", "telegram_mcp.runner", "telegram_mcp.tools"}
    facades.update(
        f"telegram_mcp.tools.{path.stem}"
        for path in (package / "tools").glob("*.py")
        if not path.stem.startswith("_")
    )
    for path in paths:
        package_name = "telegram_mcp.tools" if path.parent.name == "tools" else "telegram_mcp"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = resolve_name("." * node.level + module, package_name)
                imports.add(module)
                imports.update(f"{module}.{alias.name}" for alias in node.names)
        assert not imports & facades, f"{path.name}: {sorted(imports & facades)}"


def test_internal_imports_are_explicit():
    package = Path(runtime.__file__).parent
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ), path.relative_to(package)


@pytest.mark.parametrize("method", ["connect", "connect_ex"])
def test_offline_bootstrap_rejects_network_connections(method, network_guard_probe):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        with pytest.raises(AssertionError, match="Unexpected network connection"):
            getattr(connection, method)(("127.0.0.1", 9))
    assert network_guard_probe == [method]


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
