import pytest
import os

os.environ["TELEGRAM_API_ID"] = "12345"
os.environ["TELEGRAM_API_HASH"] = "dummy_hash"
from main import validate_id, ValidationError, log_and_format_error
from functools import wraps
import asyncio
from typing import Union, List, Optional


# A simple async function to be decorated for testing
@validate_id("user_id", "chat_id", "user_ids")
async def dummy_function(**kwargs):
    return "success", kwargs


@pytest.mark.asyncio
async def test_valid_integer_id():
    result, kwargs = await dummy_function(user_id=12345)
    assert result == "success"
    assert kwargs["user_id"] == 12345


@pytest.mark.asyncio
async def test_valid_negative_integer_id():
    result, kwargs = await dummy_function(chat_id=-100123456)
    assert result == "success"
    assert kwargs["chat_id"] == -100123456


@pytest.mark.asyncio
async def test_valid_string_integer_id():
    result, kwargs = await dummy_function(user_id="12345")
    assert result == "success"
    assert kwargs["user_id"] == 12345


@pytest.mark.asyncio
async def test_valid_username():
    result, kwargs = await dummy_function(user_id="@test_user")
    assert result == "success"
    assert kwargs["user_id"] == "@test_user"


@pytest.mark.asyncio
async def test_valid_username_without_at():
    result, kwargs = await dummy_function(user_id="test_user_long_enough")
    assert result == "success"
    assert kwargs["user_id"] == "test_user_long_enough"


@pytest.mark.asyncio
async def test_valid_list_of_ids():
    result, kwargs = await dummy_function(user_ids=[123, "456", "@test_user"])
    assert result == "success"
    assert kwargs["user_ids"] == [123, 456, "@test_user"]


@pytest.mark.asyncio
async def test_invalid_float_id():
    result = await dummy_function(user_id=123.45)
    assert "Invalid user_id" in result
    assert "Type must be an integer or a string" in result


@pytest.mark.asyncio
async def test_invalid_string_id():
    result = await dummy_function(user_id="inv")  # too short
    assert "Invalid user_id" in result
    assert "Must be a valid integer ID, or a username string" in result


@pytest.mark.asyncio
async def test_integer_out_of_range():
    result = await dummy_function(user_id=2**64)
    assert "Invalid user_id" in result
    assert "out of the valid integer range" in result


@pytest.mark.asyncio
async def test_invalid_item_in_list():
    result = await dummy_function(user_ids=[123, "456", 123.45])
    assert "Invalid user_ids" in result
    assert "Type must be an integer or a string" in result


@pytest.mark.asyncio
async def test_no_id_provided():
    result, kwargs = await dummy_function()
    assert result == "success"


@pytest.mark.asyncio
async def test_none_id_provided():
    result, kwargs = await dummy_function(user_id=None)
    assert result == "success"
