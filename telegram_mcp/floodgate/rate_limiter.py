# Copyright (c) 2026 Axisrow
#
# Vendored from https://github.com/axisrow/telethon-floodgate (MIT License)
# and modified for use in telegram-mcp. This file is distributed under the
# MIT License reproduced below; the telegram-mcp project as a whole remains
# Apache-2.0.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Per-account sliding-window rate limiter for live ``auth.resolveUsername``
calls (#551).

The reactive backoff added in #502 only kicks in *after* Telegram has already
returned a multi-hour ``FLOOD_WAIT_X``. By then the damage is done: production
logs show a single fresh session firing 160+ ``resolve_username`` calls in
seconds, escalating to 15–18 hour flood waits.

This limiter caps the *burst* before it happens. It is a pure in-memory,
per-account sliding window — no DB, no locks — so it is cheap to consult on the
hot resolve path. When an account exceeds its window the caller defers the
channel (short reschedule) instead of issuing the live API call.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from math import ceil, isfinite

# Telegram does not publish the ``auth.resolveUsername`` limit. Production
# evidence points at roughly 30 calls / account / minute before escalation can
# begin, so the default stays materially below that.
DEFAULT_MAX_CALLS = 20
DEFAULT_WINDOW_SEC = 60.0
DEFAULT_JITTER_SEC = 5.0
RESOLVE_USERNAME_BACKOFF_BUFFER_SEC = 5
GLOBAL_RESOLVE_BACKOFF_THRESHOLD_SEC = 300


class UsernameResolveFloodWaitDeferredError(RuntimeError):
    """Raised when username resolution is deferred by Flood Wait backoff."""

    def __init__(self, wait_seconds: int, next_available_at: datetime):
        super().__init__(
            "Username resolve is flood-waited until "
            f"{next_available_at.isoformat()} (retry in {wait_seconds}s)"
        )
        self.wait_seconds = wait_seconds
        self.next_available_at = next_available_at


class UsernameResolveRateLimitedError(RuntimeError):
    """Raised when a live username resolve is throttled before hitting Telegram."""

    def __init__(self, phone: str, retry_after_sec: float, *, now: datetime | None = None):
        retry_after_sec = max(0.0, float(retry_after_sec))
        retry_after_seconds = ceil(retry_after_sec)
        next_available_at = (now or datetime.now(timezone.utc)) + timedelta(
            seconds=retry_after_seconds
        )
        super().__init__(
            f"resolve_username rate-limited for {phone}; retry in {retry_after_seconds}s"
        )
        self.phone = phone
        self.retry_after_sec = retry_after_sec
        self.retry_after_seconds = retry_after_seconds
        self.next_available_at = next_available_at

    def run_after_with_buffer(
        self, buffer_sec: int = RESOLVE_USERNAME_BACKOFF_BUFFER_SEC
    ) -> datetime:
        return self.next_available_at + timedelta(seconds=buffer_sec)


class ResolveRateLimiter:
    """Sliding-window limiter keyed by account phone.

    ``try_acquire`` is the only method that mutates state: it prunes the
    window, and either records the call and returns ``0.0`` (allowed) or
    returns a positive number of seconds the caller should defer for. The
    deferral includes a small ``±jitter`` so that many accounts unblocking at
    the same instant do not re-burst in lockstep.
    """

    def __init__(
        self,
        *,
        max_calls: int = DEFAULT_MAX_CALLS,
        window_sec: float = DEFAULT_WINDOW_SEC,
        jitter_sec: float = DEFAULT_JITTER_SEC,
        time_func=time.monotonic,
        jitter_func=random.uniform,
        sustained_max_calls: int | None = None,
        sustained_window_sec: float | None = None,
    ) -> None:
        self._max_calls = max(1, int(max_calls))
        self._window_sec = float(window_sec)
        if not (isfinite(self._window_sec) and self._window_sec > 0):
            raise ValueError(
                "window_sec must be finite and > 0: a non-positive window "
                "prunes every entry instantly, a NaN one never defers — "
                "either way the limiter silently disables"
            )
        self._jitter_sec = max(0.0, float(jitter_sec))
        self._time = time_func
        self._jitter = jitter_func
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        # Sustained tier (tg_content_factory incident 2026-10-06): the burst
        # window alone admitted an unbounded *stream* of fully-legal calls
        # (20/min for 20+ minutes) and Telegram escalated to a 13.8h
        # FLOOD_WAIT. The second, wider window caps accumulated volume; the
        # recommended companion to the 20/60s burst is 60 calls / 3600s.
        # Disabled (None) keeps the 0.1.x behaviour bit-for-bit.
        if (sustained_max_calls is None) != (sustained_window_sec is None):
            raise ValueError("sustained_max_calls and sustained_window_sec must be set together")
        self._sustained_calls: dict[str, deque[float]] | None = None
        if sustained_max_calls is not None:
            sustained_window_sec_f = float(sustained_window_sec)  # type: ignore[arg-type]
            if not isfinite(sustained_window_sec_f):
                raise ValueError("sustained_window_sec must be finite")
            if sustained_window_sec_f < self._window_sec:
                raise ValueError(
                    "sustained_window_sec must be >= window_sec: the sustained "
                    "window cannot be narrower than the burst window"
                )
            self._sustained_max_calls = max(1, int(sustained_max_calls))
            self._sustained_window_sec = sustained_window_sec_f
            self._sustained_calls = defaultdict(deque)
        else:
            self._sustained_max_calls = 0
            self._sustained_window_sec = 0.0

    def _prune_window(
        self, store: dict[str, deque[float]], phone: str, now: float, window_sec: float
    ) -> deque[float]:
        window = store[phone]
        cutoff = now - window_sec
        while window and window[0] <= cutoff:
            window.popleft()
        return window

    def _prune(self, phone: str, now: float) -> deque[float]:
        return self._prune_window(self._calls, phone, now, self._window_sec)

    def try_acquire(self, phone: str) -> float:
        """Reserve one resolve slot for ``phone``.

        Returns ``0.0`` when the call is allowed (and records it). Otherwise
        returns the number of seconds to defer before retrying — the window is
        full and no slot is consumed.
        """
        return self.try_acquire_many(phone, 1)

    def try_acquire_many(self, phone: str, slots: int) -> float:
        """Atomically reserve ``slots`` calls for one compound operation.

        With a sustained tier configured, both windows are checked first and
        timestamps are recorded in both **only when both admit the call** —
        a deferred call burns no slots in any window (unlike composing two
        separate limiter instances, where a burst refusal after a sustained
        admission would burn a sustained slot).
        """
        slots = int(slots)
        if slots < 1:
            raise ValueError("slots must be at least 1")
        if slots > self._max_calls:
            raise ValueError("slots cannot exceed max_calls")
        if self._sustained_calls is not None and slots > self._sustained_max_calls:
            # Same contract as the burst check: without it, slots above the
            # sustained cap indexed an empty window and raised IndexError.
            raise ValueError("slots cannot exceed sustained_max_calls")

        now = self._time()
        window = self._prune(phone, now)
        burst_full = len(window) + slots > self._max_calls

        # Sustained first: when BOTH windows are full, the sustained retry
        # (~hours) is the honest defer — returning the ~60s burst retry here
        # would wake every deferred caller one extra cycle per window
        # (review finding on tg_content_factory#1498).
        sustained_window: deque[float] | None = None
        sustained_retry: float | None = None
        if self._sustained_calls is not None:
            sustained_window = self._prune_window(
                self._sustained_calls, phone, now, self._sustained_window_sec
            )
            if len(sustained_window) + slots > self._sustained_max_calls:
                calls_to_expire = len(sustained_window) + slots - self._sustained_max_calls
                sustained_retry = (
                    sustained_window[calls_to_expire - 1] + self._sustained_window_sec
                ) - now

        if burst_full:
            # Wait until enough calls have expired for the whole atomic request,
            # not merely until the oldest call leaves the window.
            calls_to_expire = len(window) + slots - self._max_calls
            retry_after = (window[calls_to_expire - 1] + self._window_sec) - now
            if sustained_retry is not None:
                retry_after = max(retry_after, sustained_retry)
            if self._jitter_sec:
                retry_after += self._jitter(0.0, self._jitter_sec)
            return max(retry_after, 0.0)
        if sustained_retry is not None:
            retry = sustained_retry
            if self._jitter_sec:
                retry += self._jitter(0.0, self._jitter_sec)
            return max(retry, 0.0)

        window.extend([now] * slots)
        if sustained_window is not None:
            sustained_window.extend([now] * slots)
        return 0.0

    def used(self, phone: str) -> int:
        """Burst-window calls recorded for ``phone`` (prunes first).

        Read-only: an unknown phone seeds no window entry (dashboards may
        ask about arbitrary accounts). A sustained tier, if configured, is
        not included — this reports the burst window only.
        """
        window = self._calls.get(phone)
        if not window:
            return 0
        return len(self._prune(phone, self._time()))

    def reset(self, phone: str | None = None) -> None:
        """Drop recorded history for ``phone`` (or all accounts)."""
        if phone is None:
            self._calls.clear()
            if self._sustained_calls is not None:
                self._sustained_calls.clear()
        else:
            self._calls.pop(phone, None)
            if self._sustained_calls is not None:
                self._sustained_calls.pop(phone, None)
