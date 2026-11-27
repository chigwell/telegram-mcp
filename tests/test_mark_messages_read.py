"""Check bounded read acknowledgements through Telethon's real history methods."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from telethon import TelegramClient, functions, types, utils
from telethon.sessions import StringSession

from telegram_mcp.tools import messages

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
MESSAGE_ACKS = (functions.messages.ReadHistoryRequest, functions.channels.ReadHistoryRequest)
PHOTO = types.ChatPhotoEmpty()


class ReadServer(TelegramClient):
    def __init__(self, chat):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.chat = chat
        self.chat_id = utils.get_peer_id(chat)
        self.peer = utils.get_peer(chat)
        self.cursor = 15
        self.history = {16: True, 17: False, 18: False}
        self.hidden_ids = set()
        self.manual_unread = False
        self.pending_on_write = {}
        self.read_requests = []
        self.dialog_reads = 0
        self.change_on_recheck = False
        self.apply_ack = True

    @property
    def unread_count(self):
        return sum(mid > self.cursor and not out for mid, out in self.history.items())

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        if isinstance(request, functions.messages.GetPeerDialogsRequest):
            assert utils.get_peer_id(request.peers[0].peer) == self.chat_id
            self.dialog_reads += 1
            if self.change_on_recheck and self.dialog_reads == 2:
                self.manual_unread = True
            dialog = types.Dialog(
                peer=self.peer,
                top_message=max(self.history),
                read_inbox_max_id=self.cursor,
                read_outbox_max_id=16,
                unread_count=self.unread_count,
                unread_mentions_count=0,
                unread_reactions_count=0,
                unread_poll_votes_count=0,
                notify_settings=types.PeerNotifySettings(),
                unread_mark=self.manual_unread,
            )
            return SimpleNamespace(dialogs=[dialog])
        if isinstance(request, functions.messages.GetHistoryRequest):
            assert utils.get_peer_id(request.peer) == self.chat_id
            records = [
                types.Message(id=mid, peer_id=self.peer, date=NOW, message="example", out=out)
                for mid, out in sorted(self.history.items(), reverse=True)
                if mid not in self.hidden_ids
                and (not request.offset_id or mid < request.offset_id)
            ]
            return types.messages.Messages(
                messages=records[: request.limit], topics=[], chats=[self.chat], users=[]
            )
        assert isinstance(request, MESSAGE_ACKS)
        self.read_requests.append(request)
        target = (
            request.channel
            if isinstance(request, functions.channels.ReadHistoryRequest)
            else request.peer
        )
        assert utils.get_peer_id(target) == self.chat_id
        self.history.update(self.pending_on_write)
        if self.apply_ack:
            self.cursor = max(self.cursor, request.max_id or max(self.history))
        return True


@pytest.fixture(params=["supergroup", "group"])
def client(request, monkeypatch):
    if request.param == "supergroup":
        chat = types.Channel(
            id=10, title="Team", photo=PHOTO, date=NOW, megagroup=True, access_hash=42
        )
    else:
        chat = types.Chat(
            id=10, title="Team", photo=PHOTO, participants_count=3, date=NOW, version=1
        )
    server = ReadServer(chat)

    async def resolve(chat_id, client):
        return chat

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.delenv("TELEGRAM_BLOCKED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: server)
    monkeypatch.setattr(messages, "resolve_entity", resolve)
    return server


async def mark(client, *ids, **kwargs):
    response = await messages.mark_messages_read(
        chat_id=client.chat_id, message_ids=list(ids), **kwargs
    )
    return json.loads(response)["results"][0]


@pytest.mark.asyncio
async def test_marks_exact_range_and_checks_readback(client):
    result = await mark(client, 18, 17)

    assert result == {
        "status": "marked_read",
        "chat_id": client.chat_id,
        "message_ids": [17, 18],
        "read_through": 18,
        "unread_before": 2,
        "unread_after": 0,
    }
    assert [request.max_id for request in client.read_requests] == [18]


@pytest.mark.asyncio
async def test_preserves_later_messages_including_concurrent_arrivals(client):
    client.history[19] = False
    client.pending_on_write[20] = False

    result = await mark(client, 17, 18)

    assert client.cursor == 18
    assert (result["unread_before"], result["unread_after"]) == (3, 2)


@pytest.mark.asyncio
async def test_unprocessed_earlier_message_blocks_acknowledgement(client):
    with pytest.raises(ToolError, match="Unread message 17"):
        await mark(client, 18)
    assert not client.read_requests


@pytest.mark.asyncio
async def test_incomplete_unread_history_blocks_acknowledgement(client):
    client.history.update({19: False, 20: False})
    client.hidden_ids.add(17)

    with pytest.raises(ToolError, match="unread history"):
        await mark(client, 18, 19)
    assert not client.read_requests


@pytest.mark.asyncio
async def test_dry_run_and_repeating_the_same_write(client):
    preview = await mark(client, 17, 18, dry_run=True)
    assert (preview["status"], preview["unread_after"]) == ("ready", 2)
    assert not client.read_requests

    await mark(client, 17, 18)
    repeated = await mark(client, 17, 18)
    assert repeated["status"] == "already_read"
    assert len(client.read_requests) == 1


@pytest.mark.asyncio
async def test_preserves_manual_unread_marker(client):
    client.change_on_recheck = True

    with pytest.raises(ToolError, match="changed during verification"):
        await mark(client, 17, 18)
    assert not client.read_requests


@pytest.mark.asyncio
async def test_failed_readback_is_not_success(client):
    client.apply_ack = False

    with pytest.raises(ToolError, match="did not confirm"):
        await mark(client, 17, 18)
    assert len(client.read_requests) == 1


@pytest.mark.asyncio
async def test_blocked_chat_is_not_read_or_written(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BLOCKED_CHAT_IDS", str(client.chat_id))

    with pytest.raises(ToolError, match="TELEGRAM_BLOCKED_CHAT_IDS"):
        await mark(client, 17, 18)
    assert not client.dialog_reads
    assert not client.read_requests


@pytest.mark.asyncio
async def test_legacy_method_still_marks_the_whole_chat(client):
    await messages.mark_as_read(chat_id=client.chat_id)

    assert client.unread_count == 0
    assert [request.max_id for request in client.read_requests] == [0]
