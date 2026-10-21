"""Tools that return json.dumps() output must not turn Cyrillic and emoji into \\uXXXX."""

import pytest
from telethon import types

from telegram_mcp.tools import profile

ME = types.User(
    id=1, is_self=True, first_name="Алексей", last_name="Иванов", username="alexey", access_hash=1
)


@pytest.mark.asyncio
async def test_get_me_returns_readable_names(monkeypatch):
    class Client:
        async def get_me(self):
            return ME

    async def connected(cl=None):
        return None

    monkeypatch.setattr(profile, "get_client", lambda account=None: Client())
    monkeypatch.setattr(profile, "ensure_connected", connected)

    result = await profile.get_me()

    assert "Алексей Иванов" in result
    assert "\\u" not in result


def test_every_json_dumps_call_keeps_non_ascii_text():
    # Guard for the whole package, so a new tool cannot bring the escapes back.
    import ast
    from pathlib import Path

    package = Path(profile.__file__).resolve().parents[1]
    missing = []
    for source_path in package.rglob("*.py"):
        for node in ast.walk(ast.parse(source_path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "dumps"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
                and not any(keyword.arg == "ensure_ascii" for keyword in node.keywords)
            ):
                missing.append(f"{source_path.relative_to(package)}:{node.lineno}")

    assert missing == []
