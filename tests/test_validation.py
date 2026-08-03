import json

import pytest

from main import validate_id
from telegram_mcp import runtime


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
async def test_unresolvable_string_id_asks_the_user(monkeypatch, tmp_path):
    # A free-text reference that matches no alias is a question for the user, not a
    # dead end: the decorator returns an instruction the agent can act on.
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(tmp_path / "aliases.json"))

    payload = json.loads(await dummy_function(user_id="inv"))  # too short for a username

    assert payload["error"] == "unknown_contact"
    assert payload["reference"] == "inv"
    assert payload["nothing_sent"] is True
    assert "set_contact_alias" in payload["instruction"]


@pytest.mark.asyncio
async def test_saved_alias_passes_validation(monkeypatch, tmp_path):
    # The regression that made aliases unusable: @validate_id rejected every
    # non-ASCII chat_id before the resolver ever saw it.
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(tmp_path / "aliases.json"))
    runtime.save_aliases({"чикичев игорь": 719969066})

    result, kwargs = await dummy_function(user_id="Чикичев Игорь")

    assert result == "success"
    assert kwargs["user_id"] == 719969066
    assert runtime.alias_wording(kwargs["user_id"]) == "Чикичев Игорь"


@pytest.mark.asyncio
async def test_lookalike_alias_is_not_substituted(monkeypatch, tmp_path):
    # A near miss must reach the agent as a question, never as a resolved id.
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(tmp_path / "aliases.json"))
    runtime.save_aliases({"леня": {"id": 222, "name": "Леонид"}})

    payload = json.loads(await dummy_function(user_id="Лена"))

    assert payload["error"] == "confirm_contact"
    assert payload["candidates"][0]["name"] == "Леонид"


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
