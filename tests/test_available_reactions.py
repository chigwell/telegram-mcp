"""get_available_reactions reads the reactions a chat allows from the full chat."""

import json
import re
from datetime import datetime, timezone

import pytest
from telethon.errors import ChannelPrivateError
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.runtime import mcp
from telegram_mcp.tools import messages

DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
CHANNEL_ID = -1004358797279
CUSTOM_ID = 5427009714745517609

CHANNEL = types.Channel(
    id=4358797279, title="News", photo=types.ChatPhotoEmpty(), date=DATE, megagroup=True
)
BASIC_GROUP = types.Chat(
    id=77, title="Family", photo=types.ChatPhotoEmpty(), participants_count=3, date=DATE, version=1
)
USER = types.User(id=42, first_name="Ann")


def _channel_full(available_reactions):
    return types.messages.ChatFull(
        full_chat=types.ChannelFull(
            id=CHANNEL.id,
            about="",
            read_inbox_max_id=0,
            read_outbox_max_id=0,
            unread_count=0,
            chat_photo=types.PhotoEmpty(id=0),
            notify_settings=types.PeerNotifySettings(),
            bot_info=[],
            pts=1,
            available_reactions=available_reactions,
        ),
        chats=[CHANNEL],
        users=[],
    )


def _basic_group_full(available_reactions):
    return types.messages.ChatFull(
        full_chat=types.ChatFull(
            id=BASIC_GROUP.id,
            about="",
            participants=types.ChatParticipantsForbidden(chat_id=BASIC_GROUP.id),
            notify_settings=types.PeerNotifySettings(),
            available_reactions=available_reactions,
        ),
        chats=[BASIC_GROUP],
        users=[],
    )


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture(autouse=True)
def _no_chat_allowlist(monkeypatch):
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)


@pytest.fixture
def connect(monkeypatch):
    """Make the tool talk to the given fake client and resolve chats to the given entity."""

    def connect(fake, entity):
        async def resolve(chat_id, selected_client):
            assert selected_client is fake
            return entity

        monkeypatch.setattr(messages, "get_client", lambda account=None: fake)
        monkeypatch.setattr(messages, "resolve_entity", resolve)
        return fake

    return connect


def test_tool_is_registered_and_read_only():
    registered = {t.name: t for t in mcp._tool_manager.list_tools()}

    assert "get_available_reactions" in registered
    assert registered["get_available_reactions"].annotations.readOnlyHint is True
    assert "get_available_reactions" in messages.__all__


# ---------------------------------------------------------
# Test: the setting is read from the full chat
# ---------------------------------------------------------
def test_chat_entities_do_not_carry_the_setting():
    # Why the full chat is fetched: reading it off the entity finds nothing.
    assert not hasattr(CHANNEL, "available_reactions")
    assert not hasattr(BASIC_GROUP, "available_reactions")


@pytest.mark.asyncio
@pytest.mark.parametrize("allow_custom", [True, False])
async def test_all_reactions_with_or_without_custom_emoji(connect, allow_custom):
    fake = connect(
        FakeClient(_channel_full(types.ChatReactionsAll(allow_custom=allow_custom))), CHANNEL
    )

    data = json.loads(await messages.get_available_reactions(CHANNEL_ID))

    assert len(fake.requests) == 1
    assert isinstance(fake.requests[0], functions.channels.GetFullChannelRequest)
    assert fake.requests[0].channel is CHANNEL
    assert data == {
        "chat_id": str(CHANNEL_ID),
        "allowed": "all",
        "allow_custom": allow_custom,
        "standard_reactions": [],
        "custom_emoji_reactions": [],
    }


@pytest.mark.asyncio
async def test_some_reactions_are_listed_and_custom_ones_can_be_sent_back(connect):
    connect(
        FakeClient(
            _channel_full(
                types.ChatReactionsSome(
                    reactions=[
                        types.ReactionEmoji(emoticon="👍"),
                        types.ReactionCustomEmoji(document_id=CUSTOM_ID),
                        types.ReactionEmoji(emoticon="🔥"),
                        types.ReactionEmpty(),
                    ]
                )
            )
        ),
        CHANNEL,
    )

    data = json.loads(await messages.get_available_reactions(CHANNEL_ID))

    assert data["allowed"] == "some"
    assert data["allow_custom"] is False
    assert data["standard_reactions"] == ["👍", "🔥"]
    # The form get_message_reactions reports and send_reaction accepts.
    assert data["custom_emoji_reactions"] == [f"custom:{CUSTOM_ID}"]


@pytest.mark.asyncio
async def test_reactions_turned_off(connect):
    connect(FakeClient(_channel_full(types.ChatReactionsNone())), CHANNEL)

    data = json.loads(await messages.get_available_reactions(CHANNEL_ID))

    assert data["allowed"] == "none"
    assert data["standard_reactions"] == data["custom_emoji_reactions"] == []


@pytest.mark.asyncio
async def test_output_has_no_hardcoded_reaction_list(connect):
    connect(FakeClient(_channel_full(types.ChatReactionsAll(allow_custom=True))), CHANNEL)

    result = await messages.get_available_reactions(CHANNEL_ID)

    assert "note" not in json.loads(result)
    assert "👍" not in result


@pytest.mark.asyncio
async def test_basic_group_uses_the_basic_group_request(connect):
    fake = connect(
        FakeClient(_basic_group_full(types.ChatReactionsSome([types.ReactionEmoji("❤")]))),
        BASIC_GROUP,
    )

    data = json.loads(await messages.get_available_reactions(-BASIC_GROUP.id))

    assert len(fake.requests) == 1
    assert isinstance(fake.requests[0], functions.messages.GetFullChatRequest)
    assert fake.requests[0].chat_id == BASIC_GROUP.id
    assert data["allowed"] == "some"
    assert data["standard_reactions"] == ["❤"]


@pytest.mark.asyncio
async def test_missing_setting_is_reported_not_guessed(connect):
    connect(FakeClient(_channel_full(None)), CHANNEL)

    result = await messages.get_available_reactions(CHANNEL_ID)

    assert result == f"Telegram returned no reaction settings for chat {CHANNEL_ID}."


@pytest.mark.asyncio
async def test_private_chat_has_no_setting_and_asks_telegram_nothing(connect):
    fake = connect(FakeClient(), USER)

    result = await messages.get_available_reactions(42)

    assert result == "Chat 42 is a private chat: only groups and channels set reactions."
    assert fake.requests == []


# ---------------------------------------------------------
# Test: access and errors
# ---------------------------------------------------------
@pytest.mark.asyncio
async def test_chat_outside_the_allowlist_is_not_asked_about(connect, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "12345")
    fake = connect(FakeClient(_channel_full(types.ChatReactionsNone())), CHANNEL)

    result = await messages.get_available_reactions(CHANNEL_ID)

    assert "restricted by privacy policy" in result
    assert fake.requests == []


@pytest.mark.asyncio
async def test_rpc_error_names_telegram_error_without_its_text(connect):
    error = ChannelPrivateError(request=functions.channels.GetFullChannelRequest(CHANNEL))
    assert "private" in str(error)  # the text the tool must not pass on
    connect(FakeClient(error=error), CHANNEL)

    result = await messages.get_available_reactions(CHANNEL_ID)

    assert re.fullmatch(
        r"An error occurred \(code: GEN-ERR-\d{3}\): Telegram returned "
        r"CHANNEL_PRIVATE \(ChannelPrivateError\)\.",
        result,
    )


@pytest.mark.asyncio
async def test_unexpected_error_does_not_leak_its_text(connect):
    connect(FakeClient(error=RuntimeError("exception-payload-secret")), CHANNEL)

    result = await messages.get_available_reactions(CHANNEL_ID)

    assert result.startswith("An error occurred (code:")
    assert "exception-payload-secret" not in result
