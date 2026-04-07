import pytest
from telegram_mcp import chats


@pytest.mark.asyncio
async def test_get_chats():
    res = await chats.get_chats()
    assert res is not None


@pytest.mark.asyncio
async def test_subscribe_public_channel():
    res = await chats.subscribe_public_channel("test")
    assert res is not None


@pytest.mark.asyncio
async def test_list_topics():
    res = await chats.list_topics(123)
    assert res is not None


@pytest.mark.asyncio
async def test_list_chats():
    res = await chats.list_chats()
    assert res is not None


@pytest.mark.asyncio
async def test_get_chat():
    res = await chats.get_chat(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_direct_chat_by_contact():
    res = await chats.get_direct_chat_by_contact("test")
    assert res is not None


@pytest.mark.asyncio
async def test_get_contact_chats():
    res = await chats.get_contact_chats(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_last_interaction():
    res = await chats.get_last_interaction(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_message_context():
    res = await chats.get_message_context(123, 1)
    assert res is not None


@pytest.mark.asyncio
async def test_create_group():
    res = await chats.create_group("title", [123])
    assert res is not None


@pytest.mark.asyncio
async def test_invite_to_group():
    res = await chats.invite_to_group(123, [456])
    assert res is not None


@pytest.mark.asyncio
async def test_leave_chat():
    res = await chats.leave_chat(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_participants():
    res = await chats.get_participants(123)
    assert res is not None


@pytest.mark.asyncio
async def test_create_channel():
    res = await chats.create_channel("title")
    assert res is not None


@pytest.mark.asyncio
async def test_edit_chat_title():
    res = await chats.edit_chat_title(123, "new")
    assert res is not None


@pytest.mark.asyncio
async def test_delete_chat_photo():
    res = await chats.delete_chat_photo(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_admins():
    res = await chats.get_admins(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_banned_users():
    res = await chats.get_banned_users(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_invite_link():
    res = await chats.get_invite_link(123)
    assert res is not None


@pytest.mark.asyncio
async def test_join_chat_by_link():
    res = await chats.join_chat_by_link("http://t.me/test")
    assert res is not None


@pytest.mark.asyncio
async def test_export_chat_invite():
    res = await chats.export_chat_invite(123)
    assert res is not None


@pytest.mark.asyncio
async def test_import_chat_invite():
    res = await chats.import_chat_invite("hash")
    assert res is not None
