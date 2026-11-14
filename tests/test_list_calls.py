"""list_calls returns the account's call log from the phone-call search filter."""

import json
from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import messages

CHRIS = types.User(id=701, first_name="Chris", last_name="L", username="chris", access_hash=7)
DANA = types.User(id=702, first_name="Dana", access_hash=8)


def _call(msg_id, user, day, *, out, reason, duration=None, video=None):
    return types.MessageService(
        id=msg_id,
        peer_id=types.PeerUser(user.id),
        date=datetime(2026, 11, day, 18, 0, tzinfo=timezone.utc),
        out=out,
        action=types.MessageActionPhoneCall(
            call_id=msg_id, reason=reason, duration=duration, video=video
        ),
    )


class CallServer(TelegramClient):
    """A real Telethon client whose server answers every search with fixed calls."""

    def __init__(self, calls, paged=False):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.calls = calls
        self.paged = paged
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        assert isinstance(request, functions.messages.SearchRequest)
        self.requests.append(request)
        if not self.paged:
            return types.messages.Messages(
                messages=self.calls, topics=[], chats=[], users=[CHRIS, DANA]
            )
        older = [c for c in self.calls if not request.offset_id or c.id < request.offset_id]
        return types.messages.MessagesSlice(
            count=len(self.calls),
            messages=older[: request.limit],
            topics=[],
            chats=[],
            users=[CHRIS, DANA],
        )


@pytest.fixture
def serve(monkeypatch):
    def _serve(calls, paged=False):
        client = CallServer(calls, paged)

        async def resolve(chat_id, cl=None):
            return {CHRIS.id: CHRIS, DANA.id: DANA}[int(chat_id)]

        async def connected(cl=None):
            return None

        monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
        monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS", raising=False)
        monkeypatch.setattr(messages, "get_client", lambda account=None: client)
        monkeypatch.setattr(messages, "resolve_entity", resolve)
        monkeypatch.setattr(messages, "ensure_connected", connected)
        return client

    return _serve


@pytest.mark.asyncio
async def test_completed_call_reports_peer_direction_duration_and_reason(serve):
    client = serve(
        [
            _call(
                40, CHRIS, 11, out=True, reason=types.PhoneCallDiscardReasonHangup(), duration=3612
            )
        ]
    )

    result = json.loads(await messages.list_calls())

    assert result["results"] == [
        {
            "id": 40,
            "peer": {"id": 701, "name": "Chris L", "username": "chris"},
            "date": "2026-11-11T18:00:00+00:00",
            "direction": "outgoing",
            "duration": 3612,
            "end_reason": "hangup",
            "video": False,
        }
    ]
    request = client.requests[0]
    assert isinstance(request.peer, types.InputPeerEmpty)
    assert isinstance(request.filter, types.InputMessagesFilterPhoneCalls)


@pytest.mark.asyncio
async def test_missed_call_is_incoming_with_zero_duration(serve):
    serve([_call(41, DANA, 12, out=False, reason=types.PhoneCallDiscardReasonMissed())])

    (call,) = json.loads(await messages.list_calls())["results"]

    assert (call["direction"], call["duration"], call["end_reason"]) == ("incoming", 0, "missed")


@pytest.mark.asyncio
async def test_video_call_with_one_peer(serve):
    client = serve(
        [
            _call(
                42,
                CHRIS,
                13,
                out=False,
                reason=types.PhoneCallDiscardReasonHangup(),
                duration=95,
                video=True,
            )
        ]
    )

    (call,) = json.loads(await messages.list_calls(chat_id=CHRIS.id))["results"]

    assert call["video"] is True
    assert client.requests[0].peer == types.InputPeerUser(CHRIS.id, CHRIS.access_hash)


@pytest.mark.asyncio
async def test_date_range_bounds_the_search_and_the_results(serve):
    client = serve(
        [
            _call(45, DANA, 15, out=True, reason=types.PhoneCallDiscardReasonHangup(), duration=1),
            _call(44, DANA, 11, out=True, reason=types.PhoneCallDiscardReasonHangup(), duration=1),
            _call(43, DANA, 9, out=True, reason=types.PhoneCallDiscardReasonHangup(), duration=1),
        ]
    )

    result = json.loads(await messages.list_calls(from_date="2026-11-10", to_date="2026-11-12"))

    assert [c["id"] for c in result["results"]] == [44]
    request = client.requests[0]
    assert request.min_date == datetime(2026, 11, 10, tzinfo=timezone.utc)
    assert request.max_date == datetime(2026, 11, 13, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_chat_allowlist_hides_calls_with_other_users(serve, monkeypatch):
    serve(
        [
            _call(
                47, CHRIS, 14, out=True, reason=types.PhoneCallDiscardReasonHangup(), duration=1
            ),
            _call(46, DANA, 14, out=True, reason=types.PhoneCallDiscardReasonHangup(), duration=1),
        ]
    )
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", str(DANA.id))

    result = json.loads(await messages.list_calls())
    refused = await messages.list_calls(chat_id=CHRIS.id)

    assert [c["peer"]["id"] for c in result["results"]] == [DANA.id]
    assert "restricted by privacy policy" in refused


@pytest.mark.asyncio
async def test_calls_beyond_one_page_are_fetched_from_older_pages(serve):
    hangup = types.PhoneCallDiscardReasonHangup()
    client = serve(
        [_call(i, DANA, 1, out=True, reason=hangup, duration=1) for i in range(300, 150, -1)],
        paged=True,
    )

    result = json.loads(await messages.list_calls(limit=120))

    assert [c["id"] for c in result["results"]] == list(range(300, 180, -1))
    assert [(r.offset_id, r.limit) for r in client.requests] == [(0, 100), (201, 20)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 0}, {"limit": 501}, {"from_date": "2026-11-12", "to_date": "2026-11-10"}],
)
async def test_bad_arguments_send_no_request(serve, kwargs):
    client = serve([])

    result = await messages.list_calls(**kwargs)

    assert "results" not in result
    assert client.requests == []
