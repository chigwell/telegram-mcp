"""Folder edits preserve flags, peer order, and their no-op behavior."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import folders

PEER = types.InputPeerUser(42, 123)
OTHER = types.InputPeerUser(43, 456)


def make_folder(shared, included=False):
    common = dict(
        id=3,
        title=types.TextWithEntities("Folder", []),
        pinned_peers=[PEER] if included else [],
        include_peers=[OTHER, PEER] if included else [OTHER],
        emoticon="📁",
        title_noanimate=True,
        color=4,
    )
    if shared:
        return types.DialogFilterChatlist(**common)
    return types.DialogFilter(
        **common,
        exclude_peers=[types.InputPeerUser(44, 789)],
        contacts=True,
        non_contacts=False,
        groups=True,
        broadcasts=True,
        bots=False,
        exclude_muted=True,
        exclude_read=True,
        exclude_archived=False,
    )


class RecordingClient:
    def __init__(self, folder):
        self.folder = folder
        self.requests = []

    async def __call__(self, request):
        bytes(request)
        self.requests.append(request)
        return SimpleNamespace(filters=[self.folder])


def install_client(monkeypatch, folder):
    client = RecordingClient(folder)
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    monkeypatch.setattr(folders, "resolve_input_entity", AsyncMock(return_value=PEER))
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("operation", ["add", "remove"])
async def test_updates_preserve_every_unmodified_field(monkeypatch, shared, operation):
    original = make_folder(shared, included=operation == "remove")
    before = original.to_dict()
    client = install_client(monkeypatch, original)
    if operation == "add":
        result = await folders.add_chat_to_folder(3, 42, pinned=True)
        assert result == "Chat 42 added to folder 3 (pinned)."
    else:
        result = await folders.remove_chat_from_folder(3, 42)
        assert result == "Chat 42 removed from folder 3."
    assert len(client.requests) == 2
    assert isinstance(client.requests[0], functions.messages.GetDialogFiltersRequest)
    request = client.requests[1]
    assert isinstance(request, functions.messages.UpdateDialogFilterRequest)
    assert request.id == 3
    expected = make_folder(shared, included=operation == "add")
    assert request.filter.to_dict() == expected.to_dict()
    assert bytes(request) == bytes(functions.messages.UpdateDialogFilterRequest(3, expected))
    assert original.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("operation", ["add", "remove"])
async def test_noop_does_not_update_folder(monkeypatch, shared, operation):
    client = install_client(monkeypatch, make_folder(shared, included=operation == "add"))
    if operation == "add":
        result = await folders.add_chat_to_folder(3, 42, pinned=True)
        assert result == "Chat 42 is already in folder 3."
    else:
        result = await folders.remove_chat_from_folder(3, 42)
        assert result == "Chat 42 was not in folder 3."
    assert len(client.requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [folders.add_chat_to_folder, folders.remove_chat_from_folder])
async def test_missing_folder_never_resolves_or_writes(monkeypatch, tool):
    client = install_client(monkeypatch, make_folder(False))
    assert await tool(99, 42) == (
        "Folder with ID 99 not found. Use list_folders to see available folders."
    )
    folders.resolve_input_entity.assert_not_awaited()
    assert len(client.requests) == 1
