"""list_messages with search_query + to_date must start the search at to_date.

Without a server-side bound, a search that should cover one old day walks
every newer match first (newest -> oldest, 100 per request, 1s apart), which
times out in busy groups long before reaching the requested day.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl import types
from telethon.tl.functions.messages import SearchRequest

from telegram_mcp.tools import messages

NEXT_MIDNIGHT = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _msg(msg_id, date):
    return SimpleNamespace(
        id=msg_id, sender=None, sender_id=1, date=date, message="前端", reply_to=None
    )


class _RecordingClient:
    def __init__(self, found):
        self.found = found
        self.calls = []

    def iter_messages(self, entity, **kwargs):
        self.calls.append(kwargs)

        async def _gen():
            for m in self.found:
                yield m

        return _gen()


@pytest.fixture
def patch_client(monkeypatch):
    def _patch(client):
        monkeypatch.setattr(messages, "get_client", lambda account=None: client)
        monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=SimpleNamespace()))
        monkeypatch.setattr(messages, "get_marked_id", lambda entity: -1001)
        monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())

    return _patch


@pytest.mark.asyncio
async def test_search_with_to_date_starts_at_next_midnight(patch_client):
    client = _RecordingClient([_msg(7, datetime(2026, 9, 23, 23, 59, 59, tzinfo=timezone.utc))])
    patch_client(client)

    result = await messages.list_messages(
        chat_id=-1001, search_query="前端", to_date="2026-09-23", limit=5, account=None
    )

    assert client.calls == [{"search": "前端", "offset_date": NEXT_MIDNIGHT}]
    assert [r["id"] for r in json.loads(result)["results"]] == [7]


@pytest.mark.asyncio
async def test_search_without_to_date_has_no_offset_date(patch_client):
    client = _RecordingClient([_msg(7, datetime(2026, 9, 23, tzinfo=timezone.utc))])
    patch_client(client)

    await messages.list_messages(
        chat_id=-1001, search_query="前端", from_date="2026-09-01", limit=5, account=None
    )

    assert client.calls == [{"search": "前端"}]


@pytest.mark.asyncio
async def test_search_still_drops_newer_matches_if_server_ignores_max_date(patch_client):
    client = _RecordingClient(
        [
            _msg(9, datetime(2026, 9, 24, 0, 0, 0, tzinfo=timezone.utc)),
            _msg(8, datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)),
        ]
    )
    patch_client(client)

    result = await messages.list_messages(
        chat_id=-1001, search_query="前端", to_date="2026-09-23", limit=5, account=None
    )

    assert [r["id"] for r in json.loads(result)["results"]] == [8]


class _CapturingTelethonClient(TelegramClient):
    """Real Telethon request building; only the network call is replaced."""

    def __init__(self):
        super().__init__(StringSession(), 1, "0" * 32)
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        self.requests.append(
            (
                type(request),
                getattr(request, "max_date", None),
                getattr(request, "offset_id", None),
            )
        )
        peer = types.PeerChannel(1001)
        if len(self.requests) == 1:
            batch = [
                types.Message(id=500 - i, peer_id=peer, date=NEXT_MIDNIGHT, message="x")
                for i in range(100)
            ]
            return types.messages.MessagesSlice(
                count=1000, messages=batch, topics=[], chats=[], users=[]
            )
        return types.messages.MessagesSlice(count=1000, messages=[], topics=[], chats=[], users=[])


@pytest.mark.asyncio
async def test_telethon_sends_offset_date_as_search_max_date_on_first_request_only():
    client = _CapturingTelethonClient()
    entity = types.InputPeerChannel(channel_id=1001, access_hash=1)

    async for _ in client.iter_messages(entity, search="前端", offset_date=NEXT_MIDNIGHT):
        pass

    first, second = client.requests
    assert first == (SearchRequest, NEXT_MIDNIGHT, 0)
    assert second == (SearchRequest, None, 401)
