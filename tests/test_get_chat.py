import datetime
import json
from types import SimpleNamespace

import pytest

from telegram_mcp.tools import chats

# Distinct sentinel returned by get_input_entity so the test can assert the
# GetPeerDialogsRequest was built for exactly the resolved peer.
FAKE_INPUT_PEER = object()


class FakeChatClient:
    """Client stub whose per-peer methods let us assert get_chat reads the
    REQUESTED chat's dialog, not the account's top dialog.

    Regression guard: get_chat used get_dialogs(limit=1, offset_peer=entity),
    where Telethon's `offset_peer` is a pagination cursor (not a filter). With
    offset_id=0 it is effectively ignored, so limit=1 returned the account's
    top dialog and mis-attributed its unread/archived/last_message to the
    requested chat.
    """

    def __init__(self, entity, last_message, *, unread=0, folder_id=0):
        self.entity = entity
        self._last_message = last_message
        self._unread = unread
        self._folder_id = folder_id
        self.peer_dialog_peers = None
        self.get_messages_calls = []
        self.get_dialogs_called = False

    async def get_participants(self, entity, limit=0):
        return SimpleNamespace(total=9)

    async def get_input_entity(self, entity):
        assert entity is self.entity
        return FAKE_INPUT_PEER

    async def __call__(self, request):
        # functions.messages.GetPeerDialogsRequest for exactly the requested peer
        self.peer_dialog_peers = [p.peer for p in request.peers]
        return SimpleNamespace(
            dialogs=[SimpleNamespace(unread_count=self._unread, folder_id=self._folder_id)]
        )

    async def get_messages(self, entity, limit=1):
        self.get_messages_calls.append({"entity": entity, "limit": limit})
        return [self._last_message]

    async def get_dialogs(self, *args, **kwargs):
        self.get_dialogs_called = True
        raise AssertionError(
            "get_chat must not use get_dialogs(offset_peer=...) — it returns the "
            "account's top dialog, not the requested chat"
        )


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _patch(monkeypatch, client, entity):
    monkeypatch.setattr(chats, "get_client", lambda account=None: client)
    monkeypatch.setattr(chats, "resolve_entity", _async_return(entity))
    monkeypatch.setattr(chats, "get_marked_id", lambda e: -1002929916934)
    monkeypatch.setattr(chats, "get_entity_type", lambda e: "Supergroup")


def _parse(result):
    return json.loads(result.split("\n\n")[0])


@pytest.mark.asyncio
async def test_get_chat_reads_requested_peer_not_top_dialog(monkeypatch):
    entity = SimpleNamespace(title="Технический Мониторинг", username=None)
    last_msg = SimpleNamespace(
        date=datetime.datetime(2026, 7, 19, 1, 0, 0, tzinfo=datetime.timezone.utc),
        message="NetBird mesh alert",
        sender=SimpleNamespace(first_name="cobalt_quartz_bot", last_name=None, title=None),
    )
    client = FakeChatClient(entity, last_msg, unread=5, folder_id=0)
    _patch(monkeypatch, client, entity)

    result = await chats.get_chat(chat_id=-1002929916934, account=None)
    payload = _parse(result)

    assert "GEN-ERR" not in result
    # last_message comes from the requested chat, not a foreign top dialog
    assert payload["last_message"]["sender"] == "cobalt_quartz_bot"
    assert payload["last_message"]["text"] == "NetBird mesh alert"
    assert payload["unread"] == 5
    assert payload["archived"] is False
    # resolved via GetPeerDialogsRequest for exactly this peer, never get_dialogs
    assert client.peer_dialog_peers == [FAKE_INPUT_PEER]
    assert client.get_messages_calls == [{"entity": entity, "limit": 1}]
    assert client.get_dialogs_called is False


@pytest.mark.asyncio
async def test_get_chat_marks_archived_from_folder_id(monkeypatch):
    entity = SimpleNamespace(title="Archived Group", username=None)
    last_msg = SimpleNamespace(
        date=datetime.datetime(2026, 7, 19, 1, 0, 0, tzinfo=datetime.timezone.utc),
        message="hi",
        sender=SimpleNamespace(first_name="Ada", last_name="Lovelace", title=None),
    )
    client = FakeChatClient(entity, last_msg, unread=0, folder_id=1)
    _patch(monkeypatch, client, entity)

    payload = _parse(await chats.get_chat(chat_id=-100777, account=None))

    assert payload["archived"] is True
    assert payload["last_message"]["sender"] == "Ada Lovelace"
