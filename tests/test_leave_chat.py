"""leave_chat on a private chat must say why it cannot work."""

import pytest
from telethon import types

from telegram_mcp.tools import groups


@pytest.mark.asyncio
async def test_leave_chat_explains_that_a_private_chat_cannot_be_left(monkeypatch):
    friend = types.User(id=101, first_name="Ivan", access_hash=1)

    async def resolve(chat_id, cl=None):
        return friend

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(groups, "get_client", lambda account=None: object())
    monkeypatch.setattr(groups, "resolve_entity", resolve)

    result = await groups.leave_chat(chat_id=101)

    assert result == "Error: chat_id must be a group or channel, not a user."
