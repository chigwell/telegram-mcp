import pytest
from telegram_mcp import groups


@pytest.mark.asyncio
async def test_promote_admin():
    res = await groups.promote_admin(123, 456)
    assert res is not None


@pytest.mark.asyncio
async def test_demote_admin():
    res = await groups.demote_admin(123, 456)
    assert res is not None


@pytest.mark.asyncio
async def test_ban_user():
    res = await groups.ban_user(123, 456)
    assert res is not None


@pytest.mark.asyncio
async def test_unban_user():
    res = await groups.unban_user(123, 456)
    assert res is not None
