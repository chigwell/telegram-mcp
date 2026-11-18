"""Poll results through message records and get_poll_results, with real Telethon TL objects."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import errors, types
from telethon.tl.functions.messages import GetPollResultsRequest, GetPollVotesRequest

from telegram_mcp import runtime
from telegram_mcp.tools import messages

OPTIONS = [("Tea", b"tea-id"), ("Water", b"\xff\x10"), ("Juice", b"juice-id")]


def _poll(**flags):
    return types.Poll(
        id=77,
        hash=0,
        question=types.TextWithEntities("Pick a drink", []),
        answers=[types.PollAnswer(types.TextWithEntities(t, []), o) for t, o in OPTIONS],
        **flags,
    )


def _results(counts, chosen=(), correct=(), **kwargs):
    return types.PollResults(
        results=[
            types.PollAnswerVoters(
                option=o, voters=n, chosen=o in chosen or None, correct=o in correct or None
            )
            for (_, o), n in zip(OPTIONS, counts)
        ],
        total_voters=sum(counts),
        **kwargs,
    )


def _message(poll, results):
    return types.Message(
        id=42,
        peer_id=types.PeerChat(123),
        date=datetime.now(timezone.utc),
        message="",
        media=types.MessageMediaPoll(poll, results),
    )


def _user(user_id, first_name):
    return types.User(id=user_id, first_name=first_name, access_hash=1)


@pytest.fixture
def polling(monkeypatch):
    poll = _poll(public_voters=True)
    message = _message(poll, types.PollResults(min=True))
    fresh = _results([3, 1, 0], chosen=[b"\xff\x10"])
    client = AsyncMock()
    client.get_messages.return_value = message
    votes = {
        b"tea-id": types.messages.VotesList(
            count=3,
            votes=[types.MessagePeerVote(types.PeerUser(1), b"tea-id", None)],
            chats=[],
            users=[_user(1, "Ann")],
        ),
        b"\xff\x10": types.messages.VotesList(
            count=1,
            votes=[types.MessagePeerVote(types.PeerUser(2), b"\xff\x10", None)],
            chats=[],
            users=[_user(2, "Bob")],
        ),
        b"juice-id": types.messages.VotesList(count=0, votes=[], chats=[], users=[]),
    }

    async def call(request):
        if isinstance(request, GetPollResultsRequest):
            return types.Updates(
                updates=[types.UpdateMessagePoll(poll_id=77, results=fresh)],
                users=[],
                chats=[],
                date=datetime.now(timezone.utc),
                seq=0,
            )
        if isinstance(request, GetPollVotesRequest):
            return votes[request.option]
        raise AssertionError(f"unexpected request {request!r}")

    client.side_effect = call
    entity = types.InputPeerChat(123)
    monkeypatch.setattr(messages, "get_client", Mock(return_value=client))
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=entity))
    monkeypatch.setattr(runtime, "is_multi_mode", lambda: False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    return SimpleNamespace(poll=poll, message=message, client=client, entity=entity)


def test_message_record_includes_results_aligned_with_answers():
    poll = _poll(quiz=True, closed=True)
    results = _results([2, 5, 1], chosen=[b"juice-id"], correct=[b"\xff\x10"], solution="H2O")
    record = messages.message_to_dict(_message(poll, results))["poll"]
    assert record == {
        "question": "Pick a drink",
        "answers": ["Tea", "Water", "Juice"],
        "closed": True,
        "quiz": True,
        "votes": [2, 5, 1],
        "chosen": [2],
        "correct": [1],
        "total_voters": 8,
        "solution": "H2O",
    }


@pytest.mark.asyncio
async def test_results_are_refreshed_from_telegram(polling):
    record = json.loads(await messages.get_poll_results(chat_id=-123, message_id=42))
    request = polling.client.call_args_list[0].args[0]
    assert isinstance(request, GetPollResultsRequest)
    assert (request.peer, request.msg_id) == (polling.entity, 42)
    assert bytes(request)
    assert record["votes"] == [3, 1, 0]
    assert record["chosen"] == [1]
    assert record["total_voters"] == 4
    assert "voters" not in record


@pytest.mark.asyncio
async def test_not_voted_is_explicit(polling):
    async def call(request):
        return types.Updates(
            updates=[types.UpdateMessagePoll(poll_id=77, results=_results([1, 0, 0]))],
            users=[],
            chats=[],
            date=datetime.now(timezone.utc),
            seq=0,
        )

    polling.client.side_effect = call
    record = json.loads(await messages.get_poll_results(chat_id=-123, message_id=42))
    assert record["chosen"] == []


@pytest.mark.asyncio
async def test_voters_listed_per_answer(polling):
    record = json.loads(
        await messages.get_poll_results(chat_id=-123, message_id=42, include_voters=True)
    )
    assert record["voters"] == [
        {"answer": 0, "count": 3, "voters": [{"id": 1, "name": "Ann", "type": "user"}]},
        {"answer": 1, "count": 1, "voters": [{"id": 2, "name": "Bob", "type": "user"}]},
        {"answer": 2, "count": 0, "voters": []},
    ]


@pytest.mark.asyncio
async def test_anonymous_poll_never_asks_for_voters(polling):
    polling.poll.public_voters = None
    record = json.loads(
        await messages.get_poll_results(chat_id=-123, message_id=42, include_voters=True)
    )
    assert "Anonymous" in record["voters_note"]
    assert not any(
        isinstance(c.args[0], GetPollVotesRequest) for c in polling.client.call_args_list
    )


@pytest.mark.asyncio
async def test_refused_voter_list_keeps_counts(polling):
    async def call(request):
        if isinstance(request, GetPollVotesRequest):
            raise errors.PollVoteRequiredError(request=request)
        return types.Updates(updates=[], users=[], chats=[], date=None, seq=0)

    polling.client.side_effect = call
    polling.message.media.results = _results([1, 1, 0])
    record = json.loads(
        await messages.get_poll_results(chat_id=-123, message_id=42, include_voters=True)
    )
    assert record["votes"] == [1, 1, 0]
    assert "refused" in record["voters_note"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "plain"])
async def test_non_poll_is_rejected(polling, kind):
    if kind == "missing":
        polling.client.get_messages.return_value = None
    else:
        polling.message.media = None
    result = await messages.get_poll_results(chat_id=-123, message_id=42)
    assert "does not contain a poll" in result
    polling.client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, expected",
    [({"message_id": 0}, "positive integer"), ({"voters_limit": 0}, "voters_limit")],
)
async def test_invalid_args_never_call_telegram(polling, args, expected):
    result = await messages.get_poll_results(**{"chat_id": -123, "message_id": 42, **args})
    assert expected in result
    polling.client.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_allowlist_blocks_results(polling, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-456")
    result = await messages.get_poll_results(chat_id=-123, message_id=42)
    assert "restricted by privacy policy" in result
    polling.client.get_messages.assert_not_awaited()


def test_registered_as_read_only_tool():
    tool = next(t for t in messages.mcp._tool_manager.list_tools() if t.name == "get_poll_results")
    assert tool.annotations.readOnlyHint
    from telegram_mcp.tools import get_poll_results

    assert get_poll_results is messages.get_poll_results


@pytest.mark.asyncio
async def test_voters_paginate_up_to_limit():
    pages = [
        types.messages.VotesList(
            count=5,
            votes=[types.MessagePeerVote(types.PeerUser(i), b"x", None) for i in (1, 2)],
            chats=[],
            users=[_user(1, "Ann"), _user(2, "Bob")],
            next_offset="next",
        ),
        types.messages.VotesList(
            count=5,
            votes=[types.MessagePeerVote(types.PeerUser(i), b"x", None) for i in (3, 4)],
            chats=[],
            users=[],
            next_offset="more",
        ),
    ]
    client = AsyncMock(side_effect=pages)
    voters, total = await messages._poll_voters(client, "peer", 42, b"x", limit=3)
    assert total == 5
    assert [v["id"] for v in voters] == [1, 2, 3]
    second = client.call_args_list[1].args[0]
    assert (second.offset, second.limit) == ("next", 1)
