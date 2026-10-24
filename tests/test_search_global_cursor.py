"""search_global walks Telegram's (rate, peer, id) cursor and hands it back as next_cursor."""

import json
from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import messages

NOW = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
CHANNEL = types.Channel(
    id=1555000000, title="Новости", photo=types.ChatPhotoEmpty(), date=NOW, access_hash=5
)
FRIEND = types.User(id=777, first_name="Friend", access_hash=77)


def _results():
    """Newest first. Message ids are per chat: a small private chat has low ids."""
    posts = [
        types.Message(
            id=5000 - n, peer_id=types.PeerChannel(CHANNEL.id), date=NOW, message=f"отчёт {n}"
        )
        for n in range(300)
    ]
    # The 101st hit is message 3 of a private chat: Telethon's iterator stops here,
    # because a first id <= the chunk size "means" nothing older is left.
    dm = types.Message(id=3, peer_id=types.PeerUser(FRIEND.id), date=NOW, message="отчёт DM")
    return posts[:100] + [dm] + posts[100:]


class GlobalSearchServer(TelegramClient):
    """Answers messages.searchGlobal like Telegram: from the cursor, `limit` at a time."""

    def __init__(self, results=None, response_type=types.messages.MessagesSlice):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.results = _results() if results is None else results
        self.response_type = response_type
        self.requests = []

    def _position(self, request):
        if not request.offset_id:
            return 0
        for index, msg in enumerate(self.results):
            same_peer = isinstance(request.offset_peer, types.InputPeerEmpty) or (
                msg.peer_id is not None
                and messages.utils.get_peer_id(msg.peer_id)
                == messages.utils.get_peer_id(request.offset_peer)
            )
            if msg.id == request.offset_id and same_peer:
                # The server's next_rate, carried back (Telethon pairs it with the last
                # non-empty message, so it may belong to a later MessageEmpty).
                assert request.offset_rate >= 1000 + index
                return index + 1
        raise AssertionError("cursor points at no result")

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        assert isinstance(request, functions.messages.SearchGlobalRequest)
        self.requests.append(request)
        start = self._position(request)
        chunk = self.results[start : start + request.limit]
        kwargs = dict(messages=chunk, chats=[CHANNEL], users=[FRIEND], topics=[])
        if self.response_type is types.messages.Messages:
            return types.messages.Messages(**kwargs)
        return types.messages.MessagesSlice(
            count=len(self.results),
            next_rate=1000 + start + len(chunk) - 1 if chunk else None,
            inexact=False,
            **kwargs,
        )


@pytest.fixture
def server(monkeypatch):
    client = GlobalSearchServer()

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "ensure_connected", connected)
    return client


def ids(result):
    return [(r["chat_id"], r["id"]) for r in json.loads(result)["results"]]


@pytest.mark.asyncio
async def test_telethon_iterator_stops_at_a_low_id_from_another_chat(server):
    # The model reproduces the bug: the chunk that starts with the low id is the last.
    found = await server.get_messages(None, limit=400, search="отчёт")
    assert len(found) == 200 < len(server.results)


@pytest.mark.asyncio
async def test_deep_page_reads_past_a_low_id_from_another_chat(server):
    page6 = json.loads(await messages.search_global("отчёт", page=6, page_size=20))
    assert ids(json.dumps(page6))[0] == (FRIEND.id, 3)
    assert len(page6["results"]) == 20
    assert page6["exhausted"] is False
    assert [r.limit for r in server.requests] == [100, 20]


@pytest.mark.asyncio
async def test_cursor_walks_every_result_once_one_request_per_call(server):
    seen, cursor, calls = [], None, 0
    while True:
        result = json.loads(await messages.search_global("отчёт", page_size=40, cursor=cursor))
        calls += 1
        seen += [(r["chat_id"], r["id"]) for r in result["results"]]
        if result["exhausted"]:
            assert result["next_cursor"] is None
            break
        cursor = result["next_cursor"]
    assert seen == [(messages.utils.get_peer_id(m.peer_id), m.id) for m in server.results]
    assert len(set(seen)) == len(server.results) == 301
    assert len(server.requests) == calls + 1  # the last call learns the end from an empty slice


@pytest.mark.asyncio
async def test_pages_and_cursor_agree(server):
    pages = [ids(await messages.search_global("отчёт", page=n, page_size=50)) for n in (1, 2, 3)]
    first = json.loads(await messages.search_global("отчёт", page_size=50))
    second = await messages.search_global("отчёт", page_size=50, cursor=first["next_cursor"])
    assert pages[0] == ids(json.dumps(first))
    assert pages[1] == ids(second)
    assert len({k for page in pages for k in page}) == 150


@pytest.mark.asyncio
async def test_cursor_needs_no_entity_cache(server, monkeypatch):
    first = json.loads(await messages.search_global("отчёт", page=6, page_size=20))
    fresh = GlobalSearchServer()  # a restarted server: empty session, empty cache
    monkeypatch.setattr(messages, "get_client", lambda account=None: fresh)
    second = await messages.search_global("отчёт", page_size=20, cursor=first["next_cursor"])
    after = server.results[120]  # page 6 of 20 ends with result 119
    assert ids(second)[0] == (messages.utils.get_peer_id(after.peer_id), after.id)
    peer = fresh.requests[0].offset_peer
    assert isinstance(peer, types.InputPeerChannel) and peer.access_hash == CHANNEL.access_hash


@pytest.mark.asyncio
async def test_page_past_the_end_keeps_the_plain_message(server):
    assert await messages.search_global("отчёт", page=20, page_size=20) == (
        "No messages found for this page."
    )


@pytest.mark.asyncio
async def test_a_non_slice_response_is_the_whole_result(server):
    server.response_type = types.messages.Messages
    server.results = server.results[:5]
    result = json.loads(await messages.search_global("отчёт", page_size=20))
    assert len(result["results"]) == 5
    assert result["exhausted"] is True and result["next_cursor"] is None
    assert len(server.requests) == 1


@pytest.mark.asyncio
async def test_cursor_steps_over_empty_messages(server):
    real = server.results[:6]
    server.results = real[:3] + [types.MessageEmpty(id=99, peer_id=None)] + real[3:]
    first = json.loads(await messages.search_global("отчёт", page_size=4))
    assert [r["id"] for r in first["results"]] == [m.id for m in real[:4]]
    rest = json.loads(
        await messages.search_global("отчёт", page_size=20, cursor=first["next_cursor"])
    )
    assert [r["id"] for r in rest["results"]] == [m.id for m in real[4:]]
    assert rest["exhausted"] is True


@pytest.mark.asyncio
async def test_endless_empty_messages_end_the_search(server):
    server.results = server.results[:2] + [
        types.MessageEmpty(id=900 - n, peer_id=None) for n in range(50)
    ]
    result = json.loads(await messages.search_global("отчёт", page_size=10))
    assert len(result["results"]) == 2 and result["exhausted"] is True
    assert len(server.requests) == 1 + messages.SEARCH_GLOBAL_MAX_EMPTY_CHUNKS


@pytest.mark.asyncio
async def test_allowlist_shortens_a_page_but_not_the_cursor(server, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", str(FRIEND.id))
    first = json.loads(await messages.search_global("отчёт", page_size=100))
    assert first["results"] == [] and first["exhausted"] is False
    second = json.loads(
        await messages.search_global("отчёт", page_size=100, cursor=first["next_cursor"])
    )
    assert [(r["chat_id"], r["id"]) for r in second["results"]] == [(FRIEND.id, 3)]


@pytest.mark.asyncio
async def test_cursor_is_tied_to_its_query_and_account(server):
    first = json.loads(await messages.search_global("отчёт", page_size=10, account="work"))
    for kwargs in ({"query": "другое", "account": "work"}, {"query": "отчёт", "account": "home"}):
        result = await messages.search_global(cursor=first["next_cursor"], **kwargs)
        assert "error" in result.lower()
    assert len(server.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"cursor": "not-a-cursor"},
        {"cursor": "e30=", "page": 2},
    ],
)
async def test_rejects_bad_paging_before_any_request(server, kwargs):
    result = await messages.search_global("отчёт", **kwargs)
    assert "error" in result.lower()
    assert server.requests == []
