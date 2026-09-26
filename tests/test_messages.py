"""Unit tests for messages tools, specifically create_poll."""

from datetime import datetime
from unittest.mock import MagicMock
import pytest

from telegram_mcp.tools import messages
from telethon.tl.types import InputMediaPoll, Poll
from telethon.tl.functions.messages import SendMediaRequest


@pytest.fixture
def mock_telethon(monkeypatch):
    """Mock get_client, ensure_connected, and resolve_entity for isolated tool testing."""
    client = MagicMock()
    # cl(request) is called via async call
    sent_requests = []

    async def fake_call(request):
        sent_requests.append(request)
        return MagicMock()

    client.side_effect = fake_call
    client.is_connected = MagicMock(return_value=True)

    fake_entity = MagicMock()

    async def fake_resolve(chat_id, cl=None):
        return fake_entity

    async def fake_ensure(cl=None):
        pass

    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "ensure_connected", fake_ensure)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "is_chat_allowlist_enabled", lambda: False)

    return {
        "client": client,
        "sent_requests": sent_requests,
        "entity": fake_entity,
    }


@pytest.mark.asyncio
async def test_create_poll_int_chat_id(mock_telethon):
    res = await messages.create_poll(
        chat_id=12345678,
        question="Which programming language do you prefer?",
        options=["Python", "Rust", "Go"],
    )

    assert "Poll created successfully in chat 12345678." in res
    assert len(mock_telethon["sent_requests"]) == 1

    req = mock_telethon["sent_requests"][0]
    assert isinstance(req, SendMediaRequest)
    assert req.peer == mock_telethon["entity"]
    assert isinstance(req.media, InputMediaPoll)

    poll = req.media.poll
    assert isinstance(poll, Poll)
    assert poll.question.text == "Which programming language do you prefer?"
    assert len(poll.answers) == 3
    assert [a.text.text for a in poll.answers] == ["Python", "Rust", "Go"]
    assert poll.hash == 0
    assert poll.multiple_choice is False
    assert poll.quiz is False
    assert poll.public_voters is True


@pytest.mark.asyncio
async def test_create_poll_string_int_chat_id(mock_telethon):
    res = await messages.create_poll(
        chat_id="-100123456789",
        question="Ready for the release?",
        options=["Yes", "No"],
    )

    assert "Poll created successfully" in res
    assert len(mock_telethon["sent_requests"]) == 1
    req = mock_telethon["sent_requests"][0]
    assert req.media.poll.question.text == "Ready for the release?"


@pytest.mark.asyncio
async def test_create_poll_username_chat_id(mock_telethon):
    res = await messages.create_poll(
        chat_id="@my_channel",
        question="Should we host a meetup?",
        options=["Definitely", "Maybe", "No"],
    )

    assert "Poll created successfully" in res
    assert "@my_channel" in res
    assert len(mock_telethon["sent_requests"]) == 1


@pytest.mark.asyncio
async def test_create_poll_options_dict_list_reproduces_issue_179(mock_telethon):
    # LLMs frequently generate options formatted as a list of dicts:
    # [{"option": "Water"}, {"option": "Milk"}, {"option": "Cola"}]
    res = await messages.create_poll(
        chat_id=12345678,
        question="What drink do you prefer?",
        options=[{"option": "Water"}, {"option": "Milk"}, {"option": "Cola"}],
    )

    assert "Poll created successfully" in res
    assert len(mock_telethon["sent_requests"]) == 1
    poll = mock_telethon["sent_requests"][0].media.poll
    assert [a.text.text for a in poll.answers] == ["Water", "Milk", "Cola"]
    # Ensure options bytes are valid TL bytes
    for i, a in enumerate(poll.answers):
        assert a.option == bytes([i])


@pytest.mark.asyncio
async def test_create_poll_options_text_key_dict_list(mock_telethon):
    res = await messages.create_poll(
        chat_id=12345678,
        question="Select option",
        options=[{"text": "First"}, {"text": "Second"}],
    )

    assert "Poll created successfully" in res
    poll = mock_telethon["sent_requests"][0].media.poll
    assert [a.text.text for a in poll.answers] == ["First", "Second"]


@pytest.mark.asyncio
async def test_create_poll_options_json_string(mock_telethon):
    res = await messages.create_poll(
        chat_id=12345678,
        question="JSON test",
        options='["Option A", "Option B", "Option C"]',
    )

    assert "Poll created successfully" in res
    poll = mock_telethon["sent_requests"][0].media.poll
    assert [a.text.text for a in poll.answers] == ["Option A", "Option B", "Option C"]


@pytest.mark.asyncio
async def test_create_poll_options_comma_separated_string(mock_telethon):
    res = await messages.create_poll(
        chat_id=12345678,
        question="Comma test",
        options="Cat, Dog, Bird",
    )

    assert "Poll created successfully" in res
    poll = mock_telethon["sent_requests"][0].media.poll
    assert [a.text.text for a in poll.answers] == ["Cat", "Dog", "Bird"]


@pytest.mark.asyncio
async def test_create_poll_attributes(mock_telethon):
    res = await messages.create_poll(
        chat_id=12345678,
        question="Quiz question",
        options=["Answer 1", "Answer 2"],
        multiple_choice=True,
        quiz_mode=True,
        public_votes=False,
        close_date="2026-12-31 23:59:59",
    )

    assert "Poll created successfully" in res
    poll = mock_telethon["sent_requests"][0].media.poll
    assert poll.multiple_choice is True
    assert poll.quiz is True
    assert poll.public_voters is False
    assert poll.close_date == datetime.fromisoformat("2026-12-31 23:59:59")


@pytest.mark.asyncio
async def test_create_poll_validation_empty_question():
    res = await messages.create_poll(
        chat_id=12345678,
        question="   ",
        options=["A", "B"],
    )
    assert res == "Error: Poll question cannot be empty."


@pytest.mark.asyncio
async def test_create_poll_validation_question_too_long():
    res = await messages.create_poll(
        chat_id=12345678,
        question="q" * 301,
        options=["A", "B"],
    )
    assert res == "Error: Poll question cannot exceed 300 characters."


@pytest.mark.asyncio
async def test_create_poll_validation_too_few_options():
    res = await messages.create_poll(
        chat_id=12345678,
        question="Question",
        options=["Only one option"],
    )
    assert res == "Error: Poll must have at least 2 options."


@pytest.mark.asyncio
async def test_create_poll_validation_too_many_options():
    res = await messages.create_poll(
        chat_id=12345678,
        question="Question",
        options=[f"Option {i}" for i in range(11)],
    )
    assert res == "Error: Poll can have at most 10 options."


@pytest.mark.asyncio
async def test_create_poll_validation_empty_option():
    res = await messages.create_poll(
        chat_id=12345678,
        question="Question",
        options=["Valid", "  "],
    )
    assert res == "Error: Poll options cannot be empty."


@pytest.mark.asyncio
async def test_create_poll_validation_duplicate_options():
    res = await messages.create_poll(
        chat_id=12345678,
        question="Question",
        options=["Yes", "Yes"],
    )
    assert res == "Error: Poll options must be unique."


@pytest.mark.asyncio
async def test_create_poll_validation_option_too_long():
    res = await messages.create_poll(
        chat_id=12345678,
        question="Question",
        options=["Normal", "x" * 101],
    )
    assert "Error: Each poll option cannot exceed 100 characters." in res


@pytest.mark.asyncio
async def test_create_poll_validation_invalid_close_date():
    res = await messages.create_poll(
        chat_id=12345678,
        question="Question",
        options=["Option 1", "Option 2"],
        close_date="not-a-date",
    )
    assert "Invalid close_date format. Use YYYY-MM-DD HH:MM:SS format." in res


def test_create_poll_fastmcp_schema():
    tool = next(t for t in messages.mcp._tool_manager.list_tools() if t.name == "create_poll")
    props = tool.parameters["properties"]
    assert "chat_id" in props
    chat_id_schema = props["chat_id"]
    assert "anyOf" in chat_id_schema
    types = {item.get("type") for item in chat_id_schema["anyOf"]}
    assert "integer" in types
    assert "string" in types

    assert "options" in props
    options_schema = props["options"]
    assert "anyOf" in options_schema
    # Check that array of strings is an allowed variant
    array_schemas = [item for item in options_schema["anyOf"] if item.get("type") == "array"]
    assert len(array_schemas) >= 1
    items_types = [s.get("items", {}).get("type") for s in array_schemas]
    assert "string" in items_types
