"""Offline tests for get_contact_chats username lookup (Issue #297)."""

import json
from types import SimpleNamespace

import pytest
from telethon.tl.types import User

from telegram_mcp.tools import contacts


def _alice():
    return User(id=999, first_name="Alice", last_name="Smith", username="alice_smith")


class FakeContactsClient:
    """Async Telethon client stub: get_dialogs + get_common_chats only."""

    def __init__(self, dialogs=None):
        self._dialogs = list(dialogs or [])

    async def get_dialogs(self, **kwargs):
        return list(self._dialogs)

    async def get_common_chats(self, entity, **kwargs):
        return []


@pytest.mark.asyncio
async def test_get_contact_chats_finds_direct_chat_by_username(monkeypatch):
    """Issue #297: username lookup must match the dialog via the resolved contact id."""
    alice = _alice()
    client = FakeContactsClient(
        dialogs=[SimpleNamespace(entity=alice, unread_count=2)],
    )

    async def fake_resolve(contact_id, cl):
        return alice

    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "resolve_entity", fake_resolve)

    result = await contacts.get_contact_chats(contact_id="@alice_smith")
    data = json.loads(result)

    assert data["results"][0]["chat_id"] == 999
    assert data["results"][0]["type"] == "Private"
    assert data["results"][0]["unread"] == 2
    assert data["contact_id"] == "@alice_smith"


@pytest.mark.asyncio
async def test_get_contact_chats_by_numeric_id_still_works(monkeypatch):
    """Numeric lookup keeps working and must not report a duplicate direct chat."""
    alice = _alice()
    client = FakeContactsClient(
        dialogs=[SimpleNamespace(entity=alice, unread_count=0)],
    )

    async def fake_resolve(contact_id, cl):
        return alice

    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "resolve_entity", fake_resolve)

    result = await contacts.get_contact_chats(contact_id=999)
    data = json.loads(result)

    assert data["results"][0]["chat_id"] == 999
    assert data["results"][0]["type"] == "Private"
    assert "unread" not in data["results"][0]
