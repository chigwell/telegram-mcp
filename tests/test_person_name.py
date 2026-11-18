"""Users without a last name (or deleted accounts) must not show up as "Ivan None"."""

import json
from types import SimpleNamespace

import pytest
from telethon import types

from telegram_mcp.runtime import get_sender_name, person_name
from telegram_mcp.tools import contacts

IVAN = types.User(id=101, first_name="Иван", access_hash=1, phone="79990000001")
MARIA = types.User(id=102, first_name="Мария", last_name="Смирнова", access_hash=2)
DELETED = types.User(id=104, deleted=True, access_hash=4)


@pytest.mark.parametrize(
    "user, expected",
    [(IVAN, "Иван"), (MARIA, "Мария Смирнова"), (DELETED, "Deleted Account")],
)
def test_person_name_skips_missing_parts(user, expected):
    assert person_name(user) == expected
    assert get_sender_name(SimpleNamespace(sender=user)) == expected


@pytest.mark.asyncio
async def test_list_contacts_has_no_none_names(monkeypatch):
    async def client(request):
        return types.contacts.Contacts(contacts=[], saved_count=0, users=[IVAN, MARIA, DELETED])

    async def connected(cl=None):
        return None

    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "ensure_connected", connected)

    result = json.loads(await contacts.list_contacts())

    assert [r["name"] for r in result["results"]] == ["Иван", "Мария Смирнова", "Deleted Account"]
