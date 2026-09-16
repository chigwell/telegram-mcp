"""Legacy forum adapters retain their original on-wire representation."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from telethon import types
from telethon.extensions import BinaryReader

from telegram_mcp.tools import chats

FIXTURE = Path(__file__).parent / "fixtures" / "forum_requests.json"


def request_samples(
    get_topics=chats.GetForumTopicsRequest, create_topic=chats.CreateForumTopicRequest
):
    for query in [None, "Topic 🧵"]:
        yield get_topics(types.InputChannel(11, 22), 0, 7, 9, 50, q=query)
    for options in [
        {},
        {"icon_color": 0, "icon_emoji_id": 0},
        {"icon_color": 0x123456, "icon_emoji_id": 999, "send_as": types.InputPeerUser(33, 44)},
    ]:
        yield create_topic(types.InputPeerChannel(11, 22), "Topic 🧵", 12345, **options)


def wire_contracts(requests):
    return [{"fields": request.to_dict(), "bytes": bytes(request).hex()} for request in requests]


def test_forum_request_bytes_match_original_adapters():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert wire_contracts(request_samples()) == expected


@pytest.mark.parametrize("rpc_request", list(request_samples()))
def test_forum_request_reader_preserves_all_fields(rpc_request):
    # The dispatcher normally consumes the constructor ID before from_reader.
    with BinaryReader(bytes(rpc_request)[4:]) as reader:
        restored = type(rpc_request).from_reader(reader)
    assert restored.to_dict() == rpc_request.to_dict()
    assert bytes(restored) == bytes(rpc_request)


@pytest.mark.asyncio
async def test_forum_request_resolution_preserves_peer_types():
    from telethon import utils

    client = AsyncMock()
    client.get_input_entity.return_value = types.InputPeerChannel(11, 22)
    get_topics = chats.GetForumTopicsRequest("channel", 0, 7, 9, 50)
    await get_topics.resolve(client, utils)
    assert get_topics.channel.to_dict() == types.InputChannel(11, 22).to_dict()
    client.get_input_entity.assert_awaited_once_with("channel")

    client.reset_mock()
    client.get_input_entity.side_effect = [
        types.InputPeerChannel(11, 22),
        types.InputPeerUser(33, 44),
    ]
    create_topic = chats.CreateForumTopicRequest("channel", "Topic", 12345, send_as="sender")
    await create_topic.resolve(client, utils)
    assert create_topic.peer.to_dict() == types.InputPeerChannel(11, 22).to_dict()
    assert create_topic.send_as.to_dict() == types.InputPeerUser(33, 44).to_dict()
    assert [call.args for call in client.get_input_entity.await_args_list] == [
        ("channel",),
        ("sender",),
    ]
