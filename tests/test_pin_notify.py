import pytest

from telegram_mcp.tools import messages


class _PinClient:
    def __init__(self):
        self.calls = []

    async def pin_message(self, entity, message_id, **kwargs):
        self.calls.append((entity, message_id, kwargs))


@pytest.fixture
def client(monkeypatch):
    cl = _PinClient()

    async def fake_resolve(chat_id, client=None):
        return "entity"

    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    return cl


@pytest.mark.asyncio
async def test_pin_message_is_silent_by_default(client):
    result = await messages.pin_message(chat_id=1, message_id=2)

    assert result == "Message 2 pinned in chat 1."
    assert client.calls == [("entity", 2, {"notify": False})]


@pytest.mark.asyncio
async def test_pin_message_notifies_when_requested(client):
    result = await messages.pin_message(chat_id=1, message_id=2, notify=True)

    assert result == "Message 2 pinned in chat 1."
    assert client.calls == [("entity", 2, {"notify": True})]


def test_pin_message_schema_exposes_optional_notify():
    tool = messages.mcp._tool_manager.get_tool("pin_message")
    notify = tool.parameters["properties"]["notify"]

    assert notify["default"] is False
    assert "notify" not in tool.parameters.get("required", [])
