"""Deterministic operation traces frozen before extracting adapter helpers."""

import copy
from types import SimpleNamespace

import pytest
from telethon import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import folders


def _peer(user_id):
    return types.InputPeerUser(user_id=user_id, access_hash=user_id * 10)


def _peer_dict(user_id):
    return {"_": "InputPeerUser", "user_id": user_id, "access_hash": user_id * 10}


def _folder(shared, operation):
    kwargs = {
        "id": 42,
        "title": types.TextWithEntities(
            text="Work", entities=[types.MessageEntityBold(offset=0, length=4)]
        ),
        "emoticon": "💼",
        "color": 4,
        "title_noanimate": True,
        "include_peers": [_peer(11)],
        "pinned_peers": [_peer(11)],
    }
    if operation == "remove":
        kwargs["include_peers"].append(_peer(99))
        kwargs["pinned_peers"].append(_peer(99))
    if shared:
        return types.DialogFilterChatlist(**kwargs)
    return types.DialogFilter(
        **kwargs,
        exclude_peers=[_peer(22)],
        contacts=True,
        non_contacts=False,
        groups=True,
        broadcasts=False,
        bots=True,
        exclude_muted=False,
        exclude_read=True,
        exclude_archived=False,
    )


def _expected_filter(shared, operation):
    peers = [_peer_dict(11)] + ([_peer_dict(99)] if operation == "add" else [])
    result = {
        "_": "DialogFilterChatlist" if shared else "DialogFilter",
        "id": 42,
        "title": {
            "_": "TextWithEntities",
            "text": "Work",
            "entities": [{"_": "MessageEntityBold", "offset": 0, "length": 4}],
        },
        "emoticon": "💼",
        "color": 4,
        "title_noanimate": True,
        "include_peers": peers,
        "pinned_peers": peers,
    }
    if shared:
        result["has_my_invites"] = None
    else:
        result.update(
            exclude_peers=[_peer_dict(22)],
            contacts=True,
            non_contacts=False,
            groups=True,
            broadcasts=False,
            bots=True,
            exclude_muted=False,
            exclude_read=True,
            exclude_archived=False,
        )
    return result


class _FolderClient:
    def __init__(self, folder):
        self.folder = folder
        self.trace = []

    async def __call__(self, request):
        self.trace.append((type(request).__name__, request.to_dict()))
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return SimpleNamespace(filters=[types.DialogFilterDefault(), self.folder])
        if isinstance(request, functions.messages.UpdateDialogFilterRequest):
            self.folder = request.filter
            return True
        pytest.fail(f"Unexpected RPC: {type(request).__name__}")


def _install_folder_client(monkeypatch, client, *, resolution_error=False):
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)

    async def resolve(identifier, selected_client):
        assert selected_client is client
        client.trace.append(("resolve_input_entity", identifier))
        if resolution_error:
            raise ValueError("unresolvable peer")
        return _peer(99)

    monkeypatch.setattr(folders, "resolve_input_entity", resolve)


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True], ids=["regular", "shared"])
@pytest.mark.parametrize("operation", ["add", "remove"])
async def test_folder_reconstruction_preserves_rpc_fields_and_is_idempotent(
    monkeypatch, shared, operation
):
    original = _folder(shared, operation)
    original_data = copy.deepcopy(original.to_dict())
    client = _FolderClient(original)
    _install_folder_client(monkeypatch, client)

    if operation == "add":
        first = await folders.add_chat_to_folder(folder_id=42, chat_id=99, pinned=True)
        second = await folders.add_chat_to_folder(folder_id=42, chat_id=99, pinned=True)
        assert first == "Chat 99 added to folder 42 (pinned)."
        assert second == "Chat 99 is already in folder 42."
    else:
        first = await folders.remove_chat_from_folder(folder_id=42, chat_id=99)
        second = await folders.remove_chat_from_folder(folder_id=42, chat_id=99)
        assert first == "Chat 99 removed from folder 42."
        assert second == "Chat 99 was not in folder 42."

    read = ("GetDialogFiltersRequest", {"_": "GetDialogFiltersRequest"})
    resolve = ("resolve_input_entity", 99)
    update = (
        "UpdateDialogFilterRequest",
        {
            "_": "UpdateDialogFilterRequest",
            "id": 42,
            "filter": _expected_filter(shared, operation),
        },
    )
    assert client.trace == [read, resolve, update, read, resolve]
    assert client.folder is not original
    assert original.to_dict() == original_data


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "remove"])
@pytest.mark.parametrize("missing_folder", [False, True])
async def test_folder_refusals_stop_before_any_update(monkeypatch, operation, missing_folder):
    client = _FolderClient(_folder(False, operation))
    _install_folder_client(monkeypatch, client, resolution_error=True)
    folder_id = 404 if missing_folder else 42
    tool = folders.add_chat_to_folder if operation == "add" else folders.remove_chat_from_folder

    result = await tool(folder_id=folder_id, chat_id=99)

    expected_trace = [("GetDialogFiltersRequest", {"_": "GetDialogFiltersRequest"})]
    if missing_folder:
        assert result == "Folder with ID 404 not found. Use list_folders to see available folders."
    else:
        expected_trace.append(("resolve_input_entity", 99))
        assert result == "Failed to resolve a requested chat."
    assert client.trace == expected_trace


@pytest.mark.asyncio
@pytest.mark.parametrize("getter", ["get_entity", "get_input_entity"])
async def test_resolver_reconnect_warm_and_both_marked_candidates_keep_order(monkeypatch, getter):
    trace = []
    failures = [ConnectionError(), ValueError(), ValueError(), ValueError()]

    class Client:
        async def get_dialogs(self):
            trace.append("get_dialogs")

    async def get(identifier):
        trace.append((getter, identifier))
        if failures:
            raise failures.pop(0)
        return "resolved basic group"

    async def ensure_connected(client):
        assert client is selected_client
        trace.append("ensure_connected")

    selected_client = Client()
    setattr(selected_client, getter, get)
    monkeypatch.setattr(runtime, "ensure_connected", ensure_connected)
    resolver = runtime.resolve_entity if getter == "get_entity" else runtime.resolve_input_entity

    assert await resolver(123, selected_client) == "resolved basic group"
    assert trace == [
        "ensure_connected",
        (getter, 123),
        "ensure_connected",
        (getter, 123),
        "get_dialogs",
        (getter, 123),
        (getter, -1000000000123),
        (getter, -123),
    ]
