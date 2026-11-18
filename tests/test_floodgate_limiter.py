# Copyright (c) 2026 Axisrow
# Vendored from https://github.com/axisrow/telethon-floodgate (MIT License)
# and modified; full license text in telegram_mcp/floodgate/__init__.py.
"""Unit tests for the per-account resolve_username rate limiter (#551)."""

from __future__ import annotations

import pytest

from telegram_mcp.floodgate.rate_limiter import ResolveRateLimiter


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _limiter(clock: _FakeClock, *, max_calls: int = 3, window: float = 60.0, jitter: float = 0.0):
    return ResolveRateLimiter(
        max_calls=max_calls,
        window_sec=window,
        jitter_sec=jitter,
        time_func=clock,
        jitter_func=lambda _a, _b: 0.0,
    )


def test_allows_up_to_max_then_defers():
    clock = _FakeClock()
    limiter = _limiter(clock, max_calls=3, window=60.0)

    assert limiter.try_acquire("+1") == 0.0
    assert limiter.try_acquire("+1") == 0.0
    assert limiter.try_acquire("+1") == 0.0
    # Fourth call within the window is throttled.
    retry = limiter.try_acquire("+1")
    assert retry > 0.0
    # Defer until the oldest call (t=1000) exits the 60s window.
    assert retry == 60.0


def test_window_slides_and_frees_slots():
    clock = _FakeClock()
    limiter = _limiter(clock, max_calls=2, window=60.0)

    assert limiter.try_acquire("+1") == 0.0  # t=1000
    clock.advance(30)
    assert limiter.try_acquire("+1") == 0.0  # t=1030
    # Window full now.
    assert limiter.try_acquire("+1") > 0.0
    # Advance past the first call's expiry (1000 + 60 = 1060).
    clock.advance(31)  # t=1061
    assert limiter.try_acquire("+1") == 0.0


def test_multi_slot_retry_waits_until_all_required_slots_are_free():
    clock = _FakeClock()
    limiter = _limiter(clock, max_calls=3, window=300.0)

    assert limiter.try_acquire("+1") == 0.0  # t=1000
    clock.advance(100)
    assert limiter.try_acquire("+1") == 0.0  # t=1100
    clock.advance(100)
    assert limiter.try_acquire("+1") == 0.0  # t=1200
    clock.advance(50)

    # Two calls must expire (at t=1300 and t=1400), so retrying after only
    # the oldest call expires would still be rejected.
    assert limiter.try_acquire_many("+1", 2) == 150.0
    clock.advance(150)
    assert limiter.try_acquire_many("+1", 2) == 0.0


def test_limit_is_per_account():
    clock = _FakeClock()
    limiter = _limiter(clock, max_calls=1, window=60.0)

    assert limiter.try_acquire("+1") == 0.0
    assert limiter.try_acquire("+1") > 0.0  # +1 throttled
    # A different account has its own independent window.
    assert limiter.try_acquire("+2") == 0.0


def test_jitter_added_to_retry():
    clock = _FakeClock()
    limiter = ResolveRateLimiter(
        max_calls=1,
        window_sec=60.0,
        jitter_sec=5.0,
        time_func=clock,
        jitter_func=lambda _a, b: b,  # always max jitter
    )
    assert limiter.try_acquire("+1") == 0.0
    retry = limiter.try_acquire("+1")
    assert retry == 65.0  # 60s window + 5s jitter


def test_reset_clears_history():
    clock = _FakeClock()
    limiter = _limiter(clock, max_calls=1, window=60.0)

    assert limiter.try_acquire("+1") == 0.0
    assert limiter.try_acquire("+1") > 0.0
    limiter.reset("+1")
    assert limiter.try_acquire("+1") == 0.0

    # reset() with no arg clears all accounts.
    limiter.try_acquire("+2")
    limiter.reset()
    assert limiter.try_acquire("+2") == 0.0


class _TieredFakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _tiered(clock, *, burst_calls=20, sustained_calls=60):
    return ResolveRateLimiter(
        max_calls=burst_calls,
        window_sec=60.0,
        jitter_sec=0.0,
        time_func=clock,
        jitter_func=lambda _a, _b: 0.0,
        sustained_max_calls=sustained_calls,
        sustained_window_sec=3600.0,
    )


def test_sustained_tier_caps_accumulated_volume():
    """Sustained tier caps volume that the burst window admits legally.

    Production incident (tg_content_factory 2026-10-06): a cold collect of
    623 channels fired 20 resolves/min — every 60s burst window green — for
    20+ minutes until Telegram answered FLOOD_WAIT 49613s. 180 minutes of
    the same pattern must now yield at most one sustained window per hour.
    """
    clock = _TieredFakeClock()
    limiter = _tiered(clock)

    allowed = 0
    for _ in range(180):  # 3 hours, 20 "legal" calls per minute
        for _ in range(20):
            if limiter.try_acquire("+1") == 0.0:
                allowed += 1
        clock.now += 60.0

    assert allowed <= 180


def test_without_sustained_tier_volume_is_uncapped():
    """Back-compat: no sustained params -> 0.1.x behaviour (burst only)."""
    clock = _TieredFakeClock()
    limiter = ResolveRateLimiter(
        max_calls=20,
        window_sec=60.0,
        jitter_sec=0.0,
        time_func=clock,
        jitter_func=lambda _a, _b: 0.0,
    )

    allowed = 0
    for _ in range(180):
        for _ in range(20):
            if limiter.try_acquire("+1") == 0.0:
                allowed += 1
        clock.now += 60.0

    assert allowed == 3600


def test_sustained_window_slides():
    """After an hour the sustained budget is fully restored."""
    clock = _TieredFakeClock()
    # Wide burst window: isolate the sustained tier's behaviour.
    limiter = _tiered(clock, burst_calls=60)

    for _ in range(60):
        assert limiter.try_acquire("+1") == 0.0
    # Burst window is green again, but the sustained hour is exhausted.
    clock.now += 60.0
    assert limiter.try_acquire("+1") > 0.0
    # Sustained calls made at t=0 leave the sustained window at t=3600.
    clock.now = 3600.0
    assert limiter.try_acquire("+1") == 0.0


def test_sustained_tier_is_per_account():
    clock = _TieredFakeClock()
    limiter = _tiered(clock, sustained_calls=1)

    assert limiter.try_acquire("+1") == 0.0
    assert limiter.try_acquire("+1") > 0.0
    assert limiter.try_acquire("+2") == 0.0


def test_sustained_params_must_come_in_pair():
    with pytest.raises(ValueError):
        ResolveRateLimiter(sustained_max_calls=60)
    with pytest.raises(ValueError):
        ResolveRateLimiter(sustained_window_sec=3600.0)


def test_sustained_window_cannot_be_narrower_than_burst():
    with pytest.raises(ValueError):
        ResolveRateLimiter(sustained_max_calls=60, sustained_window_sec=30.0)


def test_reset_clears_sustained_window():
    clock = _TieredFakeClock()
    limiter = _tiered(clock, sustained_calls=1)

    assert limiter.try_acquire("+1") == 0.0
    assert limiter.try_acquire("+1") > 0.0
    limiter.reset("+1")
    assert limiter.try_acquire("+1") == 0.0


def test_slots_above_sustained_max_raise_instead_of_indexerror():
    limiter = _tiered(_TieredFakeClock(), sustained_calls=5)
    with pytest.raises(ValueError, match="sustained_max_calls"):
        limiter.try_acquire_many("+1", 6)


def test_non_positive_window_is_rejected():
    """A non-positive window prunes every entry instantly: a silent no-op."""
    with pytest.raises(ValueError, match="window_sec"):
        ResolveRateLimiter(max_calls=1, window_sec=0)
