"""get_pinned_messages must return every pinned message, not just the newest one."""

import json
from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import messages

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
CHANNEL = types.Channel(
    id=1777000000,
    title="Team",
    photo=types.ChatPhotoEmpty(),
    date=NOW,
    megagroup=True,
    access_hash=42,
)
ALICE = types.User(id=501, first_name="Alice", access_hash=1)


class PinnedServer(TelegramClient):
    """A real Telethon client whose chat has three pinned messages."""

    def __init__(self):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.limits = []
        self.pinned = [
            types.Message(
                id=i,
                peer_id=types.PeerChannel(CHANNEL.id),
                from_id=types.PeerUser(ALICE.id),
                date=NOW,
                message=text,
                pinned=True,
            )
            for i, text in [(30, "Rules"), (20, "Deadline"), (10, "Docs")]
        ]

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        assert isinstance(request, functions.messages.SearchRequest)
        self.limits.append(request.limit)
        found = self.pinned[: request.limit]
        return types.messages.ChannelMessages(
            pts=1,
            count=len(self.pinned),
            messages=found,
            topics=[],
            chats=[CHANNEL],
            users=[ALICE],
        )


@pytest.mark.asyncio
async def test_get_pinned_messages_returns_all_pinned_messages(monkeypatch):
    client = PinnedServer()

    async def resolve(chat_id, cl=None):
        return CHANNEL

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", resolve)
    monkeypatch.setattr(messages, "ensure_connected", connected)

    result = json.loads(await messages.get_pinned_messages(chat_id=-1001777000000))

    assert [m["id"] for m in result["results"]] == [30, 20, 10]
    assert client.limits == [100]
