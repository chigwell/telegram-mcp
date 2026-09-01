"""Tests for filtered message history reads."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import messages


def _message(message_id: int, date: datetime, text: str = "message"):
    return SimpleNamespace(
        id=message_id,
        date=date,
        message=text,
        sender=SimpleNamespace(
            first_name="Alice",
            last_name=None,
            username="alice",
        ),
        sender_id=42,
        grouped_id=None,
        reply_to=None,
        views=None,
        forwards=None,
        reactions=None,
    )


class _MessageClient:
    def __init__(self, source_messages):
        self.source_messages = source_messages
        self.get_messages_calls = []
        self.iter_messages_calls = []

    async def get_messages(self, entity, limit, **kwargs):
        self.get_messages_calls.append({"entity": entity, "limit": limit, "kwargs": kwargs})
        return self.source_messages[:limit]

    async def iter_messages(self, entity, **kwargs):
        self.iter_messages_calls.append({"entity": entity, "kwargs": kwargs})
        for message in self.source_messages:
            yield message


def _patch_client(monkeypatch, client):
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)

    async def resolve_chat(chat_id, resolved_client=None):
        assert chat_id == -100123
        assert resolved_client is client
        return "group-peer"

    monkeypatch.setattr(messages, "resolve_entity", resolve_chat)


def _result_ids(result: str):
    return [record["id"] for record in json.loads(result)["results"]]


@pytest.mark.asyncio
async def test_list_messages_keeps_unfiltered_fast_path(monkeypatch):
    client = _MessageClient([_message(3, datetime(2026, 8, 15, 12, tzinfo=timezone.utc))])
    _patch_client(monkeypatch, client)

    async def unexpected_sender_resolution(*args, **kwargs):
        raise AssertionError("from_user must not be resolved when it is omitted")

    monkeypatch.setattr(messages, "resolve_input_entity", unexpected_sender_resolution)

    result = await messages.list_messages(chat_id=-100123, limit=5)

    assert _result_ids(result) == [3]
    assert client.get_messages_calls == [{"entity": "group-peer", "limit": 5, "kwargs": {}}]
    assert client.iter_messages_calls == []


@pytest.mark.asyncio
async def test_list_messages_filters_by_resolved_sender(monkeypatch):
    client = _MessageClient([_message(3, datetime(2026, 8, 15, 12, tzinfo=timezone.utc))])
    _patch_client(monkeypatch, client)

    async def resolve_sender(from_user, resolved_client=None):
        assert from_user == "@alice"
        assert resolved_client is client
        return "alice-input-peer"

    monkeypatch.setattr(messages, "resolve_input_entity", resolve_sender)

    result = await messages.list_messages(chat_id=-100123, from_user="@alice")

    assert _result_ids(result) == [3]
    assert client.get_messages_calls == []
    assert client.iter_messages_calls == [
        {
            "entity": "group-peer",
            "kwargs": {"from_user": "alice-input-peer"},
        }
    ]


@pytest.mark.asyncio
async def test_sender_search_applies_dates_and_limit_after_server_filters(monkeypatch):
    client = _MessageClient(
        [
            _message(5, datetime(2026, 8, 21, tzinfo=timezone.utc)),
            _message(4, datetime(2026, 8, 20, 20, tzinfo=timezone.utc)),
            _message(3, datetime(2026, 8, 15, tzinfo=timezone.utc)),
            _message(2, datetime(2026, 8, 14, tzinfo=timezone.utc)),
            _message(1, datetime(2026, 8, 9, tzinfo=timezone.utc)),
        ]
    )
    _patch_client(monkeypatch, client)

    async def resolve_sender(from_user, resolved_client=None):
        return "alice-input-peer"

    monkeypatch.setattr(messages, "resolve_input_entity", resolve_sender)

    result = await messages.list_messages(
        chat_id=-100123,
        limit=2,
        search_query="release",
        from_date="2026-08-10",
        to_date="2026-08-20",
        from_user=42,
    )

    assert _result_ids(result) == [4, 3]
    assert client.iter_messages_calls == [
        {
            "entity": "group-peer",
            "kwargs": {
                "search": "release",
                "from_user": "alice-input-peer",
            },
        }
    ]


def test_list_messages_schema_marks_sender_filter_optional():
    tool = next(
        tool for tool in messages.mcp._tool_manager.list_tools() if tool.name == "list_messages"
    )
    schema = tool.parameters["properties"]["from_user"]

    assert schema["default"] is None
    assert {option["type"] for option in schema["anyOf"]} == {
        "integer",
        "string",
        "null",
    }
