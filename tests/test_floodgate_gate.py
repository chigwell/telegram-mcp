# Copyright (c) 2026 Axisrow
# Vendored from https://github.com/axisrow/telethon-floodgate (MIT License)
# and modified; full license text in telegram_mcp/floodgate/__init__.py.
from __future__ import annotations

import asyncio

import pytest

from telegram_mcp.floodgate.rate_limit_gate import (
    RateLimitSpec,
    TelegramPeerRateLimitedError,
    TelegramRateLimitedError,
    TelegramRateLimitGate,
    _effective_jitter,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _no_jitter(low: float, high: float) -> float:
    return 0.0


def test_dialogs_gate_is_per_phone_and_conservative() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    assert gate.try_acquire("+1", "dialogs") == 0.0
    assert gate.try_acquire("+2", "dialogs") == 0.0
    assert gate.try_acquire("+1", "dialogs") == 60.0


def test_categories_have_independent_buckets() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        category_limits={"history": RateLimitSpec(max_calls=1, window_sec=60)},
        time_func=clock,
        jitter_func=_no_jitter,
    )
    assert gate.try_acquire("+1", "dialogs") == 0.0
    assert gate.try_acquire("+1", "history") == 0.0
    assert gate.try_acquire("+1", "history") == 60.0


def test_resolve_and_reaction_keep_their_existing_dedicated_gates() -> None:
    gate = TelegramRateLimitGate(
        category_limits={"default": RateLimitSpec(max_calls=1, window_sec=60)},
    )

    for operation, expected_category in (
        ("telegram_resolve_entity", "resolve"),
        ("telegram_resolve_input_entity", "resolve"),
        ("telegram_send_reaction", "reaction"),
    ):
        category = gate.category_for(operation)
        assert category == expected_category
        assert gate.try_acquire("+1", category) == 0.0
        assert gate.try_acquire("+1", category) == 0.0


@pytest.mark.parametrize(
    "operation",
    (
        "resolve_channel_warm_dialog_cache",
        "fetch_channel_meta_warm_dialog_cache",
        "get_forum_topics_warm_dialog_cache",
        "search_warm_dialog_cache",
        "leave_channels:123_warm_dialog_cache",
        "delete_dialogs:123_warm_dialog_cache",
    ),
)
def test_decorated_dialog_operations_use_the_dialogs_bucket(operation: str) -> None:
    assert TelegramRateLimitGate.category_for(operation) == "dialogs"


@pytest.mark.parametrize(
    "operation",
    ("telegram_stream_dialogs", "resume_stream_dialogs"),
)
def test_dialog_sweep_has_its_own_bucket(operation: str) -> None:
    """A sweep is one operation continued across passes, not repeated calls.

    Sharing the warm-up's 1/min bucket meant the second pass of a resumable
    sweep was always refused, so the sweep could never resume (#1359). The
    warm-up keeps its own strict bucket — that is what #1330 needs.
    """
    assert TelegramRateLimitGate.category_for(operation) == "dialog_sweep"
    assert TelegramRateLimitGate.category_for("telegram_warm_dialog_cache") == "dialogs"


def test_phase_two_categories_are_separately_calibrated() -> None:
    gate = TelegramRateLimitGate()
    assert gate.category_for("telegram_stream_messages") == "history"
    assert gate.category_for("telegram_edit_admin") == "admin_action"
    assert gate.category_for("telegram_send_message") == "send"
    assert gate.category_for("telegram_publish_files") == "send"
    assert gate.category_for("telegram_create_channel") == "channel_lifecycle"
    assert gate.category_for("telegram_import_chat_invite") == "channel_lifecycle"
    assert gate.category_for("telegram_delete_chat") == "channel_lifecycle"

    # A history stream must not consume a write-operation slot.
    assert gate.try_acquire("+1", "history") == 0.0
    assert gate.try_acquire("+1", "send") == 0.0


def test_history_uses_empirical_window_with_conservative_margin() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    for _ in range(24):
        assert gate.try_acquire("+1", "history") == 0.0
    assert gate.try_acquire("+1", "history") == 30.0

    clock.now += 30.0
    assert gate.try_acquire("+1", "history") == 0.0


def test_compound_slot_reservation_is_atomic() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    assert gate.try_acquire("+1", "channel_lifecycle", slots=2) == 0.0
    assert gate.try_acquire("+1", "channel_lifecycle", slots=2) == 300.0
    # The rejected two-slot reservation must not consume the one remaining slot.
    assert gate.try_acquire("+1", "channel_lifecycle") == 0.0


# --- per-peer send limits --------------------------------------------------


def test_peer_user_bucket_allows_one_per_1_1_seconds() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0
    assert gate.try_acquire("+1", "send", peer="user:42") > 0.0
    # The conservative 1.1s window slides after the configured interval.
    clock.now += 1.1
    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0


def test_peer_refusal_does_not_burn_the_category_slot() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        category_limits={"send": RateLimitSpec(max_calls=2, window_sec=60)},
        time_func=clock,
    )

    # A successful call consumes BOTH its peer slot and one category slot.
    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0
    # The refused call burns neither: the peer bucket rejects it first and the
    # account-wide category keeps its remaining slot.
    assert gate.try_acquire("+1", "send", peer="user:42") > 0.0
    assert gate.try_acquire("+1", "send") == 0.0
    # The peer bucket passes for a fresh peer, but the category is now full:
    # deferral with a retry hint, not a silent pass.
    assert gate.try_acquire("+1", "send", peer="user:43") > 0.0


def test_peer_channel_bucket_allows_sixteen_per_minute() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    for _ in range(16):
        assert gate.try_acquire("+1", "send", peer="channel:-100123") == 0.0
    assert gate.try_acquire("+1", "send", peer="channel:-100123") > 0.0
    # A classic chat has the same shape of limit, in its own bucket.
    assert gate.try_acquire("+1", "send", peer="chat:7") == 0.0


def test_peer_buckets_are_per_phone() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0
    assert gate.try_acquire("+1", "send", peer="user:42") > 0.0
    assert gate.try_acquire("+2", "send", peer="user:42") == 0.0


def test_unknown_peer_kind_is_not_limited() -> None:
    """``send:unknown`` has no peer bucket — only the category still applies."""
    clock = _Clock()
    gate = TelegramRateLimitGate(
        category_limits={"send": RateLimitSpec(max_calls=1000, window_sec=60)},
        time_func=clock,
    )

    for _ in range(50):
        assert gate.try_acquire("+1", "send", peer="id:999") == 0.0


def test_peer_limits_are_configurable() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        peer_limits={"send:user": RateLimitSpec(max_calls=5, window_sec=60)},
        time_func=clock,
    )

    for _ in range(5):
        assert gate.try_acquire("+1", "send", peer="user:42") == 0.0
    assert gate.try_acquire("+1", "send", peer="user:42") > 0.0


def test_peer_buckets_are_bounded_by_lru_eviction() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(peer_max_buckets=2, time_func=clock)

    assert gate.try_acquire("+1", "send", peer="user:1") == 0.0
    assert gate.try_acquire("+1", "send", peer="user:2") == 0.0
    assert gate.try_acquire("+1", "send", peer="user:3") == 0.0  # evicts user:1
    assert gate.try_acquire("+1", "send", peer="user:1") == 0.0  # fresh bucket
    assert gate.try_acquire("+1", "send", peer="user:3") > 0.0  # survivor stays limited


def test_reset_clears_peer_buckets_for_one_phone() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)

    gate.try_acquire("+1", "send", peer="user:42")
    gate.try_acquire("+2", "send", peer="user:42")
    gate.reset("+1")
    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0
    assert gate.try_acquire("+2", "send", peer="user:42") > 0.0
    gate.reset()
    assert gate.try_acquire("+2", "send", peer="user:42") == 0.0


def test_peer_error_is_a_rate_limit_error() -> None:
    """Existing TelegramRateLimitedError handlers must absorb the peer flavour."""
    exc = TelegramPeerRateLimitedError("+7", "user:42", 3.5)

    assert isinstance(exc, TelegramRateLimitedError)
    assert exc.peer == "user:42"
    assert exc.category == "send_peer"
    assert exc.retry_after_sec == 3.5


@pytest.mark.asyncio
async def test_acquire_rechecks_after_early_wakeup_and_reserves() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    gate.try_acquire("+1", "dialogs")
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        clock.now += seconds / 2 if len(sleeps) == 1 else seconds

    await gate.acquire("+1", "dialogs", sleep=sleep)
    # The 60s defer is slept in capped 30s slices (ACQUIRE_SLEEP_CAP_SEC) —
    # the last slice is the remainder — so a reset() mid-wait cannot strand
    # the sleeper; every wake re-checks.
    assert sleeps == [30.0, 30.0, 15.0]
    assert gate.try_acquire("+1", "dialogs") == 60.0


@pytest.mark.asyncio
async def test_concurrent_acquire_never_admits_unrecorded_waiters() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    waiters = []
    admitted = []

    async def sleep(seconds):
        future = asyncio.get_running_loop().create_future()
        waiters.append((seconds, future))
        await future

    async def acquire():
        await gate.acquire("+1", "dialogs", sleep=sleep)
        admitted.append(clock.now)

    await acquire()  # fills the window
    tasks = [asyncio.create_task(acquire()) for _ in range(2)]
    await asyncio.sleep(0)
    assert len(waiters) == 2
    clock.now += 60
    for _, future in waiters[:]:
        future.set_result(None)
    await asyncio.sleep(0)
    assert admitted == [1000.0, 1060.0]
    assert len(waiters) == 3  # the losing waiter must wait again
    clock.now += 60
    waiters[-1][1].set_result(None)
    await asyncio.gather(*tasks)
    assert admitted == [1000.0, 1060.0, 1120.0]


@pytest.mark.asyncio
async def test_cancelling_acquire_does_not_reserve_or_block_others() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    await gate.acquire("+1", "dialogs")
    sleeping = asyncio.Event()

    async def sleep(seconds):
        sleeping.set()
        await asyncio.Future()

    task = asyncio.create_task(gate.acquire("+1", "dialogs", sleep=sleep))
    await sleeping.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    clock.now += 60
    assert gate.try_acquire("+1", "dialogs") == 0.0


# --- adaptive flood backoff -------------------------------------------------


def _flooded_gate(clock: _Clock, **kwargs: object) -> TelegramRateLimitGate:
    options: dict = {
        "category_limits": {"send": RateLimitSpec(max_calls=1, window_sec=60)},
        "flood_backoff": True,
        "time_func": clock,
        "jitter_func": _no_jitter,
    }
    options.update(kwargs)
    return TelegramRateLimitGate(**options)


def test_flood_backoff_doubles_defers_after_note_flood() -> None:
    clock = _Clock()
    gate = _flooded_gate(clock)
    assert gate.try_acquire("+1", "send") == 0.0
    assert gate.note_flood("+1", 90.0) == 2.0
    assert gate.try_acquire("+1", "send") == 120.0


def test_transient_pacing_floods_do_not_escalate() -> None:
    """≤60s waits are Telegram's pacing protocol, not a calibration failure.

    Three routine 27-30s sweep floods must not silence the whole phone at
    the 8x cap; only blocking waits (or unknown severity) escalate.
    """
    clock = _Clock()
    gate = _flooded_gate(clock)
    gate.try_acquire("+1", "send")
    gate.note_flood("+1", 30.0)
    assert gate.try_acquire("+1", "send") == 60.0
    assert gate.note_flood("+1") == 2.0  # unknown severity counts conservatively


def test_flood_backoff_is_opt_in() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        category_limits={"send": RateLimitSpec(max_calls=1, window_sec=60)},
        time_func=clock,
        jitter_func=_no_jitter,
    )
    gate.try_acquire("+1", "send")
    assert gate.note_flood("+1", 90.0) == 1.0
    assert gate.try_acquire("+1", "send") == 60.0


def test_flood_backoff_escalates_and_caps() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(flood_backoff=True, time_func=clock)
    assert gate.note_flood("+1", 90.0) == 2.0
    assert gate.note_flood("+1", 90.0) == 4.0
    assert gate.note_flood("+1", 90.0) == 8.0
    assert gate.note_flood("+1", 90.0) == 8.0  # capped at the default cap


def test_flood_backoff_validates_params() -> None:
    with pytest.raises(ValueError):
        TelegramRateLimitGate(flood_backoff=True, flood_backoff_cap=0.5)
    with pytest.raises(ValueError):
        TelegramRateLimitGate(flood_backoff=True, flood_decay_sec=0.0)


def test_flood_backoff_survives_a_flood_storm() -> None:
    """1024+ events inside the decay window must clamp, not overflow float.

    try_acquire never raises — the pow is clamped BEFORE min() with the cap,
    so a tight report/retry loop (1024 events/hour is reachable at 1-3s
    floods) cannot crash the gate during the storm.
    """
    clock = _Clock()
    gate = _flooded_gate(clock)
    multiplier = 1.0
    for _ in range(1100):
        multiplier = gate.note_flood("+1")  # severity unknown: counts
    assert multiplier == 8.0
    gate.try_acquire("+1", "send")
    assert gate.try_acquire("+1", "send") == 480.0


def test_category_scoped_reset_keeps_flood_events() -> None:
    clock = _Clock()
    gate = _flooded_gate(clock)
    gate.note_flood("+1", 90.0)
    gate.reset(category="send")
    assert gate.snapshot("+1")["flood_multiplier"] == 2.0
    gate.reset()
    assert gate.snapshot("+1")["flood_multiplier"] == 1.0


def test_flood_backoff_scales_peer_defers_too() -> None:
    clock = _Clock()
    gate = _flooded_gate(
        clock,
        peer_limits={"send:user": RateLimitSpec(max_calls=1, window_sec=1.1)},
    )
    gate.try_acquire("+1", "send", peer="user:42")
    gate.note_flood("+1", 90.0)
    assert gate.try_acquire("+1", "send", peer="user:42") == pytest.approx(2.2)


def test_flood_backoff_is_per_phone() -> None:
    clock = _Clock()
    gate = _flooded_gate(clock)
    gate.try_acquire("+1", "send")
    gate.try_acquire("+2", "send")
    gate.note_flood("+1", 90.0)
    assert gate.try_acquire("+1", "send") == 120.0
    assert gate.try_acquire("+2", "send") == 60.0


def test_flood_backoff_decays_after_the_window() -> None:
    clock = _Clock()
    gate = _flooded_gate(clock)
    gate.note_flood("+1", 90.0)
    clock.now += 3600.0
    assert gate.snapshot("+1")["flood_multiplier"] == 1.0


def test_flood_backoff_leaves_zero_defers_at_zero() -> None:
    clock = _Clock()
    gate = _flooded_gate(clock)
    gate.note_flood("+1", 90.0)
    # A fresh window still admits: backoff lengthens defers, it invents
    # no refusals.
    assert gate.try_acquire("+1", "dialogs") == 0.0


def test_reset_clears_flood_events_for_one_phone() -> None:
    clock = _Clock()
    gate = _flooded_gate(clock)
    gate.note_flood("+1", 90.0)
    gate.note_flood("+2", 90.0)
    gate.reset("+1")
    assert gate.snapshot("+1")["flood_multiplier"] == 1.0
    assert gate.snapshot("+2")["flood_multiplier"] == 2.0
    gate.reset()
    assert gate.snapshot("+2")["flood_multiplier"] == 1.0


def test_reset_scopes_peer_buckets_to_the_category() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        peer_limits={"history:user": RateLimitSpec(max_calls=1, window_sec=60)},
        time_func=clock,
    )
    gate.try_acquire("+1", "send", peer="user:42")
    gate.try_acquire("+1", "history", peer="user:42")
    gate.reset("+1", category="send")
    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0  # cleared
    assert gate.try_acquire("+1", "history", peer="user:42") > 0.0  # untouched


# --- snapshot ---------------------------------------------------------------


def test_snapshot_reports_category_and_peer_usage() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    gate.try_acquire("+1", "history")
    gate.try_acquire("+1", "send", peer="user:42")

    snap = gate.snapshot("+1")

    assert snap["flood_backoff"] is False
    assert snap["flood_multiplier"] == 1.0
    assert snap["categories"]["history"] == {
        "max_calls": 24,
        "window_sec": 30.0,
        "jitter_sec": 1.5,
        "used": 1,
    }
    assert snap["categories"]["send"]["used"] == 1
    assert snap["peer_buckets"] == {
        "send:user:42": {
            "max_calls": 1,
            "window_sec": 1.1,
            "jitter_sec": 0.15,
            "used": 1,
        },
    }


def test_snapshot_scopes_to_one_phone() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    gate.try_acquire("+1", "history")

    snap = gate.snapshot("+2")

    assert snap["categories"]["history"]["used"] == 0
    assert snap["peer_buckets"] == {}


def test_peer_user_spec_jitters_by_default() -> None:
    assert TelegramRateLimitGate.SEND_PEER_USER_SPEC.jitter_sec == 0.15


def test_every_category_ships_default_jitter() -> None:
    """The lockstep re-burst fix must cover all categories, not just peers.

    Iterates the authoritative registry (not a hand list), so a newly added
    category without jitter fails here.
    """
    gate = TelegramRateLimitGate()
    specs = list(gate._category_specs.values()) + list(gate._peer_specs.values())
    assert len(specs) >= 11
    for spec in specs:
        assert _effective_jitter(spec) > 0.0, spec


def test_category_jitter_applies_through_the_single_mechanism() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        time_func=clock, jitter_func=lambda low, high: high  # jitter = full value
    )
    gate.try_acquire("+1", "dialogs")
    # Window defer + the packaged derived default, via the limiter path; the
    # expectation tracks the spec so recalibrating the ratio stays green.
    spec = TelegramRateLimitGate.DIALOGS_SPEC
    assert gate.try_acquire("+1", "dialogs") == spec.window_sec + _effective_jitter(spec)


def test_category_jitter_is_per_category_configurable() -> None:
    clock = _Clock()
    gate = TelegramRateLimitGate(
        category_limits={"dialogs": RateLimitSpec(max_calls=1, window_sec=60, jitter_sec=0)},
        time_func=clock,
    )
    gate.try_acquire("+1", "dialogs")
    assert gate.try_acquire("+1", "dialogs") == 60.0


def test_reset_scopes_peer_buckets_even_without_a_phone() -> None:
    """reset(None, category=X) must not wipe other categories' peer buckets —
    a category-scoped unblock used to silently disable per-peer pacing for
    every account (the opposite of what 0.1.3 documented)."""
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    gate.try_acquire("+1", "send", peer="user:42")

    gate.reset(category="history")
    assert "send:user:42" in gate.snapshot("+1")["peer_buckets"]
    assert gate.try_acquire("+1", "send", peer="user:42") > 0.0  # pacing intact

    gate.reset(category="send")
    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0  # cleared


def test_reset_tolerates_noop_and_unknown_categories() -> None:
    """resolve/reaction are no-op categories, "send_peer" is the category on
    TelegramPeerRateLimitedError — reset() must absorb them, not KeyError."""
    gate = TelegramRateLimitGate(time_func=_Clock())
    gate.reset("+1", category="resolve")
    gate.reset("+1", category="reaction")
    gate.reset("+1", category="send_peer")
    gate.reset(None, category="resolve")
    gate.reset("+1", category="not_a_category")


def test_reset_maps_send_peer_category_onto_send() -> None:
    """TelegramPeerRateLimitedError carries category="send_peer" while the
    refusing buckets live under "send": the endorsed unblock
    reset(err.phone, category=err.category) must clear the bucket, not
    silently match nothing."""
    clock = _Clock()
    gate = TelegramRateLimitGate(time_func=clock, jitter_func=_no_jitter)
    gate.try_acquire("+1", "send", peer="user:42")
    gate.try_acquire("+2", "send", peer="user:42")

    gate.reset("+1", category="send_peer")

    assert gate.try_acquire("+1", "send", peer="user:42") == 0.0  # cleared
    assert gate.try_acquire("+2", "send", peer="user:42") > 0.0  # other phone intact


def test_peer_specs_validate_window_at_construction() -> None:
    """A degenerate peer window must fail at gate construction like category
    specs do, not raise from inside try_acquire mid-flight (0.1.3 silently
    disabled the pacer for such specs)."""
    with pytest.raises(ValueError, match="send:user"):
        TelegramRateLimitGate(
            peer_limits={"send:user": RateLimitSpec(max_calls=1, window_sec=0.0)}
        )
