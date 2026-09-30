"""Every JSON reader returns the same complete record as get_history.

list_messages, search_messages, search_global and get_message_context used to
hand-build a text-only record, so a channel post read through them lost the
link hidden behind its text, its forward origin and its view count.
"""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

from telegram_mcp import transcription
from telegram_mcp.tools import messages

DATE = datetime(2026, 9, 16, tzinfo=timezone.utc)
HIDDEN_URL = "https://example.com/model"
CHANNEL_ID = 1234567890
MARKED_CHANNEL_ID = -1001234567890


def _post(**kwargs):
    msg = types.Message(
        id=1,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message="Model is here",
        entities=[types.MessageEntityTextUrl(9, 4, HIDDEN_URL)],
        fwd_from=types.MessageFwdHeader(
            date=DATE, from_id=types.PeerChannel(987654321), channel_post=17
        ),
        views=1500,
        forwards=12,
        **kwargs,
    )
    msg._chat = SimpleNamespace(id=CHANNEL_ID, title="Example channel")
    return msg


def _voice():
    doc = types.Document(
        id=1,
        access_hash=0,
        file_reference=b"",
        date=DATE,
        mime_type="audio/ogg",
        size=1,
        dc_id=2,
        attributes=[types.DocumentAttributeAudio(duration=13, voice=True)],
    )
    msg = types.Message(
        id=1,
        peer_id=types.PeerChannel(CHANNEL_ID),
        date=DATE,
        message="",
        media=types.MessageMediaDocument(document=doc),
    )
    msg._chat = SimpleNamespace(id=CHANNEL_ID, title="Example channel")
    return msg


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        messages,
        "resolve_entity",
        AsyncMock(
            return_value=types.Channel(
                id=CHANNEL_ID, title="Example channel", photo=types.ChatPhotoEmpty(), date=DATE
            )
        ),
    )
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(messages.transcription, "prefetch_transcripts", AsyncMock())
    return cl


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, arguments",
    [
        (messages.get_history, {"chat_id": MARKED_CHANNEL_ID}),
        (messages.list_messages, {"chat_id": MARKED_CHANNEL_ID}),
        (messages.search_messages, {"chat_id": MARKED_CHANNEL_ID, "query": "Model"}),
        (messages.search_global, {"query": "Model"}),
        (messages.get_message_context, {"chat_id": MARKED_CHANNEL_ID, "message_id": 1}),
    ],
)
async def test_json_readers_return_the_complete_record(client, tool, arguments):
    msg = _post()
    client.get_messages.return_value = [msg]
    if tool is messages.get_message_context:
        client.get_messages.side_effect = [[], msg, []]

    record = json.loads(await tool(**arguments, account="test"))["results"][0]

    assert record["text"] == "Model is here"
    assert record["link_urls"] == [HIDDEN_URL]
    assert record["forwarded"]["channel_post"] == 17
    assert record["engagement"]["views"] == 1500


@pytest.mark.asyncio
async def test_search_global_keeps_chat_attribution(client):
    client.get_messages.return_value = [_post()]

    record = json.loads(await messages.search_global("Model", account="test"))["results"][0]

    assert record["chat_name"] == "Example channel"
    assert record["chat_id"] == MARKED_CHANNEL_ID


@pytest.mark.asyncio
async def test_context_keeps_target_marker(client):
    msg = _post()
    client.get_messages.side_effect = [[], msg, []]

    result = json.loads(await messages.get_message_context(MARKED_CHANNEL_ID, 1, account="test"))

    assert result["results"][0]["is_target"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["search_global", "get_message_context"])
async def test_cached_voice_transcript_is_part_of_the_record(client, transcript_cache_dir, tool):
    transcription.save_transcript(MARKED_CHANNEL_ID, 1, "groq", "Hello there", duration=13)
    voice = _voice()
    if tool == "search_global":
        client.get_messages.return_value = [voice]
        result = await messages.search_global("Hello", account="test")
    else:
        client.get_messages.side_effect = [[], voice, []]
        result = await messages.get_message_context(MARKED_CHANNEL_ID, 1, account="test")

    record = json.loads(result)["results"][0]

    assert record["media"] == "voice"
    assert record["transcript"] == "Hello there"


def test_link_preview_is_part_of_the_record():
    page = types.WebPage(
        id=1,
        url=HIDDEN_URL,
        display_url="example.com/model",
        hash=0,
        site_name="Example",
        title="Example model",
    )
    msg = _post(media=types.MessageMediaWebPage(webpage=page))

    assert messages.message_to_dict(msg)["web_preview"] == {
        "url": HIDDEN_URL,
        "site_name": "Example",
        "title": "Example model",
    }


def test_poll_question_and_answers_are_part_of_the_record():
    poll = types.Poll(
        id=1,
        question=types.TextWithEntities(text="Which model?", entities=[]),
        answers=[
            types.PollAnswer(text=types.TextWithEntities(text="Qwen", entities=[]), option=b"0"),
            types.PollAnswer(text=types.TextWithEntities(text="Gemma", entities=[]), option=b"1"),
        ],
        hash=0,
    )
    msg = _post(media=types.MessageMediaPoll(poll=poll, results=types.PollResults()))

    assert messages.message_to_dict(msg)["poll"] == {
        "question": "Which model?",
        "answers": ["Qwen", "Gemma"],
    }
