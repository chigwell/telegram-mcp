"""send_typing shows the typing indicator, refreshes it, and always clears it."""

import asyncio
import math

import pytest
from telethon.tl import functions
from telethon.tl.types import SendMessageCancelAction, SendMessageTypingAction

from telegram_mcp.tools import messages


class FakeClient:
    """Records every request; `fail_calls` are the 1-based call numbers that raise."""

    def __init__(self, fail_calls=()):
        self.requests = []
        self.fail_calls = set(fail_calls)

    async def __call__(self, request, **kwargs):
        assert kwargs == {"flood_sleep_threshold": 0}
        self.requests.append(request)
        if len(self.requests) in self.fail_calls:
            raise RuntimeError("boom")
        return True


def _install(monkeypatch, fake):
    monkeypatch.setattr(messages, "get_client", lambda account=None: fake)

    async def resolve(chat_id, selected_client):
        return "peer"

    monkeypatch.setattr(messages, "resolve_entity", resolve)
    monkeypatch.setattr(messages, "resolve_input_entity", resolve)
    monkeypatch.setattr(messages, "is_chat_allowlist_enabled", lambda: False)
    return fake


@pytest.fixture
def client(monkeypatch):
    return _install(monkeypatch, FakeClient())


@pytest.fixture
def sleeps(monkeypatch):
    """Record the requested delays instead of really waiting."""
    delays = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        delays.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(messages.asyncio, "sleep", fake_sleep)
    return delays


def _kinds(fake):
    assert all(isinstance(r, functions.messages.SetTypingRequest) for r in fake.requests)
    return [type(r.action) for r in fake.requests]


@pytest.mark.asyncio
async def test_short_typing_is_one_typing_then_one_cancel(client, sleeps):
    result = await messages.send_typing(123456, 0.5)

    assert result == "Typing requests accepted in chat 123456 for 0.5s; indicator cleared."
    assert _kinds(client) == [SendMessageTypingAction, SendMessageCancelAction]
    assert client.requests[0].peer == "peer"
    assert sleeps == [0.5]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seconds, typing_requests",
    [(0.5, 1), (4, 1), (4.5, 2), (8, 2), (8.1, 3), (45, 12)],
)
async def test_refreshes_before_telegram_expires_the_indicator(
    client, sleeps, seconds, typing_requests
):
    await messages.send_typing(123456, seconds)

    kinds = _kinds(client)
    assert kinds.count(SendMessageTypingAction) == typing_requests
    assert kinds.count(SendMessageCancelAction) == 1
    assert kinds[-1] is SendMessageCancelAction
    assert max(sleeps) <= messages.TYPING_REFRESH_SECONDS
    assert sum(sleeps) == pytest.approx(seconds)


@pytest.mark.asyncio
async def test_topic_id_is_sent_with_every_request(client, sleeps):
    await messages.send_typing(123456, 10, topic_id=77)

    assert len(client.requests) == 4
    assert {r.top_msg_id for r in client.requests} == {77}


@pytest.mark.asyncio
async def test_no_topic_id_by_default(client, sleeps):
    await messages.send_typing(123456, 0.5)

    assert {r.top_msg_id for r in client.requests} == {None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seconds",
    [0, 0.4, 45.1, 600, -3, True, False, "5", None, float("nan"), float("inf"), float("-inf")],
)
async def test_out_of_range_seconds_is_rejected_without_any_request(client, sleeps, seconds):
    result = await messages.send_typing(123456, seconds)

    assert result == "seconds must be between 0.5 and 45."
    assert client.requests == []
    assert sleeps == []


@pytest.mark.asyncio
async def test_cancel_is_still_sent_when_a_refresh_fails(monkeypatch, sleeps):
    fake = _install(monkeypatch, FakeClient(fail_calls={2}))

    result = await messages.send_typing(123456, 10)

    assert "indicator cleared" not in result
    assert _kinds(fake) == [
        SendMessageTypingAction,
        SendMessageTypingAction,
        SendMessageCancelAction,
    ]


@pytest.mark.asyncio
async def test_failed_cancel_reports_degraded_result(monkeypatch, sleeps):
    fake = _install(monkeypatch, FakeClient(fail_calls={2}))

    result = await messages.send_typing(123456, 0.5)

    assert "clearing the indicator could not be confirmed" in result
    assert _kinds(fake) == [SendMessageTypingAction, SendMessageCancelAction]


@pytest.mark.asyncio
async def test_failed_cancel_does_not_hide_the_original_error(monkeypatch, sleeps):
    fake = _install(monkeypatch, FakeClient(fail_calls={1, 2}))

    result = await messages.send_typing(123456, 0.5)

    assert "indicator cleared" not in result
    assert len(fake.requests) == 2


@pytest.mark.asyncio
async def test_allowlist_blocks_typing_before_any_request(client, sleeps, monkeypatch):
    monkeypatch.setattr(messages, "is_chat_allowlist_enabled", lambda: True)
    monkeypatch.setattr(messages, "is_chat_allowed", lambda chat_id, entity=None: False)
    monkeypatch.setattr(messages, "check_chat_access", lambda chat_id, entity=None: "denied")

    result = await messages.send_typing(123456, 0.5)

    assert "denied" in result
    assert client.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("topic_id", [0, -1, True, 1.5, "77"])
async def test_invalid_topic_does_not_contact_telegram(client, topic_id):
    result = await messages.send_typing(123456, 0.5, topic_id=topic_id)
    assert result == "Error: topic_id must be a positive integer."
    assert client.requests == []


@pytest.mark.asyncio
async def test_false_typing_result_is_not_success(monkeypatch, sleeps):
    class RefusingClient(FakeClient):
        async def __call__(self, request, **kwargs):
            await super().__call__(request, **kwargs)
            return isinstance(request.action, SendMessageCancelAction)

    fake = _install(monkeypatch, RefusingClient())
    result = await messages.send_typing(123456, 0.5)
    assert "indicator cleared" not in result
    assert sleeps == []
    assert _kinds(fake)[-1] is SendMessageCancelAction


@pytest.mark.asyncio
async def test_false_cancel_result_is_degraded(monkeypatch, sleeps):
    class RefusingCancel(FakeClient):
        async def __call__(self, request, **kwargs):
            await super().__call__(request, **kwargs)
            return not isinstance(request.action, SendMessageCancelAction)

    _install(monkeypatch, RefusingCancel())
    result = await messages.send_typing(123456, 0.5)
    assert "could not be confirmed" in result


@pytest.mark.asyncio
async def test_overlap_rejected_and_cancellation_cleans_registry(monkeypatch):
    started = asyncio.Event()
    forever = asyncio.Event()

    class BlockingClient(FakeClient):
        async def __call__(self, request, **kwargs):
            await super().__call__(request, **kwargs)
            if isinstance(request.action, SendMessageTypingAction):
                started.set()
                await forever.wait()
            return True

    fake = _install(monkeypatch, BlockingClient())
    task = asyncio.create_task(messages.send_typing(123456, 0.5))
    await asyncio.wait_for(started.wait(), 1)
    try:
        result = await messages.send_typing(123456, 0.5)
        assert "already active" in result
        assert len(fake.requests) == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert _kinds(fake)[-1] is SendMessageCancelAction
    assert not messages._active_typing


@pytest.mark.asyncio
async def test_operation_and_cleanup_timeouts_are_bounded(monkeypatch):
    forever = asyncio.Event()

    class StalledClient(FakeClient):
        async def __call__(self, request, **kwargs):
            await super().__call__(request, **kwargs)
            await forever.wait()

    fake = _install(monkeypatch, StalledClient())
    monkeypatch.setattr(messages, "TYPING_OPERATION_TIMEOUT", 0.02)
    monkeypatch.setattr(messages, "TYPING_CLEANUP_TIMEOUT", 0.02)
    result = await asyncio.wait_for(messages.send_typing(123456, 0.5), timeout=1)
    assert "indicator cleared" not in result
    assert _kinds(fake) == [SendMessageTypingAction, SendMessageCancelAction]
    assert not messages._active_typing


def test_registered_tool_metadata():
    tool = next(t for t in messages.mcp._tool_manager.list_tools() if t.name == "send_typing")

    assert not tool.annotations.readOnlyHint
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    required = set(tool.parameters.get("required", []))
    assert "chat_id" in required
    assert not {"seconds", "topic_id"} & required
    assert "send_typing" in messages.__all__
    assert math.isclose(messages.TYPING_REFRESH_SECONDS, 4.0)
