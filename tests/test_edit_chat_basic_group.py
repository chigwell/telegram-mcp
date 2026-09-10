"""Tests for edit_chat_title / edit_chat_photo / delete_chat_photo on basic groups.

Regression guard: the basic-group branches passed the raw ``chat_id`` argument
straight into ``messages.EditChatTitle`` / ``messages.EditChatPhoto``. Those
requests take the positive ``Chat.id``; the negative Bot-API-style id (or a
string) that MCP callers pass is rejected by Telegram — CHAT_ID_INVALID, or
repeated internal errors until Telethon gives up. The resolved entity's id must
be used, exactly as the channel branches already pass the resolved entity.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.tl import functions, types

from telegram_mcp.tools import groups

BASIC_GROUP_ID = 5317263588  # Telegram's positive Chat.id
BOT_API_ID = -BASIC_GROUP_ID  # the spelling MCP callers typically pass


class FakeClient:
    """Records every raw request and stubs the upload used by edit_chat_photo."""

    def __init__(self):
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace()

    async def upload_file(self, path):
        return SimpleNamespace(name=Path(path).name)


def _basic_group():
    return types.Chat(
        id=BASIC_GROUP_ID,
        title="Basic Group",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=None,
        version=1,
    )


def _patch(monkeypatch, client, entity):
    async def fake_resolve(identifier, cl):
        # Like telethon, any spelling of the id resolves to the same entity.
        return entity

    async def fake_readable_path(raw_path, ctx, tool_name):
        return Path(raw_path), None

    monkeypatch.setattr(groups, "get_client", lambda account=None: client)
    monkeypatch.setattr(groups, "resolve_entity", fake_resolve)
    monkeypatch.setattr(groups, "_resolve_readable_file_path", fake_readable_path)


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", [BOT_API_ID, str(BOT_API_ID)])
async def test_edit_chat_title_basic_group_uses_resolved_id(monkeypatch, chat_id):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group())

    result = await groups.edit_chat_title(chat_id=chat_id, title="New Title", account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.EditChatTitleRequest)
    assert request.chat_id == BASIC_GROUP_ID
    assert request.title == "New Title"
    assert "title updated to 'New Title'" in result


@pytest.mark.asyncio
async def test_edit_chat_photo_basic_group_uses_resolved_id(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group())

    result = await groups.edit_chat_photo(chat_id=BOT_API_ID, file_path="avatar.jpg", account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.EditChatPhotoRequest)
    assert request.chat_id == BASIC_GROUP_ID
    assert isinstance(request.photo, types.InputChatUploadedPhoto)
    assert request.photo.file.name == "avatar.jpg"
    assert "photo updated from avatar.jpg" in result


@pytest.mark.asyncio
async def test_delete_chat_photo_basic_group_uses_resolved_id(monkeypatch):
    client = FakeClient()
    _patch(monkeypatch, client, _basic_group())

    result = await groups.delete_chat_photo(chat_id=BOT_API_ID, account=None)

    (request,) = client.requests
    assert isinstance(request, functions.messages.EditChatPhotoRequest)
    assert request.chat_id == BASIC_GROUP_ID
    assert isinstance(request.photo, types.InputChatPhotoEmpty)
    assert "photo deleted" in result


@pytest.mark.asyncio
async def test_channel_branches_still_pass_the_entity(monkeypatch):
    channel = types.Channel(id=777, title="Announcements", photo=None, date=None)
    client = FakeClient()
    _patch(monkeypatch, client, channel)

    await groups.edit_chat_title(chat_id=-1000000000777, title="Title", account=None)
    await groups.edit_chat_photo(chat_id=-1000000000777, file_path="avatar.jpg", account=None)
    await groups.delete_chat_photo(chat_id=-1000000000777, account=None)

    title, photo, delete = client.requests
    assert isinstance(title, functions.channels.EditTitleRequest)
    assert isinstance(photo, functions.channels.EditPhotoRequest)
    assert isinstance(delete, functions.channels.EditPhotoRequest)
    assert all(request.channel is channel for request in client.requests)
    assert isinstance(delete.photo, types.InputChatPhotoEmpty)
