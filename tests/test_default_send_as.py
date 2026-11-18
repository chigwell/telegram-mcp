"""Offline contracts for saving a chat's default sender."""

import pytest
from telethon import errors
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import messages


class RecordingClient:
    def __init__(self):
        self.requests = []
        self.resolve_calls = []
        self.error = None
        self.peers = {
            "supergroup": types.InputPeerChannel(11, 101),
            "basic_group": types.InputPeerChat(12),
            "person": types.InputPeerUser(22, 202),
            "me": types.InputPeerSelf(),
            "channel": types.InputPeerChannel(33, 303),
        }

    async def resolve(self, identifier, client):
        assert client is self
        self.resolve_calls.append(identifier)
        return self.peers[identifier]

    async def __call__(self, request):
        self.requests.append(request)
        bytes(request)  # Exercise the real SDK serializer, not a fake TL schema.
        if self.error:
            raise self.error
        return True


@pytest.fixture
def client(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(runtime, "clients", {"default": client})
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_input_entity", client.resolve)
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "send_as,label",
    [("me", messages.SEND_AS_SELF_LABEL), ("channel", "channel")],
)
async def test_saves_the_resolved_sender(client, send_as, label):
    result = await messages.set_default_send_as("supergroup", send_as)
    assert result == f"Default sender in chat supergroup set to {label}."
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.SaveDefaultSendAsRequest)
    assert request.peer is client.peers["supergroup"]
    assert request.send_as is client.peers[send_as]


@pytest.mark.asyncio
@pytest.mark.parametrize("chat", ["basic_group", "person"])
async def test_chat_without_send_as_is_refused_before_any_request(client, chat):
    result = await messages.set_default_send_as(chat, "me")
    assert result == messages.SEND_AS_UNSUPPORTED_CHAT.format(chat_id=chat)
    assert client.requests == []
    assert client.resolve_calls == [chat]


@pytest.mark.asyncio
async def test_sender_not_allowed_points_to_get_send_as(client):
    client.error = errors.SendAsPeerInvalidError(request=None)
    result = await messages.set_default_send_as("supergroup", "channel")
    assert result == messages.SEND_AS_PEER_REFUSED.format(send_as="channel", chat_id="supergroup")
    assert "get_send_as" in result


@pytest.mark.asyncio
async def test_premium_only_sender_is_explained(client):
    client.error = errors.BadRequestError(None, "PREMIUM_ACCOUNT_REQUIRED", 400)
    result = await messages.set_default_send_as("supergroup", "channel")
    assert result == messages.SEND_AS_PREMIUM_REQUIRED.format(
        send_as="channel", chat_id="supergroup"
    )


@pytest.mark.asyncio
async def test_other_telegram_errors_keep_the_error_contract(client):
    client.error = errors.ChatAdminRequiredError(request=None)
    result = await messages.set_default_send_as("supergroup", "channel")
    assert result.startswith("An error occurred (code: ")
    assert "CHAT_ADMIN_REQUIRED" in result


@pytest.mark.asyncio
async def test_invalid_sender_id_fails_validation_before_any_request(client):
    result = await messages.set_default_send_as(chat_id="supergroup", send_as=2**63)
    assert "Invalid send_as" in result
    assert client.requests == []
    assert client.resolve_calls == []


def test_tool_is_a_write_that_can_be_repeated():
    registered = {t.name: t for t in runtime.mcp._tool_manager.list_tools()}
    tool = registered["set_default_send_as"]
    assert tool.annotations.readOnlyHint is not True
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.destructiveHint is False
