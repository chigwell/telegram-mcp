"""Folder creation uses account-specific app config, with server fallback."""

import asyncio
from types import SimpleNamespace

import pytest
from telethon import types
from telethon.errors.rpcerrorlist import BadRequestError

from telegram_mcp.tools import folders


def app_config(default=7, premium=23):
    return types.help.AppConfig(
        hash=123,
        config=types.JsonObject(
            value=[
                types.JsonObjectValue(key=key, value=types.JsonNumber(value=value))
                for key, value in (
                    ("dialog_filters_limit_default", default),
                    ("dialog_filters_limit_premium", premium),
                )
            ]
        ),
    )


class FakeClient:
    def __init__(self, count, premium=False, config=None, error=None, shared=False):
        self.count = count
        self.premium = premium
        self.config = app_config() if config is None else config
        self.error = error
        self.shared = shared
        self.updates = []

    async def get_me(self):
        if isinstance(self.premium, BaseException):
            raise self.premium
        if self.premium is None:
            return None
        return SimpleNamespace(premium=self.premium)

    async def __call__(self, request):
        if isinstance(request, folders.functions.messages.GetDialogFiltersRequest):
            filters = [types.DialogFilterDefault()]
            for i in range(self.count):
                cls = types.DialogFilterChatlist if self.shared else types.DialogFilter
                kwargs = {} if self.shared else {"exclude_peers": []}
                filters.append(
                    cls(
                        id=2 + i,
                        title=types.TextWithEntities(text=f"Folder {i}", entities=[]),
                        pinned_peers=[],
                        include_peers=[],
                        **kwargs,
                    )
                )
            return SimpleNamespace(filters=filters)
        if isinstance(request, folders.functions.help.GetAppConfigRequest):
            assert request.hash == 0
            if isinstance(self.config, BaseException):
                raise self.config
            return self.config
        if isinstance(request, folders.functions.messages.UpdateDialogFilterRequest):
            self.updates.append(request)
            if self.error:
                raise BadRequestError(request=request, message=self.error)
            return True
        raise AssertionError(f"Unexpected request: {type(request).__name__}")


@pytest.mark.asyncio
@pytest.mark.parametrize("premium,limit", [(False, 7), (True, 23)])
@pytest.mark.parametrize("extra", [0, 1])
@pytest.mark.parametrize("shared", [False, True])
async def test_precheck_blocks_at_configured_limit(monkeypatch, premium, limit, extra, shared):
    client = FakeClient(limit + extra, premium=premium, shared=shared)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)

    async def unexpected_resolution(*args):
        pytest.fail("Over-limit creation must stop before resolving chats")

    monkeypatch.setattr(folders, "resolve_input_entity", unexpected_resolution)
    result = await folders.create_folder(title="Too many", chat_ids=[123])
    assert "Cannot create folder" in result
    assert f"{limit}" in result
    assert "Premium" in result if premium else "regular" in result
    assert not client.updates


@pytest.mark.asyncio
@pytest.mark.parametrize("premium,count", [(False, 6), (True, 22), (True, 10)])
async def test_under_limit_creates_and_excludes_system_folder(monkeypatch, premium, count):
    client = FakeClient(count, premium=premium)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    result = await folders.create_folder(title="Allowed")
    assert '"success": true' in result
    assert len(client.updates) == 1
    assert client.updates[0].id == count + 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        RuntimeError("unavailable"),
        types.help.AppConfigNotModified(),
        types.help.AppConfig(hash=1, config=types.JsonObject(value=[])),
        types.help.AppConfig(hash=1, config=types.JsonNull()),
    ],
)
async def test_unavailable_config_falls_back_to_server(monkeypatch, config):
    client = FakeClient(40, config=config)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    assert '"success": true' in await folders.create_folder(title="Fallback")
    assert len(client.updates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, 2.5, float("nan"), float("inf"), "7", True])
async def test_invalid_limit_falls_back_to_server(monkeypatch, value):
    config = app_config()
    config.config.value[0].value = (
        types.JsonBool(value)
        if isinstance(value, bool)
        else types.JsonString(value) if isinstance(value, str) else types.JsonNumber(value)
    )
    client = FakeClient(40, config=config)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    assert '"success": true' in await folders.create_folder(title="Fallback")
    assert len(client.updates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("premium", [RuntimeError("unavailable"), None])
async def test_unknown_account_status_falls_back_to_server(monkeypatch, premium):
    client = FakeClient(40, premium=premium)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    assert '"success": true' in await folders.create_folder(title="Fallback")
    assert len(client.updates) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [app_config(), RuntimeError("unavailable")])
async def test_server_rejection_handles_race_or_missing_config(monkeypatch, config):
    client = FakeClient(6, config=config, error="DIALOG_FILTERS_TOO_MUCH")
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    result = await folders.create_folder(title="Race")
    assert "Cannot create folder" in result
    assert "folder limit" in result
    assert "10 for regular accounts, 20 for Premium" not in result
    assert len(client.updates) == 1


@pytest.mark.asyncio
async def test_unrelated_server_error_uses_existing_error_handler(monkeypatch):
    client = FakeClient(6, error="FILTER_TITLE_EMPTY")
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    monkeypatch.setattr(folders, "log_and_format_error", lambda *args, **kwargs: "handled error")
    assert await folders.create_folder(title="Bad") == "handled error"


@pytest.mark.asyncio
async def test_precheck_uses_selected_account(monkeypatch):
    clients = {"regular": FakeClient(7), "premium": FakeClient(7, premium=True)}
    monkeypatch.setattr(folders, "get_client", lambda account=None: clients[account])
    assert "Cannot create folder" in await folders.create_folder(title="Full", account="regular")
    assert '"success": true' in await folders.create_folder(title="Allowed", account="premium")


@pytest.mark.asyncio
async def test_config_cancellation_is_not_swallowed(monkeypatch):
    client = FakeClient(6, config=asyncio.CancelledError())
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)
    with pytest.raises(asyncio.CancelledError):
        await folders.create_folder(title="Cancelled")
    assert not client.updates
