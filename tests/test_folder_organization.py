"""MCP-only folder planning and reversible private-folder updates (synthetic data)."""

import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from telethon import functions, types
from telethon.errors import BadRequestError

from telegram_mcp.tools import folders


def private_folder(**overrides):
    values = dict(
        id=2,
        title=types.TextWithEntities("Original", []),
        emoticon="📁",
        color=3,
        pinned_peers=[types.InputPeerUser(1, 101)],
        include_peers=[types.InputPeerUser(2, 202)],
        exclude_peers=[types.InputPeerChannel(3, 303)],
        contacts=True,
        non_contacts=False,
        groups=True,
        broadcasts=False,
        bots=True,
        exclude_muted=True,
        exclude_read=True,
        exclude_archived=True,
        title_noanimate=True,
    )
    values.update(overrides)
    return types.DialogFilter(**values)


def config(chats=3, pinned=2):
    return types.help.AppConfig(
        hash=1,
        config=types.JsonObject(
            [
                types.JsonObjectValue(key, types.JsonNumber(value))
                for key, value in {
                    "dialog_filters_limit_default": 7,
                    "dialog_filters_limit_premium": 23,
                    "dialog_filters_chats_limit_default": chats,
                    "dialog_filters_chats_limit_premium": chats + 2,
                    "dialogs_folder_pinned_limit_default": pinned,
                    "dialogs_folder_pinned_limit_premium": pinned + 2,
                }.items()
            ]
        ),
    )


class Client:
    def __init__(self, definition=None, premium=False, app_config=None):
        self.filters = [types.DialogFilterDefault(), definition or private_folder()]
        self.premium = premium
        self.config = config() if app_config is None else app_config
        self.requests = []
        self.reject = False
        self.on_resolve = None

    async def get_me(self):
        if isinstance(self.premium, BaseException):
            raise self.premium
        return SimpleNamespace(id=99, premium=self.premium)

    async def get_input_entity(self, identifier):
        if self.on_resolve:
            self.on_resolve()
        return types.InputPeerUser(identifier, 999)

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.messages.GetDialogFiltersRequest):
            return SimpleNamespace(filters=copy.deepcopy(self.filters), tags_enabled=True)
        if isinstance(request, functions.help.GetAppConfigRequest):
            if isinstance(self.config, BaseException):
                raise self.config
            return self.config
        if isinstance(request, functions.messages.UpdateDialogFilterRequest):
            assert request.filter is not None, "Folder deletion is forbidden"
            if self.reject:
                raise BadRequestError(request, "FILTER_INCLUDE_EMPTY")
            self.filters[1] = copy.deepcopy(request.filter)
            return True
        pytest.fail("Unexpected RPC: " + type(request).__name__)

    @property
    def updates(self):
        return [
            r for r in self.requests if isinstance(r, functions.messages.UpdateDialogFilterRequest)
        ]


@pytest.fixture
def client(monkeypatch):
    cl = Client()
    monkeypatch.setattr(folders, "get_client", lambda account=None: cl)

    async def connected(*args):
        pass

    async def resolve(identifier, cl):
        return await cl.get_input_entity(identifier)

    monkeypatch.setattr(folders, "ensure_connected", connected)
    monkeypatch.setattr(folders, "resolve_input_entity", resolve)
    return cl


def parsed(result):
    return json.loads(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("premium,folder_limit,chat_limit", [(False, 7, 3), (True, 23, 5)])
async def test_limits_exposes_effective_tier(client, premium, folder_limit, chat_limit):
    client.premium = premium
    result = parsed(await folders.get_folder_limits())
    assert result["premium"] is premium
    assert result["limits"]["folders"] == folder_limit
    assert result["limits"]["chats_per_folder"] == chat_limit
    assert result["title_limit_utf16"] == 12
    assert not client.updates


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["account", "config", "not_modified", "missing"])
async def test_limits_reports_unknown_without_invented_defaults(client, failure):
    if failure == "account":
        client.premium = RuntimeError("unavailable")
    elif failure == "config":
        client.config = RuntimeError("unavailable")
    elif failure == "not_modified":
        client.config = types.help.AppConfigNotModified()
    else:
        client.config = types.help.AppConfig(1, types.JsonObject([]))
    result = parsed(await folders.get_folder_limits())
    assert result["limits"]["folders"] is None
    assert result["limits"]["chats_per_folder"] is None
    assert not client.updates


@pytest.mark.asyncio
async def test_snapshot_contains_complete_definitions_and_order_without_access_hashes(client):
    client.filters[1].title = types.TextWithEntities(
        "🙂", [types.MessageEntityCustomEmoji(0, 2, 123)]
    )
    client.filters.append(
        types.DialogFilterChatlist(
            5,
            types.TextWithEntities("Shared", []),
            [],
            [],
            has_my_invites=True,
            color=4,
            title_noanimate=True,
        )
    )
    result = parsed(await folders.get_folder_snapshot())
    assert result["folder_order"] == [0, 2, 5]
    assert result["tags_enabled"] is True
    private = result["folders"][1]
    patch = private["definition"]
    assert patch["title"] == "🙂"
    assert patch["title_entities"][0]["document_id"] == 123
    assert patch["color"] == 3 and patch["title_noanimate"] is True
    assert patch["pinned_chat_ids"] == [1] and patch["exclude_chat_ids"] == [-1000000000003]
    assert all(flag in patch for flag in folders._FOLDER_FLAGS)
    assert result["folders"][2]["has_my_invites"] is True
    assert "access_hash" not in json.dumps(result)
    assert not client.updates


@pytest.mark.asyncio
async def test_snapshot_resolves_self_to_stable_id(client):
    client.filters[1].include_peers = [types.InputPeerSelf()]
    result = parsed(await folders.get_folder_snapshot())
    assert result["folders"][1]["definition"]["include_chat_ids"] == [99]


@pytest.mark.asyncio
async def test_partial_patch_preserves_unspecified_fields_and_target_id(client):
    before = client.filters[1].to_dict()
    result = parsed(await folders.update_folder(2, {"title": "New"}))
    assert result["success"] is True
    after = client.updates[0].filter.to_dict()
    before["title"] = types.TextWithEntities("New", []).to_dict()
    assert after == before
    assert client.updates[0].id == 2


@pytest.mark.asyncio
async def test_membership_patch_preserves_pins_excludes_flags(client):
    original = copy.deepcopy(client.filters[1])
    await folders.update_folder(2, {"include_chat_ids": [7, 8]})
    actual = client.updates[0].filter
    assert [p.user_id for p in actual.include_peers] == [7, 8]
    assert actual.pinned_peers == original.pinned_peers
    assert actual.exclude_peers == original.exclude_peers
    assert actual.exclude_archived is True


@pytest.mark.asyncio
async def test_archived_inclusion_only_changes_filter_rule(client):
    result = parsed(await folders.update_folder(2, {"exclude_archived": False}))
    assert result["success"] is True
    assert client.updates[0].filter.exclude_archived is False
    assert all(
        isinstance(
            r,
            (
                functions.messages.GetDialogFiltersRequest,
                functions.help.GetAppConfigRequest,
                functions.messages.UpdateDialogFilterRequest,
            ),
        )
        for r in client.requests
    )


@pytest.mark.asyncio
async def test_snapshot_definition_restores_private_folder_state(client):
    original = client.filters[1].to_dict()
    snapshot = parsed(await folders.get_folder_snapshot())["folders"][1]
    await folders.update_folder(
        2, {"title": "Changed", "include_chat_ids": [9], "exclude_archived": False}
    )
    result = parsed(await folders.update_folder(2, snapshot["definition"]))
    assert result["success"] is True
    restored = client.updates[-1].filter.to_dict()
    # Access hashes may be refreshed by resolution; compare stable peer IDs/state.
    for key in ("include_peers", "pinned_peers", "exclude_peers"):
        for peer in restored[key]:
            peer.pop("access_hash", None)
        for peer in original[key]:
            peer.pop("access_hash", None)
    assert restored == original


@pytest.mark.asyncio
@pytest.mark.parametrize("id_value", [0, 1, -1, True, 2**31, "2"])
async def test_invalid_folder_ids_never_write(client, id_value):
    assert "Error" in await folders.update_folder(id_value, {"title": "New"})
    assert not client.requests


@pytest.mark.asyncio
async def test_missing_and_shared_folders_refused(client):
    assert "not found" in await folders.update_folder(9, {"title": "New"})
    client.filters[1] = types.DialogFilterChatlist(2, types.TextWithEntities("Shared", []), [], [])
    assert "shared" in (await folders.update_folder(2, {"title": "New"})).lower()
    assert not client.updates


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["", " " * 3, "x" * 13, "😀" * 7])
async def test_title_validation_before_rpc(client, title):
    assert "Error" in await folders.update_folder(2, {"title": title})
    assert not client.requests


@pytest.mark.asyncio
async def test_title_utf16_boundary(client):
    assert parsed(await folders.update_folder(2, {"title": "😀" * 6}))["success"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"unknown": True},
        {"include_chat_ids": [0]},
        {"include_chat_ids": [True]},
        {"include_chat_ids": [2**63]},
        {"include_chat_ids": [1, 1]},
        {"color": 8},
        {"exclude_archived": "false"},
        {"title_entities": [{"_": "GetDialogFiltersRequest"}]},
    ],
)
async def test_invalid_patch_never_writes(client, patch):
    assert "Error" in await folders.update_folder(2, patch)
    assert not client.updates


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "premium,count,allowed",
    [(False, 2, True), (False, 3, False), (True, 4, True), (True, 5, False)],
)
async def test_explicit_peer_limit_counts_pinned_union(client, premium, count, allowed):
    client.premium = premium
    result = await folders.update_folder(2, {"include_chat_ids": list(range(10, 10 + count))})
    assert bool(client.updates) is allowed
    if not allowed:
        assert "limit" in result


@pytest.mark.asyncio
async def test_pinned_overlap_not_double_counted_and_exclusions_limited_separately(client):
    assert parsed(await folders.update_folder(2, {"include_chat_ids": [1, 2, 3]}))["success"]
    client.requests.clear()
    assert "limit" in await folders.update_folder(2, {"exclude_chat_ids": [4, 5, 6, 7]})
    assert not client.updates


@pytest.mark.asyncio
async def test_config_failure_retains_server_validation(client):
    client.config = RuntimeError("unavailable")
    assert parsed(await folders.update_folder(2, {"include_chat_ids": list(range(10, 20))}))[
        "success"
    ]
    client.reject = True
    assert "success" not in await folders.update_folder(2, {"title": "Failed"})


@pytest.mark.asyncio
async def test_expected_revision_refuses_stale_plan(client):
    before = parsed(await folders.get_folder_snapshot())["folders"][1]
    client.filters[1].title = types.TextWithEntities("External", [])
    assert "changed" in await folders.update_folder(
        2, {"title": "New"}, expected_revision=before["revision"]
    )
    assert not client.updates


@pytest.mark.asyncio
async def test_refetch_detects_changes_during_resolution(client):
    client.on_resolve = lambda: setattr(client.filters[1], "exclude_archived", False)
    assert "changed" in await folders.update_folder(2, {"include_chat_ids": [9]})
    assert not client.updates


@pytest.mark.asyncio
async def test_cancellation_propagates(client):
    client.config = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await folders.get_folder_limits()


@pytest.mark.asyncio
async def test_tools_registered_with_correct_readonly_annotations():
    tools = {t.name: t for t in await folders.mcp.list_tools()}
    assert tools["get_folder_limits"].annotations.readOnlyHint is True
    assert tools["get_folder_snapshot"].annotations.readOnlyHint is True
    assert tools["update_folder"].annotations.readOnlyHint is not True
    assert tools["update_folder"].annotations.idempotentHint is True
    assert "patch" in tools["update_folder"].inputSchema["properties"]


@pytest.mark.asyncio
async def test_noop_patch_does_not_write(client):
    snapshot = parsed(await folders.get_folder_snapshot())["folders"][1]
    result = parsed(await folders.update_folder(2, snapshot["definition"], snapshot["revision"]))
    assert result["changed"] is False
    assert not client.updates


@pytest.mark.asyncio
async def test_snapshot_is_complete_or_errors_on_unknown_self_identity(client):
    client.filters[1].include_peers = [types.InputPeerSelf()]
    client.premium = RuntimeError("unavailable")
    result = await folders.get_folder_snapshot()
    assert "schema_version" not in result
    assert not client.updates


@pytest.mark.asyncio
async def test_allowlist_does_not_leak_complete_snapshot_or_update(client, monkeypatch):
    monkeypatch.setattr(folders, "is_chat_allowlist_enabled", lambda: True)
    assert "allowlist" in await folders.get_folder_snapshot()
    assert "allowlist" in await folders.update_folder(2, {"title": "New"})
    assert not client.requests


@pytest.mark.asyncio
async def test_pinned_count_limit_and_include_exclude_overlap(client):
    assert "limit" in await folders.update_folder(2, {"pinned_chat_ids": [1, 4, 5]})
    assert "overlap" in await folders.update_folder(2, {"exclude_chat_ids": [1]})
    assert not client.updates


@pytest.mark.asyncio
async def test_empty_inclusion_rejected_without_deleting(client):
    patch = {flag: False for flag in ("contacts", "non_contacts", "groups", "broadcasts", "bots")}
    patch.update(include_chat_ids=[], pinned_chat_ids=[])
    assert "inclusion rule" in await folders.update_folder(2, patch)
    assert not client.updates


@pytest.mark.asyncio
async def test_invalid_title_entity_bounds(client):
    result = await folders.update_folder(
        2,
        {
            "title": "🙂",
            "title_entities": [
                {"_": "MessageEntityCustomEmoji", "offset": 1, "length": 2, "document_id": 123}
            ],
        },
    )
    assert "UTF-16" in result
    assert not client.updates


@pytest.mark.asyncio
async def test_restore_title_entities_and_clear_display_options(client):
    original = client.filters[1].to_dict()
    patch = {
        "title": "🙂",
        "title_entities": [
            {"_": "MessageEntityCustomEmoji", "offset": 0, "length": 2, "document_id": 123}
        ],
        "emoticon": None,
        "color": None,
        "title_noanimate": False,
    }
    result = parsed(await folders.update_folder(2, patch))
    assert result["success"]
    actual = client.updates[0].filter
    assert actual.title.entities[0].document_id == 123
    assert actual.color is None and actual.emoticon is None
    assert actual.title_noanimate is False
    assert actual.include_peers[0].to_dict() == original["include_peers"][0]


@pytest.mark.asyncio
async def test_membership_resolution_refuses_wrong_peer_identity(client, monkeypatch):
    async def wrong(*args):
        return types.InputPeerChat(9)

    monkeypatch.setattr(folders, "resolve_input_entity", wrong)
    assert "identity" in await folders.update_folder(2, {"include_chat_ids": [9]})
    assert not client.updates


@pytest.mark.asyncio
async def test_expected_revision_ignores_include_order_but_preserves_pin_order(client):
    client.filters[1].include_peers = [types.InputPeerUser(2, 202), types.InputPeerUser(4, 404)]
    original = parsed(await folders.get_folder_snapshot())["folders"][1]["revision"]
    client.filters[1].include_peers.reverse()
    assert parsed(await folders.update_folder(2, {"title": "New"}, original))["success"]
    client.filters[1].pinned_peers = [types.InputPeerUser(1, 101), types.InputPeerUser(5, 505)]
    original = parsed(await folders.get_folder_snapshot())["folders"][1]["revision"]
    client.filters[1].pinned_peers.reverse()
    assert "changed" in await folders.update_folder(2, {"title": "Newest"}, original)


@pytest.mark.asyncio
async def test_multi_account_reads_and_writes_route_selected_client(client, monkeypatch):
    second = Client(premium=True)
    monkeypatch.setattr(
        folders, "get_client", lambda account=None: second if account == "second" else client
    )
    assert parsed(await folders.get_folder_limits(account="second"))["premium"] is True
    assert parsed(await folders.update_folder(2, {"title": "Second"}, account="second"))["success"]
    assert not client.updates and len(second.updates) == 1


@pytest.mark.asyncio
async def test_saved_messages_can_be_added_by_stable_account_id(client, monkeypatch):
    async def saved_messages(*args):
        return types.InputPeerSelf()

    monkeypatch.setattr(folders, "resolve_input_entity", saved_messages)
    result = parsed(await folders.update_folder(2, {"include_chat_ids": [99]}))
    assert result["success"] is True
    assert isinstance(client.updates[0].filter.include_peers[0], types.InputPeerSelf)
    snapshot = parsed(await folders.get_folder_snapshot())
    assert snapshot["folders"][1]["definition"]["include_chat_ids"] == [99]
