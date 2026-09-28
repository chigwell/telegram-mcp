import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import groups, messages  # noqa: F401  (registers the tools)

# These tools change state, so TELEGRAM_EXPOSED_TOOLS=read-only must drop them:
# export_unread_messages writes a JSON file to a caller-chosen path, and
# export_chat_invite / get_invite_link call messages.exportChatInvite, which
# creates a new invite link. They can still be exposed by name
# (read-only+export_chat_invite).
SIDE_EFFECT_TOOLS = ["export_unread_messages", "export_chat_invite", "get_invite_link"]


@pytest.mark.parametrize("tool_name", SIDE_EFFECT_TOOLS)
def test_side_effect_tools_are_not_read_only(tool_name):
    registered = {tool.name: tool for tool in runtime.mcp._tool_manager.list_tools()}
    assert registered[tool_name].annotations.readOnlyHint is False
