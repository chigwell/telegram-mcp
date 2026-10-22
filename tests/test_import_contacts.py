"""import_contacts must build real TL objects and send contacts.importContacts."""

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import contacts


class ImportServer(TelegramClient):
    def __init__(self):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        self.requests.append(request)
        assert isinstance(request, functions.contacts.ImportContactsRequest)
        imported = [
            types.ImportedContact(user_id=100 + c.client_id, client_id=c.client_id)
            for c in request.contacts
        ]
        return types.contacts.ImportedContacts(
            imported=imported, popular_invites=[], retry_contacts=[], users=[]
        )


@pytest.fixture
def server(monkeypatch):
    client = ImportServer()

    async def connected(cl=None):
        return None

    monkeypatch.setattr(contacts, "get_client", lambda account=None: client)
    monkeypatch.setattr(contacts, "ensure_connected", connected)
    return client


@pytest.mark.asyncio
async def test_import_contacts_sends_the_contacts(server):
    result = await contacts.import_contacts(
        [
            {"phone": "+79990001122", "first_name": "Иван", "last_name": "Петров"},
            {"phone": "+79990003344", "first_name": "Мария"},
        ]
    )

    assert result == "Imported 2 contacts."
    (request,) = server.requests
    assert [(c.phone, c.first_name, c.last_name) for c in request.contacts] == [
        ("+79990001122", "Иван", "Петров"),
        ("+79990003344", "Мария", ""),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [{"first_name": "Иван"}, {"phone": "+79990001122"}, "+79990001122"],
    ids=["no-phone", "no-first-name", "not-a-dict"],
)
async def test_import_contacts_explains_a_malformed_contact(server, bad):
    result = await contacts.import_contacts([{"phone": "+7999", "first_name": "A"}, bad])

    assert result.startswith("Error: every contact needs")
    assert server.requests == []
