import json
from types import SimpleNamespace

import pytest
from telethon.tl import functions
from telethon.tl.types import Channel

from telegram_mcp.tools import chats


def _supergroup(*, forum=False):
    return Channel(
        id=12345,
        title="Hermes Topics",
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
        self.result = result or SimpleNamespace(updates=[])

    async def __call__(self, request):
        self.requests.append(request)
        return self.result


@pytest.mark.asyncio
async def test_enable_forum_topics_sends_toggle_forum_request(monkeypatch):
    entity = _supergroup(forum=False)
    client = RecordingClient()

    async def fake_resolve(chat_id, cl):
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    result = await chats.enable_forum_topics(chat_id=12345)

    assert result == "Forum topics enabled for Hermes Topics."
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.channels.ToggleForumRequest)
    assert request.channel is entity
    assert request.enabled is True
    assert request.tabs is True
    assert entity.forum is True


@pytest.mark.asyncio
async def test_create_forum_topic_sends_raw_create_forum_topic_request(monkeypatch):
    entity = _supergroup(forum=True)
    client = RecordingClient(SimpleNamespace(updates=[SimpleNamespace(id=777)]))

    async def fake_resolve(chat_id, cl):
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    result = await chats.create_forum_topic(chat_id=12345, title="Dev", icon_color=0x6FB9F0)

    payload = json.loads(result)
    assert payload["results"] == [{"chat_id": -1000000012345, "topic_id": 777, "title": "Dev"}]
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, chats.CreateForumTopicRequest)
    assert request.peer is entity
    assert request.title == "Dev"
    assert request.icon_color == 0x6FB9F0
    assert isinstance(request.random_id, int)


@pytest.mark.asyncio
async def test_create_forum_topic_requires_forum_enabled(monkeypatch):
    entity = _supergroup(forum=False)
    client = RecordingClient()

    async def fake_resolve(chat_id, cl):
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)

    result = await chats.create_forum_topic(chat_id=12345, title="Dev")

    assert (
        result
        == "The specified supergroup does not have forum topics enabled. Use enable_forum_topics first."
    )
    assert client.requests == []


class SequenceClient:
    def __init__(self, results):
        self.requests = []
        self.results = list(results)

    async def __call__(self, request):
        self.requests.append(request)
        return self.results.pop(0)


def _patch_client(monkeypatch, entity, client):
    async def fake_resolve(chat_id, cl):
        return entity

    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", fake_resolve)


@pytest.mark.asyncio
async def test_edit_forum_topic_sends_only_changed_fields(monkeypatch):
    entity = _supergroup(forum=True)
    client = RecordingClient()
    _patch_client(monkeypatch, entity, client)

    result = await chats.edit_forum_topic(chat_id=12345, topic_id=42, title="Renamed", closed=True)

    payload = json.loads(result)
    assert payload["results"] == [
        {"chat_id": -1000000012345, "topic_id": 42, "title": "Renamed", "closed": True}
    ]
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.EditForumTopicRequest)
    assert request.peer is entity
    assert request.topic_id == 42
    assert request.title == "Renamed"
    assert request.closed is True
    assert request.icon_emoji_id is None
    assert request.hidden is None


@pytest.mark.asyncio
async def test_edit_forum_topic_without_changes_sends_nothing(monkeypatch):
    entity = _supergroup(forum=True)
    client = RecordingClient()
    _patch_client(monkeypatch, entity, client)

    result = await chats.edit_forum_topic(chat_id=12345, topic_id=42)

    assert result == "Nothing to change: pass title, icon_emoji_id, closed or hidden."
    assert client.requests == []


@pytest.mark.asyncio
async def test_delete_forum_topic_repeats_until_history_is_gone(monkeypatch):
    entity = _supergroup(forum=True)
    client = SequenceClient(
        [
            SimpleNamespace(pts=1, pts_count=100, offset=7),
            SimpleNamespace(pts=2, pts_count=3, offset=0),
        ]
    )
    _patch_client(monkeypatch, entity, client)

    result = await chats.delete_forum_topic(chat_id=12345, topic_id=42)

    payload = json.loads(result)
    assert payload["results"] == [
        {"chat_id": -1000000012345, "topic_id": 42, "deleted": True, "batches": 2}
    ]
    assert len(client.requests) == 2
    for request in client.requests:
        assert isinstance(request, functions.messages.DeleteTopicHistoryRequest)
        assert request.peer is entity
        assert request.top_msg_id == 42


@pytest.mark.asyncio
async def test_delete_forum_topic_requires_forum_enabled(monkeypatch):
    entity = _supergroup(forum=False)
    client = RecordingClient()
    _patch_client(monkeypatch, entity, client)

    result = await chats.delete_forum_topic(chat_id=12345, topic_id=42)

    assert (
        result
        == "The specified supergroup does not have forum topics enabled. Use enable_forum_topics first."
    )
    assert client.requests == []
