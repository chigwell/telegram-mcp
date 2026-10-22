"""Behavioral tests for the floodgate integration in the tool runtime."""

from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError

from telegram_mcp import runtime


class _FakeGate:
    """Records try_acquire calls; defers labels in ``deferred``."""

    def __init__(self, deferred=()):
        self.deferred = set(deferred)
        self.seen = []

    def try_acquire(self, label, category):
        self.seen.append((label, category))
        return 25.0 if label in self.deferred else 0.0

    def note_flood(self, label, seconds=None):
        return 1.0

    def reset(self, label=None, category=None):
        pass


class _FakeBreaker:
    def __init__(self, suspended=()):
        self.suspended = set(suspended)
        self.floods = []

    def check(self, operation, label):
        if (operation, label) in self.suspended:
            raise runtime.TelegramOperationSuspendedError(operation, label, 300.0)

    def record_flood(self, operation, label):
        self.floods.append((operation, label))

    def reset(self, operation=None, label=None):
        pass


def _sole_label():
    return next(iter(runtime.clients))


def _install(monkeypatch, gate=None, breaker=None):
    monkeypatch.setattr(runtime, "_floodgate_gate", gate or _FakeGate())
    monkeypatch.setattr(runtime, "_floodgate_breaker", breaker or _FakeBreaker())


def _tool(calls, body="ok"):
    async def fake_tool(chat_id=None, account=None):
        calls.append(account)
        return body

    return fake_tool


@pytest.mark.asyncio
async def test_gate_allow_runs_the_tool(monkeypatch):
    _install(monkeypatch)
    calls = []
    wrapped = runtime.with_account(readonly=True)(_tool(calls))

    result = await wrapped(chat_id=1)

    assert result == "ok"
    # In single-mode the wrapper leaves account=None (the client resolves inside).
    assert calls == [None]


@pytest.mark.asyncio
async def test_gate_defer_returns_rate_limit_string_without_client_call(monkeypatch):
    _install(monkeypatch, gate=_FakeGate(deferred={_sole_label()}))
    calls = []
    wrapped = runtime.with_account(readonly=True)(_tool(calls))

    result = await wrapped(chat_id=1)

    assert calls == []  # the client was never reached
    assert "Rate limit exceeded (FloodGate)" in result
    assert "Do NOT retry immediately" in result
    assert "FLOODGATE-ERR-" in result
    assert "25 seconds" in result


@pytest.mark.asyncio
async def test_floodgate_disabled_env_runs_the_tool_despite_full_gate(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLOODGATE", "0")
    _install(monkeypatch, gate=_FakeGate(deferred={_sole_label()}))
    calls = []
    wrapped = runtime.with_account(readonly=True)(_tool(calls))

    assert await wrapped(chat_id=1) == "ok"
    assert calls == [None]


@pytest.mark.asyncio
async def test_open_breaker_defers_the_tool(monkeypatch):
    tool_name = "send_message"

    async def named_tool(chat_id=None, account=None):
        calls.append(account)
        return "ok"

    named_tool.__name__ = tool_name
    breaker = _FakeBreaker(suspended={(tool_name, _sole_label())})
    _install(monkeypatch, breaker=breaker)
    calls = []
    wrapped = runtime.with_account(readonly=True)(named_tool)

    result = await wrapped(chat_id=1)

    assert calls == []
    assert "5.0 minutes" in result  # 300s render in minutes


@pytest.mark.asyncio
async def test_unknown_tool_takes_the_default_category(monkeypatch):
    gate = _FakeGate()
    _install(monkeypatch, gate=gate)
    wrapped = runtime.with_account(readonly=True)(_tool([]))

    await wrapped(chat_id=1)

    assert gate.seen == [(_sole_label(), "default")]


@pytest.mark.asyncio
async def test_fanout_gates_each_account_separately(monkeypatch):
    labels = {"aaa", "bbb"}
    monkeypatch.setattr(runtime, "clients", {label: object() for label in labels})
    _install(monkeypatch, gate=_FakeGate(deferred={"aaa"}))
    calls = []
    wrapped = runtime.with_account(readonly=True)(_tool(calls))

    result = await wrapped(chat_id=1)

    assert "[aaa]" in result and "Rate limit exceeded (FloodGate)" in result
    assert "[bbb]" in result and "ok" in result
    assert "aaa" not in calls  # the deferred account never executed the body


def test_flood_wait_error_feeds_the_breaker_once(monkeypatch):
    breaker = _FakeBreaker()
    _install(monkeypatch, breaker=breaker)
    token = runtime._CURRENT_ACCOUNT.set(_sole_label())
    try:
        message = runtime.log_and_format_error(
            "send_message", FloodWaitError(request=None, capture=300)
        )
    finally:
        runtime._CURRENT_ACCOUNT.reset(token)

    assert "Rate limit exceeded (FloodWait)" in message  # pinned string unchanged
    assert breaker.floods == [("send_message", _sole_label())]


def test_flood_report_without_account_context_is_ignored(monkeypatch):
    breaker = _FakeBreaker()
    _install(monkeypatch, breaker=breaker)
    assert runtime._CURRENT_ACCOUNT.get() is None

    runtime._floodgate_report_flood("send_message", 300)

    assert breaker.floods == []


def test_category_mapping_covers_the_hot_tools():
    from telegram_mcp.floodgate import category_for_tool

    assert category_for_tool("send_message") == "send"
    assert category_for_tool("get_history") == "history"
    assert category_for_tool("ban_user") == "admin_action"
    assert category_for_tool("leave_chat") == "channel_lifecycle"
    assert category_for_tool("get_chats") == "default"  # dialogs bucket stays unused
    assert category_for_tool("not_a_tool") == "default"
