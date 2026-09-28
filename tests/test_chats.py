"""Tests for chats tools, specifically list_topics chat_id validation and resolution."""

import json
from types import SimpleNamespace

import pytest
from telethon.tl.types import Channel

from telegram_mcp.tools import chats


def _supergroup(*, forum=True):
    return Channel(
        id=12345,
        title="Forum Group",
        photo=None,
        date=None,
        creator=True,
        left=False,
        broadcast=False,
        verified=False,
        megagroup=True,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=forum,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        access_hash=67890,
    )


class RecordingClient:
    def __init__(self, result=None):
        self.requests = []
        self.result = result or SimpleNamespace(
            topics=[
                SimpleNamespace(
                    id=1,
                    title="General",
                    total_messages=10,
                    unread_count=2,
                    closed=False,
                    hidden=False,
                    top_message=None,
                )
            ],
            messages=[],
        )

    async def __call__(self, request):
        self.requests.append(request)
        return self.result


@pytest.mark.asyncio
async def test_list_topics_int_chat_id(monkeypatch):
    entity = _supergroup(forum=True)
    client = RecordingClient()
    resolved_ids = []

    async def fake_resolve(chat_id, cl):
        resolved_ids.append(chat_id)
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    result = await chats.list_topics(chat_id=12345)
    data = json.loads(result)
    assert data["results"][0]["title"] == "General"
    assert resolved_ids == [12345]
    assert len(client.requests) == 1
    assert isinstance(client.requests[0], chats.GetForumTopicsRequest)


@pytest.mark.asyncio
async def test_list_topics_string_int_chat_id(monkeypatch):
    entity = _supergroup(forum=True)
    client = RecordingClient()
    resolved_ids = []

    async def fake_resolve(chat_id, cl):
        resolved_ids.append(chat_id)
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    result = await chats.list_topics(chat_id="-1001234567890")
    data = json.loads(result)
    assert data["results"][0]["title"] == "General"
    assert resolved_ids == [-1001234567890]


@pytest.mark.asyncio
async def test_list_topics_username_chat_id(monkeypatch):
    entity = _supergroup(forum=True)
    client = RecordingClient()
    resolved_ids = []

    async def fake_resolve(chat_id, cl):
        resolved_ids.append(chat_id)
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    result = await chats.list_topics(chat_id="@myforum")
    data = json.loads(result)
    assert data["results"][0]["title"] == "General"
    assert resolved_ids == ["@myforum"]


@pytest.mark.asyncio
async def test_list_topics_invalid_chat_id():
    result = await chats.list_topics(chat_id=123.45)
    assert "Invalid chat_id" in result
    assert "Type must be an integer or a string" in result
