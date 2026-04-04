import pytest
from telegram_mcp import messages


@pytest.mark.asyncio
async def test_get_messages():
    res = await messages.get_messages(123)
    assert "No messages found" in res or "ID" in res.upper() or res


@pytest.mark.asyncio
async def test_get_history():
    res = await messages.get_history(123)
    assert res is not None


@pytest.mark.asyncio
async def test_get_drafts():
    res = await messages.get_drafts()
    res = await messages.send_message(123, "test")
    assert res is not None


@pytest.mark.asyncio
async def test_send_message():
    res = await messages.send_message(123, "test")
    assert res is not None


@pytest.mark.asyncio
async def test_list_inline_buttons():
    res = await messages.list_inline_buttons(123)
    assert res is not None


@pytest.mark.asyncio
async def test_press_inline_button():
    res = await messages.press_inline_button(123, button_text="test")
    assert res is not None


@pytest.mark.asyncio
async def test_list_messages():
    res = await messages.list_messages(123)
    assert res is not None


@pytest.mark.asyncio
async def test_forward_message():
    res = await messages.forward_message(123, 1, 456)
    assert res is not None


@pytest.mark.asyncio
async def test_edit_message():
    res = await messages.edit_message(123, 1, "new")
    assert res is not None


@pytest.mark.asyncio
async def test_delete_message():
    res = await messages.delete_message(123, 1)
    assert res is not None


@pytest.mark.asyncio
async def test_pin_message():
    res = await messages.pin_message(123, 1)
    assert res is not None


@pytest.mark.asyncio
async def test_unpin_message():
    res = await messages.unpin_message(123, 1)
    assert res is not None
