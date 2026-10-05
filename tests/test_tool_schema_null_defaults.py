"""Every tool argument that defaults to None must also accept null.

`account: str = None` makes the schema advertise `"default": null` on a
field typed `"type": "string"`, so a client that sends the advertised
default back (some agent frameworks fill in every optional argument) fails
validation before the tool runs.
"""

import pytest

import main  # noqa: F401  (registers every tool)
from telegram_mcp import runtime


@pytest.mark.asyncio
async def test_null_default_arguments_accept_null():
    rejecting = []
    for tool in await runtime.mcp.list_tools():
        for name, prop in tool.inputSchema.get("properties", {}).items():
            if "default" not in prop or prop["default"] is not None:
                continue
            types = [prop.get("type")] + [s.get("type") for s in prop.get("anyOf", [])]
            if "null" not in types:
                rejecting.append(f"{tool.name}.{name}")
    assert rejecting == []
