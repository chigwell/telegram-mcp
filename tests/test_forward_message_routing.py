"""Offline forwarding contracts using Telethon's generated request types."""

import json
from datetime import datetime, timezone

import pytest
from telethon import errors
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import messages


class RecordingClient:
    def __init__(self):
        self.requests = []
        self.legacy = []
        self.reads = []
        self.resolve_calls = []
        self.error = None
        self.resolve_error = None
        self.result = types.Updates([], [], [], datetime.now(timezone.utc), 1)
        self.peers = {
            "source": types.InputPeerChannel(11, 101),
            "destination": types.InputPeerUser(22, 202),
            "sender": types.InputPeerChannel(33, 303),
        }
        self.album = []
        self.build_updates = None  # callable(request) -> updates echoing its random_ids

    async def resolve(self, identifier, client):
        assert client is self
        self.resolve_calls.append(identifier)
        if identifier == "sender" and self.resolve_error:
            raise self.resolve_error
        return self.peers[identifier]

    async def __call__(self, request):
        self.requests.append(request)
        bytes(request)  # Exercise the real SDK serializer, not a fake TL schema.
        if self.error:
            raise self.error
        if self.build_updates:
            return types.Updates(
                self.build_updates(request), [], [], datetime.now(timezone.utc), 1
            )
        return self.result

    async def forward_messages(self, *args, **kwargs):
        self.legacy.append((args, kwargs))

    async def get_messages(self, entity, *, ids):
        self.reads.append(ids)
        if isinstance(ids, list):
            return self.album
        return next((m for m in self.album if m.id == ids), None)


@pytest.fixture
def client(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", client.resolve)
    monkeypatch.setattr(messages, "resolve_input_entity", client.resolve)
    return client


@pytest.mark.asyncio
async def test_legacy_call_is_unchanged(client):
    result = await messages.forward_message("source", 10, "destination", expand_album=False)
    assert result == "Message 10 forwarded from source to destination."
    assert client.legacy == [((client.peers["destination"], 10, client.peers["source"]), {})]
    assert client.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message_id,expand,expected",
    [(10, True, [9, 10]), (10, False, 10), ([10, 12], True, [10, 12])],
)
async def test_legacy_album_and_explicit_batch_are_unchanged(client, message_id, expand, expected):
    client.album = [
        types.Message(id=i, peer_id=types.PeerChannel(11), grouped_id=99) for i in [9, 10]
    ]
    await messages.forward_message("source", message_id, "destination", expand_album=expand)
    assert client.requests == []
    assert client.legacy == [((client.peers["destination"], expected, client.peers["source"]), {})]
    assert client.resolve_calls == ["source", "destination"]
    assert len(client.reads) == (2 if expand and isinstance(message_id, int) else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route", [{"topic_id": 7}, {"send_as": "sender"}, {"topic_id": 7, "send_as": "sender"}]
)
async def test_routed_forward_uses_native_request(client, route):
    result = await messages.forward_message("source", [10, 12], "destination", **route)
    assert result == (
        "2 messages forwarded from source to destination. "
        "Destination message IDs: not returned by Telegram."
    )
    assert client.legacy == []
    assert client.reads == []
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.from_peer is client.peers["source"]
    assert request.to_peer is client.peers["destination"]
    assert request.id == [10, 12]
    assert len(set(request.random_id)) == 2
    assert request.top_msg_id == route.get("topic_id")
    assert request.send_as is (client.peers["sender"] if "send_as" in route else None)
    assert request.reply_to is None
    assert not request.drop_media_captions
    assert not request.drop_author
    assert not request.silent


@pytest.mark.asyncio
@pytest.mark.parametrize("route", [{}, {"topic_id": 7, "send_as": "sender"}])
@pytest.mark.parametrize(
    "flags", [{"drop_author": True}, {"silent": True}, {"drop_author": True, "silent": True}]
)
async def test_forward_flags_keep_native_media_and_captions(client, route, flags):
    result = await messages.forward_message("source", [10, 12], "destination", **flags, **route)
    assert "2 messages forwarded" in result
    assert client.legacy == []
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.ForwardMessagesRequest)
    assert request.id == [10, 12]
    assert request.drop_author is flags.get("drop_author", False)
    assert request.silent is flags.get("silent", False)
    assert not request.drop_media_captions
    assert client.reads == []


@pytest.mark.asyncio
@pytest.mark.parametrize("topic_id", [0, -1, True, 1.5, "7"])
async def test_invalid_topic_fails_before_any_send(client, topic_id):
    result = await messages.forward_message("source", [10], "destination", topic_id=topic_id)
    assert "topic_id must be a positive integer" in result
    assert client.requests == client.legacy == []


@pytest.mark.asyncio
async def test_discovery_maps_returned_peers_without_changing_default(client):
    client.result = types.channels.SendAsPeers(
        peers=[
            types.SendAsPeer(types.PeerUser(44)),
            types.SendAsPeer(types.PeerChannel(33), premium_required=True),
        ],
        chats=[types.Channel(33, "Editorial", None, None)],
        users=[types.User(44, first_name="Alex", last_name="Example")],
    )
    result = json.loads(await messages.get_send_as("destination", account="default"))
    assert result["results"] == [
        {"id": 44, "name": "Alex Example", "premium_required": False},
        {"id": -1000000000033, "name": "Editorial", "premium_required": True},
    ]
    assert len(client.requests) == 1
    assert isinstance(client.requests[0], functions.channels.GetSendAsRequest)
    assert client.requests[0].peer is client.peers["destination"]
    assert client.legacy == []


@pytest.mark.asyncio
@pytest.mark.parametrize("expand", [True, False])
async def test_album_routing_preserves_grouped_batch_and_caption(client, expand):
    client.album = [
        types.Message(
            id=i,
            peer_id=types.PeerChannel(11),
            grouped_id=99,
            message="Caption",
            media=types.MessageMediaPhoto(),
        )
        for i in [9, 10, 11]
    ]
    result = await messages.forward_message(
        "source", 10, "destination", topic_id=7, send_as="sender", expand_album=expand
    )
    assert len(client.requests) == 1
    assert client.requests[0].id == ([9, 10, 11] if expand else [10])
    assert not client.requests[0].drop_media_captions
    assert len(client.reads) == (2 if expand else 0)
    assert ("Album of 3" if expand else "Message 10") in result
    assert client.legacy == []


@pytest.mark.asyncio
async def test_sender_resolution_failure_never_falls_back(client):
    client.resolve_error = ValueError("unknown sender")
    result = await messages.forward_message(
        "source", [10], "destination", send_as="sender", topic_id=7
    )
    assert "error" in result.lower()
    assert client.requests == client.legacy == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        errors.ChatAdminRequiredError(None),
        errors.ChatWriteForbiddenError(None),
        errors.SendAsPeerInvalidError(None),
        errors.TopicDeletedError(None),
    ],
)
async def test_api_rejection_never_changes_sender_or_topic(client, error):
    client.error = error
    result = await messages.forward_message(
        "source", [10], "destination", send_as="sender", topic_id=7
    )
    assert "error" in result.lower()
    assert len(client.requests) == 1
    assert client.requests[0].send_as is client.peers["sender"]
    assert client.requests[0].top_msg_id == 7
    assert client.legacy == []


@pytest.mark.asyncio
async def test_discovery_failure_is_not_an_empty_allowed_list(client):
    client.error = errors.ChatAdminRequiredError(None)
    result = await messages.get_send_as("destination")
    assert "error" in result.lower()
    assert '"results"' not in result
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_empty_discovery_is_a_real_empty_result(client):
    client.result = types.channels.SendAsPeers([], [], [])
    result = json.loads(await messages.get_send_as("destination"))
    assert result["results"] == []
    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_schema_exposes_optional_routing_and_readonly_discovery():
    tools = {tool.name: tool for tool in await runtime.mcp.list_tools()}
    schema = tools["forward_message"].inputSchema
    assert set(schema["required"]) == {"from_chat_id", "message_id", "to_chat_id"}
    for name in ["topic_id", "send_as"]:
        assert schema["properties"][name]["default"] is None
        assert {"type": "null"} in schema["properties"][name]["anyOf"]
    for name in ["drop_author", "silent"]:
        assert schema["properties"][name]["default"] is False
    discovery = tools["get_send_as"]
    assert discovery.annotations.readOnlyHint is True
    assert discovery.inputSchema["required"] == ["chat_id"]


@pytest.mark.asyncio
async def test_discovery_readonly_account_fanout(client, monkeypatch):
    client.result = types.channels.SendAsPeers([], [], [])
    monkeypatch.setattr(runtime, "clients", {"first": client, "second": client})
    result = await messages.get_send_as("destination")
    assert "[first]" in result and "[second]" in result
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_native_forward_reports_request_correlated_destination_ids(client):
    def updates(request):
        first, second = request.random_id
        return [
            types.UpdateMessageID(id=999, random_id=first ^ second ^ 1),  # unrelated request
            types.UpdateMessageID(id=701, random_id=second),  # out of order
            types.UpdateNewMessage(  # never inferred from
                message=types.Message(id=555, peer_id=types.PeerUser(22), message="other"),
                pts=1,
                pts_count=1,
            ),
            types.UpdateMessageID(id=700, random_id=first),
        ]

    client.build_updates = updates
    result = await messages.forward_message("source", [10, 12], "destination", topic_id=7)
    assert result == (
        "2 messages forwarded from source to destination. Destination message IDs: [700, 701]."
    )
    assert client.legacy == []


@pytest.mark.asyncio
async def test_native_forward_reports_only_ids_telegram_returned(client):
    def updates(request):
        return [types.UpdateMessageID(id=701, random_id=request.random_id[1])]

    client.build_updates = updates
    result = await messages.forward_message("source", [10, 12], "destination", silent=True)
    assert result.endswith("Destination message IDs: [701].")


@pytest.mark.asyncio
async def test_native_forward_states_when_no_ids_were_returned(client):
    def updates(request):
        return [types.UpdateMessageID(id=999, random_id=request.random_id[0] ^ 1)]

    client.build_updates = updates
    result = await messages.forward_message("source", 10, "destination", drop_author=True)
    assert result == (
        "Message 10 forwarded from source to destination. "
        "Destination message IDs: not returned by Telegram."
    )
