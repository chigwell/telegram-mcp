"""search_global(page=N) must return the N-th page, not the first one again."""

import json
from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import messages

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
CHANNEL = types.Channel(
    id=1555000000,
    title="Новости",
    photo=types.ChatPhotoEmpty(),
    date=NOW,
    broadcast=True,
    access_hash=5,
)
POSTS = [
    types.Message(
        id=number, peer_id=types.PeerChannel(CHANNEL.id), date=NOW, message=f"отчёт №{number}"
    )
    for number in range(45, 0, -1)  # newest first, like Telegram's search
]


class SearchServer(TelegramClient):
    def __init__(self):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        assert isinstance(request, functions.messages.SearchGlobalRequest)
        self.requests.append((request.offset_id, request.limit))
        remaining = [m for m in POSTS if not request.offset_id or m.id < request.offset_id]
        chunk = remaining[: request.limit]
        return types.messages.MessagesSlice(
            messages=chunk,
            count=len(POSTS),
            next_rate=0,
            topics=[],
            inexact=False,
            chats=[CHANNEL],
            users=[],
        )


@pytest.fixture
def server(monkeypatch):
    client = SearchServer()

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "ensure_connected", connected)
    return client


def numbers(result):
    return [int(r["text"].split("№")[1]) for r in json.loads(result)["results"]]


@pytest.mark.asyncio
async def test_each_page_continues_where_the_previous_one_ended(server):
    pages = [
        numbers(await messages.search_global(query="отчёт", page=page, page_size=15))
        for page in (1, 2, 3)
    ]

    assert pages == [
        list(range(45, 30, -1)),
        list(range(30, 15, -1)),
        list(range(15, 0, -1)),
    ]


@pytest.mark.asyncio
async def test_a_page_past_the_end_says_so(server):
    result = await messages.search_global(query="отчёт", page=4, page_size=15)

    assert result == "No messages found for this page."
