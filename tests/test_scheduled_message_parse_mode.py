"""send_scheduled_message: explicit parse_mode, backward-compatible default."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from telegram_mcp.tools import messages


class _DummyClient:
    def __init__(self):
        self.calls = []

    async def send_message(self, entity, message, **kwargs):
        self.calls.append({"entity": entity, "message": message, **kwargs})
        result = MagicMock()
        result.id = 42
        return result


@pytest.fixture
def client(monkeypatch):
    cl = _DummyClient()

    async def fake_ensure(cl=None):
        pass

    async def fake_resolve(chat_id, cl=None):
        return "entity"

    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "ensure_connected", fake_ensure)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    return cl


def _future():
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


@pytest.mark.asyncio
async def test_default_keeps_client_parse_mode(client):
    # Backward compatibility: without parse_mode the kwarg is not passed at all,
    # so Telethon applies the client default (Markdown) exactly as before.
    result = await messages.send_scheduled_message(123, "**hi**", _future())

    assert result.startswith("Scheduled message 42 for ")
    assert "parse_mode" not in client.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plain", "PLAIN"])
async def test_plain_disables_parsing(client, mode):
    result = await messages.send_scheduled_message(123, "2 * 3 = 6", _future(), parse_mode=mode)

    assert result.startswith("Scheduled message 42 for ")
    assert "parse_mode" in client.calls[0]
    assert client.calls[0]["parse_mode"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["md", "markdown", "html"])
async def test_parse_mode_is_forwarded(client, mode):
    result = await messages.send_scheduled_message(123, "**hi**", _future(), parse_mode=mode)

    assert result.startswith("Scheduled message 42 for ")
    assert client.calls[0]["parse_mode"] == mode
    assert client.calls[0]["schedule"] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rich", "rich_md", "rich_markdown", "RICH_HTML"])
async def test_rich_modes_rejected(client, mode):
    result = await messages.send_scheduled_message(123, "# title", _future(), parse_mode=mode)

    assert "not supported for scheduled messages" in result
    assert client.calls == []
