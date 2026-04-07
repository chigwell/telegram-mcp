import pytest
from telegram_mcp import contacts


@pytest.mark.asyncio
async def test_list_contacts():
    res = await contacts.list_contacts()
    assert res is not None


@pytest.mark.asyncio
async def test_search_contacts():
    res = await contacts.search_contacts("test")
    assert res is not None


@pytest.mark.asyncio
async def test_get_contact_ids():
    res = await contacts.get_contact_ids()
    assert res is not None


@pytest.mark.asyncio
async def test_add_contact():
    res = await contacts.add_contact(phone="+1234567890", first_name="test")
    assert res is not None


@pytest.mark.asyncio
async def test_delete_contact():
    res = await contacts.delete_contact(123)
    assert res is not None


@pytest.mark.asyncio
async def test_block_user():
    res = await contacts.block_user(123)
    assert res is not None


@pytest.mark.asyncio
async def test_unblock_user():
    res = await contacts.unblock_user(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_me():
    res = await contacts.get_me()
    assert res is not None


@pytest.mark.asyncio
async def test_update_profile():
    res = await contacts.update_profile(first_name="new")
    assert res is not None


@pytest.mark.asyncio
async def test_delete_profile_photo():
    res = await contacts.delete_profile_photo()
    assert res is not None


@pytest.mark.asyncio
async def test_get_privacy_settings():
    res = await contacts.get_privacy_settings()
    assert res is not None


@pytest.mark.asyncio
async def test_set_privacy_settings():
    res = await contacts.set_privacy_settings("status", allow_users=[123])
    assert res is not None


@pytest.mark.asyncio
async def test_import_contacts():
    res = await contacts.import_contacts([{"phone": "+123", "first_name": "test"}])
    assert res is not None


@pytest.mark.asyncio
async def test_export_contacts():
    res = await contacts.export_contacts()
    assert res is not None


@pytest.mark.asyncio
async def test_get_blocked_users():
    res = await contacts.get_blocked_users()
    assert res is not None
