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
        self.successes = []

    def check(self, operation, label):
        if (operation, label) in self.suspended:
            raise runtime.TelegramOperationSuspendedError(operation, label, 300.0)

    def record_flood(self, operation, label):
        self.floods.append((operation, label))

    def record_success(self, operation, label):
        self.successes.append((operation, label))
        self.suspended.discard((operation, label))

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
    assert category_for_tool("resolve_username") == "resolve_guard"
    assert category_for_tool("not_a_tool") == "default"


@pytest.mark.asyncio
async def test_fanout_all_deferred_is_a_joined_reply_not_a_tool_error(monkeypatch):
    labels = {"aaa", "bbb"}
    monkeypatch.setattr(runtime, "clients", {label: object() for label in labels})
    _install(monkeypatch, gate=_FakeGate(deferred=labels))
    wrapped = runtime.with_account(readonly=True)(_tool([]))

    result = await wrapped(chat_id=1)  # must not raise ToolError

    assert result.count("Rate limit exceeded (FloodGate)") == 2


@pytest.mark.asyncio
async def test_successful_call_releases_the_breaker_trial(monkeypatch):
    """A granted half-open trial must not wedge when the call simply works."""

    class _TrialBreaker(_FakeBreaker):
        def __init__(self):
            super().__init__()
            self.trial_open = False

        def check(self, operation, label):
            if self.trial_open:
                raise runtime.TelegramOperationSuspendedError(operation, label, 1.0)
            self.trial_open = True  # grant one trial, pin it until record_success

        def record_success(self, operation, label):
            super().record_success(operation, label)
            self.trial_open = False

    breaker = _TrialBreaker()
    _install(monkeypatch, breaker=breaker)
    wrapped = runtime.with_account(readonly=True)(_tool([]))

    assert await wrapped(chat_id=1) == "ok"  # trial granted and settled
    assert await wrapped(chat_id=1) == "ok"  # released: no suspension after
    # Every completion settled a trial — none of them wedged.
    assert breaker.successes == [("fake_tool", _sole_label())] * 2


@pytest.mark.asyncio
async def test_floodwait_reply_keeps_the_recorded_flood(monkeypatch):
    """A FloodWait-shaped result must NOT settle the breaker as a success."""
    breaker = _FakeBreaker()

    async def flooded_tool(chat_id=None, account=None):
        # The real shape: the tool catches the FloodWait and formats it.
        return runtime.log_and_format_error(
            "flooded_tool", FloodWaitError(request=None, capture=42)
        )

    _install(monkeypatch, breaker=breaker)
    wrapped = runtime.with_account(readonly=True)(flooded_tool)

    await wrapped(chat_id=1)

    assert breaker.successes == []
    assert breaker.floods == [("flooded_tool", _sole_label())]


@pytest.mark.asyncio
async def test_gate_defer_releases_a_pinned_trial(monkeypatch):
    """check() pins a trial before try_acquire can defer: no call happens, so
    the deferral itself must release the trial instead of wedging it."""

    class _GrantAllBreaker(_FakeBreaker):
        def check(self, operation, label):
            pass  # never refuses: the gate is what defers here

    breaker = _GrantAllBreaker()
    _install(monkeypatch, gate=_FakeGate(deferred={_sole_label()}), breaker=breaker)
    wrapped = runtime.with_account(readonly=True)(_tool([]))

    result = await wrapped(chat_id=1)

    assert "Rate limit exceeded (FloodGate)" in result
    assert breaker.successes == [("fake_tool", _sole_label())]


@pytest.mark.asyncio
async def test_contextvar_carries_the_canonical_label(monkeypatch):
    seen = {}

    async def spy_tool(chat_id=None, account=None):
        seen["label"] = runtime._CURRENT_ACCOUNT.get()
        return "ok"

    raw = _sole_label()
    _install(monkeypatch)
    wrapped = runtime.with_account(readonly=True)(spy_tool)

    await wrapped(chat_id=1, account=raw.upper().replace("_", "-"))  # non-canonical spelling

    assert seen["label"] == raw  # breaker/gate keys stay canonical


def test_flood_report_is_inert_when_floodgate_disabled(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FLOODGATE", "0")
    breaker = _FakeBreaker()
    _install(monkeypatch, breaker=breaker)
    token = runtime._CURRENT_ACCOUNT.set(_sole_label())
    try:
        runtime._floodgate_report_flood("send_message", 300)
    finally:
        runtime._CURRENT_ACCOUNT.reset(token)

    assert breaker.floods == []


@pytest.mark.asyncio
async def test_real_gate_and_breaker_composed_with_the_wrapper(monkeypatch):
    """The real vendored objects (not fakes) behind the wrapper: the second
    send within the window must defer with no extra tool-body execution."""
    from telegram_mcp.floodgate import FloodCircuitBreaker, RateLimitSpec, TelegramRateLimitGate

    gate = TelegramRateLimitGate(
        category_limits={"send": RateLimitSpec(max_calls=1, window_sec=60.0)},
        jitter_func=lambda low, high: 0.0,
    )
    _install(monkeypatch, gate=gate, breaker=FloodCircuitBreaker())
    calls = []

    async def send_like_tool(chat_id=None, account=None):
        calls.append(account)
        return "ok"

    send_like_tool.__name__ = "send_message"  # maps to the constrained category
    wrapped = runtime.with_account(readonly=True)(send_like_tool)

    first = await wrapped(chat_id=1)
    second = await wrapped(chat_id=1)

    assert first == "ok"
    assert "Rate limit exceeded (FloodGate)" in second
    assert calls == [None]  # exactly one real execution


@pytest.mark.asyncio
async def test_shipped_history_bucket_defers_call_25(monkeypatch):
    """The shipped calibration end to end: real gate (default buckets, no
    overrides), real breaker, real tool-name mapping. get_messages maps to the
    history bucket (24/30s): calls 1-24 execute, call 25 is deferred locally
    and never reaches the tool body."""
    monkeypatch.setattr(runtime, "_floodgate_gate", None)  # _floodgate() builds the real pair
    monkeypatch.setattr(runtime, "_floodgate_breaker", None)
    calls = []
    tool = _tool(calls)
    tool.__name__ = "get_messages"  # the shipped name->category mapping
    wrapped = runtime.with_account(readonly=True)(tool)

    results = [await wrapped(chat_id=93372553) for _ in range(25)]

    assert results[:24] == ["ok"] * 24
    assert "Rate limit exceeded (FloodGate)" in results[24]
    assert len(calls) == 24  # the deferred call made no RPC


@pytest.mark.asyncio
async def test_breaker_suspension_outlives_a_single_retry(monkeypatch):
    """The breaker's own refusal must not settle the pair as a success: an
    OPEN suspension has to block more than one retry (real gate+breaker)."""
    monkeypatch.setattr(runtime, "_floodgate_gate", None)
    monkeypatch.setattr(runtime, "_floodgate_breaker", None)
    _, breaker = runtime._floodgate()  # the shipped pair
    for _ in range(3):
        breaker.record_flood("get_history", _sole_label())
    calls = []
    tool = _tool(calls)
    tool.__name__ = "get_history"
    wrapped = runtime.with_account(readonly=True)(tool)

    first = await wrapped(chat_id=1)
    second = await wrapped(chat_id=1)

    assert "Rate limit exceeded" in first
    assert "Rate limit exceeded" in second  # still suspended after the first refusal
    assert calls == []


@pytest.mark.asyncio
async def test_result_quoting_flood_text_does_not_wedge_the_trial(monkeypatch):
    """A successful call whose OUTPUT merely contains the FloodWait prefix
    (chat content quoting an old error) must still release the trial."""

    class _TrialBreaker(_FakeBreaker):
        def __init__(self):
            super().__init__()
            self.trial_open = False

        def check(self, operation, label):
            if self.trial_open:
                raise runtime.TelegramOperationSuspendedError(operation, label, 1.0)
            self.trial_open = True  # grant one trial, pinned until record_success

        def record_success(self, operation, label):
            super().record_success(operation, label)
            self.trial_open = False

    breaker = _TrialBreaker()
    _install(monkeypatch, breaker=breaker)
    wrapped = runtime.with_account(readonly=True)(
        _tool([], body=f"{runtime._FLOOD_WAIT_REPLY_PREFIX}: stale bot reply quoted in chat")
    )

    first = await wrapped(chat_id=1)
    second = await wrapped(chat_id=1)

    assert "stale bot reply" in first
    # The second call reached the tool again (no breaker refusal, which would
    # say "FloodGate"): the trial was released despite the misleading text.
    assert "FloodGate" not in second
    assert "stale bot reply" in second
    assert breaker.floods == []
