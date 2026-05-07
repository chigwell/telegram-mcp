"""Phase 5–6 tests — channels/publish, channels/list, channels/open,
channels/close, channels/typing handlers."""

import asyncio

import pytest

from telegram_mcp.mcpl.handlers import (
    extract_text,
    make_close_handler,
    make_list_handler,
    make_open_handler,
    make_publish_handler,
    make_typing_handler,
    parse_channel_id,
)


# ---------------------------------------------------------------------------
# parse_channel_id
# ---------------------------------------------------------------------------


def test_parse_channel_id_dm():
    assert parse_channel_id("telegram:default:dm:42") == ("default", "dm", 42)


def test_parse_channel_id_group_supergroup_channel():
    assert parse_channel_id("telegram:work:group:1001") == ("work", "group", 1001)
    assert parse_channel_id("telegram:work:supergroup:2001") == (
        "work",
        "supergroup",
        2001,
    )
    assert parse_channel_id("telegram:work:channel:3001") == ("work", "channel", 3001)


def test_parse_channel_id_saved():
    assert parse_channel_id("telegram:default:saved") == ("default", "saved", None)


def test_parse_channel_id_negative_chat_id():
    # Telegram channels' marked IDs are negative; must round-trip
    assert parse_channel_id("telegram:default:supergroup:-1001234567890") == (
        "default",
        "supergroup",
        -1001234567890,
    )


def test_parse_channel_id_malformed():
    with pytest.raises(ValueError):
        parse_channel_id("not-a-telegram-channel")
    with pytest.raises(ValueError):
        parse_channel_id("telegram:default:unknown:42")
    with pytest.raises(ValueError):
        parse_channel_id("telegram:default:dm:notanumber")


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------


def test_extract_text_concatenates_blocks():
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "world"},
    ]
    assert extract_text(blocks) == "hello\nworld"


def test_extract_text_drops_non_text():
    blocks = [
        {"type": "text", "text": "hi"},
        {"type": "image", "data": "..."},
        {"type": "resource", "uri": "telegram://..."},
    ]
    assert extract_text(blocks) == "hi"


def test_extract_text_empty():
    assert extract_text([]) == ""
    assert extract_text([{"type": "image", "data": "..."}]) == ""


# ---------------------------------------------------------------------------
# Publish handler
# ---------------------------------------------------------------------------


class FakeClient:
    def __init__(self):
        self.sent: list[tuple] = []

    async def send_message(self, peer, text, **kwargs):
        self.sent.append((peer, text, kwargs))
        return _SentMessage(id=999)


class _SentMessage:
    def __init__(self, id):
        self.id = id


@pytest.mark.asyncio
async def test_publish_to_dm_calls_send_message_with_resolved_entity():
    client = FakeClient()
    resolved = {"_entity": True}

    async def resolve_entity_fn(peer_id, client_):
        assert peer_id == 42
        assert client_ is client
        return resolved

    async def ensure_connected_fn(c):
        assert c is client

    handle = make_publish_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    result = await handle(
        {
            "channelId": "telegram:default:dm:42",
            "content": [{"type": "text", "text": "hello"}],
        }
    )

    assert result == {"delivered": True, "messageId": "999"}
    assert client.sent == [(resolved, "hello", {})]


@pytest.mark.asyncio
async def test_publish_to_saved_uses_me_alias():
    client = FakeClient()

    async def resolve_entity_fn(peer_id, client_):
        pytest.fail("should not resolve for saved")

    async def ensure_connected_fn(c):
        pass

    handle = make_publish_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    result = await handle(
        {
            "channelId": "telegram:default:saved",
            "content": [{"type": "text", "text": "note to self"}],
        }
    )

    assert result["delivered"] is True
    assert client.sent == [("me", "note to self", {})]


@pytest.mark.asyncio
async def test_publish_with_reply_to_message_id_threads_correctly():
    client = FakeClient()
    resolved = object()

    async def resolve_entity_fn(peer_id, client_):
        return resolved

    async def ensure_connected_fn(c):
        pass

    handle = make_publish_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    await handle(
        {
            "channelId": "telegram:default:supergroup:1001",
            "content": [{"type": "text", "text": "thread reply"}],
            "replyToMessageId": "7",
        }
    )

    assert client.sent == [(resolved, "thread reply", {"reply_to": 7})]


@pytest.mark.asyncio
async def test_publish_routes_to_correct_account():
    client_a = FakeClient()
    client_b = FakeClient()

    async def resolve_entity_fn(peer_id, client_):
        return f"resolved-on-{id(client_)}"

    async def ensure_connected_fn(c):
        pass

    handle = make_publish_handler(
        {"alpha": client_a, "beta": client_b},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    await handle(
        {
            "channelId": "telegram:beta:dm:42",
            "content": [{"type": "text", "text": "to beta"}],
        }
    )

    assert client_a.sent == []
    assert len(client_b.sent) == 1


@pytest.mark.asyncio
async def test_publish_unknown_account_raises():
    client = FakeClient()

    async def resolve_entity_fn(peer_id, client_):
        return None

    async def ensure_connected_fn(c):
        pass

    handle = make_publish_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    with pytest.raises(ValueError, match="Unknown account"):
        await handle(
            {
                "channelId": "telegram:nonexistent:dm:42",
                "content": [{"type": "text", "text": "hi"}],
            }
        )


@pytest.mark.asyncio
async def test_publish_with_only_non_text_content_raises():
    client = FakeClient()

    async def resolve_entity_fn(peer_id, client_):
        return None

    async def ensure_connected_fn(c):
        pass

    handle = make_publish_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    with pytest.raises(ValueError, match="no text content"):
        await handle(
            {
                "channelId": "telegram:default:dm:42",
                "content": [{"type": "image", "data": "fake"}],
            }
        )


# ---------------------------------------------------------------------------
# channels/list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_handler_aggregates_across_accounts(monkeypatch):
    enumerate_calls: list[str] = []

    async def fake_enumerate(client, label):
        enumerate_calls.append(label)
        return [{"id": f"telegram:{label}:dm:1", "type": "telegram"}]

    monkeypatch.setattr(
        "telegram_mcp.mcpl.handlers.enumerate_channels", fake_enumerate
    )
    handle = make_list_handler({"alpha": object(), "beta": object()})

    out = await handle({})

    assert sorted(enumerate_calls) == ["alpha", "beta"]
    ids = sorted(c["id"] for c in out["channels"])
    assert ids == ["telegram:alpha:dm:1", "telegram:beta:dm:1"]


@pytest.mark.asyncio
async def test_list_handler_continues_when_one_account_fails(monkeypatch):
    async def fake_enumerate(client, label):
        if label == "broken":
            raise RuntimeError("oops")
        return [{"id": f"telegram:{label}:saved"}]

    monkeypatch.setattr(
        "telegram_mcp.mcpl.handlers.enumerate_channels", fake_enumerate
    )
    handle = make_list_handler({"good": object(), "broken": object()})

    out = await handle({})
    assert out["channels"] == [{"id": "telegram:good:saved"}]


# ---------------------------------------------------------------------------
# channels/open and channels/close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_handler_acknowledges():
    handle = make_open_handler()
    out = await handle({"type": "telegram"})
    assert "channel" in out
    assert out["channel"]["type"] == "telegram"
    assert out["channel"]["direction"] == "bidirectional"


@pytest.mark.asyncio
async def test_close_handler_returns_closed_true():
    handle = make_close_handler()
    out = await handle({"channelId": "telegram:default:dm:1"})
    assert out == {"closed": True}


# ---------------------------------------------------------------------------
# channels/typing — fire-and-forget background task
# ---------------------------------------------------------------------------


class FakeAction:
    """Mimics Telethon's client.action(entity, 'typing') context manager."""

    def __init__(self, recorder, peer):
        self._recorder = recorder
        self._peer = peer

    async def __aenter__(self):
        self._recorder.append(("typing-start", self._peer))
        return self

    async def __aexit__(self, *args):
        self._recorder.append(("typing-end", self._peer))


class FakeClientWithAction(FakeClient):
    def __init__(self):
        super().__init__()
        self.actions: list[tuple] = []

    def action(self, peer, kind):
        return FakeAction(self.actions, (peer, kind))


@pytest.mark.asyncio
async def test_typing_handler_returns_immediately_and_runs_in_background():
    client = FakeClientWithAction()
    resolved = object()

    async def resolve_entity_fn(peer_id, client_):
        return resolved

    async def ensure_connected_fn(c):
        pass

    handle = make_typing_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
        default_duration=0.01,
    )

    # Handler must NOT block for the duration of the typing action.
    result = await asyncio.wait_for(
        handle({"channelId": "telegram:default:dm:42"}), timeout=0.5
    )
    assert result is None  # notification — no return value

    # Let the background typing task run.
    await asyncio.sleep(0.05)
    assert ("typing-start", (resolved, "typing")) in client.actions
    assert ("typing-end", (resolved, "typing")) in client.actions


@pytest.mark.asyncio
async def test_typing_handler_silently_drops_unknown_account():
    client = FakeClientWithAction()

    async def resolve_entity_fn(peer_id, client_):
        return None

    async def ensure_connected_fn(c):
        pass

    handle = make_typing_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    # Should not raise; should not record anything.
    await handle({"channelId": "telegram:nonexistent:dm:42"})
    await asyncio.sleep(0.02)
    assert client.actions == []


@pytest.mark.asyncio
async def test_typing_handler_silently_drops_malformed_channel_id():
    client = FakeClientWithAction()

    async def resolve_entity_fn(peer_id, client_):
        return None

    async def ensure_connected_fn(c):
        pass

    handle = make_typing_handler(
        {"default": client},
        resolve_entity_fn=resolve_entity_fn,
        ensure_connected_fn=ensure_connected_fn,
    )

    await handle({"channelId": "not-a-real-id"})
    await asyncio.sleep(0.02)
    assert client.actions == []
