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

"""Proactive Telegram operation rate limiting: per-account and per-peer.

The category values below are deliberately boring guardrails rather than a
claim that Telegram publishes quotas (it does not).  They are calibrated to
the observed production shape: history is a high-volume read path, while
admin and channel-lifecycle calls are sparse writes.  Keep them configurable
so a new production sample can be applied without changing call sites.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from math import isfinite

# vendored-modification: upstream imports this from flood_wait.py, which is
# not part of this port (telegram-mcp returns rate-limit strings to the agent
# instead of auto-retrying). The upstream value is kept verbatim.
TRANSIENT_FLOOD_WAIT_MAX_SEC = 60

from telegram_mcp.floodgate.rate_limiter import ResolveRateLimiter

logger = logging.getLogger(__name__)

# One sweep = the initial pass plus its resumptions. Kept in step with
# DIALOG_FETCH_MAX_PASSES in pool_dialogs (12) so the gate can never be the
# thing that truncates a sweep the loop is still willing to continue; the
# loop's own limiters (max passes, total time budget, no-progress check) and
# the flood breaker are what bound it.
DIALOG_SWEEP_MAX_CALLS = 12
# A logical dialogs operation reserves the conservative ``dialogs`` slot once.
# Its internal paginated requests use this separate safety budget so a normal
# multi-page account can finish without consuming another logical-operation
# slot. This is an implementation guard, not Phase 2 quota calibration.
DIALOG_PAGE_MAX_CALLS = 1000


@dataclass(frozen=True)
class RateLimitSpec:
    max_calls: int
    window_sec: float
    # Defer jitter in seconds. None (default) derives window_sec * 0.05 — the
    # anti-lockstep default spreads retries so accounts unblocking at the
    # same instant do not re-burst in lockstep. 0.0 disables explicitly.
    jitter_sec: float | None = None


def _effective_jitter(spec: RateLimitSpec) -> float:
    """Resolved defer jitter: the explicit value, or 5% of the window."""
    return spec.window_sec * 0.05 if spec.jitter_sec is None else spec.jitter_sec


class TelegramRateLimitedError(RuntimeError):
    """Raised when an operation is deferred before making a Telegram call."""

    def __init__(self, phone: str, category: str, retry_after_sec: float) -> None:
        super().__init__(
            f"Telegram {category} rate-limited for {phone}; retry in {retry_after_sec:.1f}s"
        )
        self.phone = phone
        self.category = category
        self.retry_after_sec = retry_after_sec


class TelegramPeerRateLimitedError(TelegramRateLimitedError):
    """Raised when a per-peer bucket defers an operation before the call.

    A subclass so every existing ``TelegramRateLimitedError`` handler — "this
    operation is unavailable right now, move on" — absorbs it unchanged.
    """

    def __init__(self, phone: str, peer: str, retry_after_sec: float) -> None:
        super().__init__(phone, "send_peer", retry_after_sec)
        self.peer = peer


_OPERATION_CATEGORIES = {
    "telegram_warm_dialog_cache": "dialogs",
    # A sweep is ONE user-facing operation that Telethon splits into ~N/100
    # paginated requests under a single reservation, and a flood mid-pagination
    # means "slow down", not "stop" (Telethon documents this outright). Sharing
    # the warm-up's 1/min bucket meant the second pass was always refused, so
    # the resumable sweep could never resume (#1359).
    "telegram_stream_dialogs": "dialog_sweep",
    # Live username resolution is already protected by ResolveGuardMixin.
    # Cached entity lookups share these operation tags, so generic throttling
    # must stay disabled for the whole transport operation rather than risk a
    # second, conflicting limiter on the live path.
    "telegram_resolve_entity": "resolve",
    "telegram_resolve_input_entity": "resolve",
    "telegram_stream_messages": "history",
    "telegram_edit_admin": "admin_action",
    "telegram_edit_permissions": "admin_action",
    "telegram_kick_participant": "admin_action",
    "telegram_edit_folder": "admin_action",
    "telegram_send_message": "send",
    "telegram_publish_files": "send",
    "telegram_edit_message": "send",
    "telegram_forward_messages": "send",
    "telegram_pin_message": "send",
    # _ensure_reaction_can_run remains the sole reaction gate in Phase 1.
    "telegram_send_reaction": "reaction",
    "telegram_create_channel": "channel_lifecycle",
    "telegram_update_channel_username": "channel_lifecycle",
    "telegram_join_channel": "channel_lifecycle",
    "telegram_import_chat_invite": "channel_lifecycle",
    "telegram_delete_channel": "channel_lifecycle",
    "telegram_delete_chat": "channel_lifecycle",
}


def _category_for_operation(operation: str) -> str:
    """Return a category for both canonical and decorated operation tags.

    Warm operations are decorated with the caller name (for example
    ``resolve_channel_warm_dialog_cache``) so that flood diagnostics retain
    their useful context.  Matching the stable suffix prevents those paths
    from silently falling back to the broad default bucket.
    """
    exact = _OPERATION_CATEGORIES.get(operation)
    if exact is not None:
        return exact
    if operation.endswith("_stream_dialogs"):
        return "dialog_sweep"
    if operation.endswith("_warm_dialog_cache"):
        return "dialogs"
    return "default"


class TelegramRateLimitGate:
    """Registry of independent sliding-window buckets keyed by phone/category."""

    # acquire() re-checks the gate at most this often while deferring: a
    # flood-multiplied defer can reach many minutes, and sleeping it in one
    # shot would make reset() — the standard unblock action — unable to wake
    # the sleeper until the whole defer elapses.
    ACQUIRE_SLEEP_CAP_SEC = 30.0

    # Defer jitter: specs leave jitter_sec unset, deriving 5% of the window
    # (anti-lockstep — uniform(0, jitter) added to every defer by the single
    # limiter mechanism). Starting ratio — recalibrate against production
    # samples; per-category override stays jitter_sec on the spec, 0.0 = off.
    DEFAULT_SPEC = RateLimitSpec(max_calls=1000, window_sec=60.0)
    # #1330 showed repeated getDialogs floods even with multi-minute pauses.
    # Keep this deliberately low until production logs calibrate the value.
    DIALOGS_SPEC = RateLimitSpec(max_calls=1, window_sec=60.0)
    # A dialog sweep is one operation continued across passes, not repeated
    # calls: each pass resumes from the cursor with DIFFERENT offsets, which is
    # not the "same method, same parameters" shape error 420 is defined
    # against. It needs room for the initial pass plus resumptions after the
    # transient floods Telegram uses to pace pagination (27-30s in our logs,
    # inside this window). Still bounded -- a sweep that makes no progress is
    # stopped by the flood breaker (#1372) and the loop's own no-progress
    # check, not by starving it here.
    DIALOG_SWEEP_SPEC = RateLimitSpec(max_calls=DIALOG_SWEEP_MAX_CALLS, window_sec=60.0)
    DIALOG_PAGE_SPEC = RateLimitSpec(max_calls=DIALOG_PAGE_MAX_CALLS, window_sec=60.0)
    # messages.getHistory empirical boundary: 30 requests in roughly 30s on
    # the calibrated account/channel (31st request returned FLOOD_WAIT_3).
    # Keep a 20% margin; Telegram does not publish this quota.
    HISTORY_SPEC = RateLimitSpec(max_calls=24, window_sec=30.0)
    ADMIN_ACTION_SPEC = RateLimitSpec(max_calls=10, window_sec=60.0)
    SEND_SPEC = RateLimitSpec(max_calls=30, window_sec=60.0)
    CHANNEL_LIFECYCLE_SPEC = RateLimitSpec(max_calls=3, window_sec=300.0)
    # Per-peer send limits (community-observed, not published by Telegram):
    # roughly one message per second to the same private chat and about
    # twenty per minute into the same group or channel. Applied as a second,
    # INDEPENDENT bucket on top of the per-account ``send`` category — an
    # account bursting into many different peers is still bounded by the
    # category, while an account hammering one peer is stopped long before it.
    # A small margin absorbs clock/network variation around the one-second
    # observation; the live test proved this pacing on a real user peer. The
    # explicit 0.15s jitter (~13% of this tight window, above the derived 5%)
    # keeps retried sends from firing metronome-precisely on the boundary.
    SEND_PEER_USER_SPEC = RateLimitSpec(max_calls=1, window_sec=1.1, jitter_sec=0.15)
    SEND_PEER_CHANNEL_SPEC = RateLimitSpec(max_calls=16, window_sec=60.0)
    SEND_PEER_CHAT_SPEC = RateLimitSpec(max_calls=16, window_sec=60.0)

    def __init__(
        self,
        *,
        category_limits: dict[str, RateLimitSpec] | None = None,
        peer_limits: dict[str, RateLimitSpec] | None = None,
        peer_max_buckets: int = 4096,
        time_func: Callable[[], float] | None = None,
        jitter_func: Callable[[float, float], float] | None = None,
        flood_backoff: bool = False,
        flood_backoff_cap: float = 8.0,
        flood_decay_sec: float = 3600.0,
    ) -> None:
        if flood_backoff_cap < 1.0:
            raise ValueError(
                "flood_backoff_cap must be >= 1.0: a smaller cap silently "
                "disables the backoff instead of tightening it"
            )
        if flood_decay_sec < 1.0:
            raise ValueError("flood_decay_sec must be >= 1.0")
        specs = {
            "dialogs": self.DIALOGS_SPEC,
            "dialog_sweep": self.DIALOG_SWEEP_SPEC,
            "dialogs_page": self.DIALOG_PAGE_SPEC,
            "history": self.HISTORY_SPEC,
            "admin_action": self.ADMIN_ACTION_SPEC,
            "send": self.SEND_SPEC,
            "channel_lifecycle": self.CHANNEL_LIFECYCLE_SPEC,
            "default": self.DEFAULT_SPEC,
        }
        specs.update(category_limits or {})
        # Resolve None-jitter to the window-derived default once, so limiter
        # construction and snapshot read the same effective values.
        specs = {
            category: replace(spec, jitter_sec=_effective_jitter(spec))
            for category, spec in specs.items()
        }
        # One clock for the gate and every limiter: mixing an injected gate
        # clock with limiter-side monotonic compares timestamps from two
        # timelines and miscomputes defers — exactly under injected clocks.
        self._time_func: Callable[[], float] = (
            time_func if time_func is not None else time.monotonic
        )
        self._jitter_func = jitter_func  # None = limiter default (random.uniform)
        self._limiters = {
            category: ResolveRateLimiter(
                max_calls=spec.max_calls,
                window_sec=spec.window_sec,
                jitter_sec=_effective_jitter(spec),
                time_func=self._time_func,
                **({"jitter_func": jitter_func} if jitter_func is not None else {}),
            )
            for category, spec in specs.items()
        }
        self._category_specs = dict(specs)
        # Adaptive backoff: every FLOOD_WAIT the consumer reports via
        # note_flood doubles the defers this gate hands out for that phone
        # until the events decay out of the window. Static windows assume the
        # calibration holds; a server-side flood is proof it currently does
        # not. Opt-in — it changes returned defers — like the sustained tier.
        self._flood_backoff = flood_backoff
        self._flood_backoff_cap = float(flood_backoff_cap)
        self._flood_decay_sec = float(flood_decay_sec)
        self._flood_events: dict[str, deque[float]] = defaultdict(deque)
        # Per-peer specs are keyed "<category>:<kind>" (e.g. "send:user"); the
        # kind prefix comes from the peer key built by telethon_floodgate.peer.
        # An unconfigured pair has no per-peer bucket, so passing a peer for it
        # degrades to category-only limiting — the safe default.
        self._peer_specs: dict[str, RateLimitSpec] = {
            "send:user": self.SEND_PEER_USER_SPEC,
            "send:channel": self.SEND_PEER_CHANNEL_SPEC,
            "send:chat": self.SEND_PEER_CHAT_SPEC,
        }
        self._peer_specs.update(peer_limits or {})
        self._peer_specs = {
            key: replace(spec, jitter_sec=_effective_jitter(spec))
            for key, spec in self._peer_specs.items()
        }
        for key, spec in self._peer_specs.items():
            if not (isfinite(spec.window_sec) and spec.window_sec > 0):
                # Fail here like category specs do (their limiters are built
                # in __init__): surfacing a degenerate peer window at request
                # time would raise from inside try_acquire and break its
                # never-raises contract.
                raise ValueError(f"peer spec {key!r}: window_sec must be finite and > 0")
        self._peer_max_buckets = max(1, int(peer_max_buckets))
        self._peer_buckets: OrderedDict[tuple[str, str, str], ResolveRateLimiter] = OrderedDict()

    @staticmethod
    def category_for(operation: str) -> str:
        # resolve is explicitly a no-op category: ResolveGuardMixin owns it.
        return _category_for_operation(operation)

    def note_flood(self, phone: str, seconds: float | None = None) -> float:
        """Record a server-side FLOOD_WAIT; returns the current defer multiplier.

        Usually fed automatically via ``handle_flood_wait(..., gate=...)`` —
        do NOT also call it from a pool-report hook then, or events
        double-count. Only waits above TRANSIENT_FLOOD_WAIT_MAX_SEC escalate:
        routine ≤60s pacing floods are Telegram's sweep protocol working as
        designed (see DIALOG_SWEEP_SPEC), while a blocking wait is proof the
        static calibration is stale for this phone. ``seconds=None`` (severity
        unknown) counts conservatively.
        """
        if not self._flood_backoff:
            return 1.0
        if seconds is not None and seconds <= TRANSIENT_FLOOD_WAIT_MAX_SEC:
            return self._flood_multiplier(phone)
        self._flood_events[phone].append(self._time_func())
        return self._flood_multiplier(phone)

    def snapshot(self, phone: str) -> dict[str, object]:
        """Live gate state for one account — dashboards/health endpoints.

        Event-loop-affine: prune-and-count mirrors the bookkeeping the next
        ``try_acquire`` would do anyway, but the walk over peer buckets is
        not thread-safe — call from the gate's loop, not a dashboard thread.
        """
        categories: dict[str, dict[str, object]] = {}
        for category, spec in self._category_specs.items():
            categories[category] = {
                "max_calls": spec.max_calls,
                "window_sec": spec.window_sec,
                "jitter_sec": spec.jitter_sec,
                "used": self._limiters[category].used(phone),
            }
        peers: dict[str, dict[str, object]] = {}
        for key, limiter in self._peer_buckets.items():
            bucket_phone, category, peer = key
            if bucket_phone != phone:
                continue
            spec = self._peer_spec_for(category, peer)
            if spec is None:  # unreachable: buckets exist only for configured specs
                continue
            peers[f"{category}:{peer}"] = {
                "max_calls": spec.max_calls,
                "window_sec": spec.window_sec,
                # The peer limiter bakes this into its defer before the flood
                # multiplier scales it — a dashboard predicting defers from
                # max_calls/window_sec alone would undershoot send:user.
                "jitter_sec": spec.jitter_sec,
                "used": limiter.used(phone),
            }
        # Prune-then-count in one place: the multiplier call evicts decayed
        # events, so the len() below is the true in-window count no matter
        # how the dict literal is refactored.
        flood_multiplier = self._flood_multiplier(phone)
        flood_events = self._flood_events.get(phone)
        return {
            "flood_backoff": self._flood_backoff,
            "flood_multiplier": flood_multiplier,
            "flood_events_in_window": len(flood_events) if flood_events else 0,
            "categories": categories,
            "peer_buckets": peers,
        }

    def _flood_multiplier(self, phone: str) -> float:
        if not self._flood_backoff:
            return 1.0
        events = self._flood_events.get(phone)
        if not events:
            return 1.0
        cutoff = self._time_func() - self._flood_decay_sec
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            # Decay drained the phone's history — drop the key so long-dead
            # accounts do not linger in state (only reset() would otherwise).
            self._flood_events.pop(phone, None)
            return 1.0
        # Clamp the exponent before the pow: 2.0 ** 1024 overflows float and
        # would raise inside try_acquire — exactly during a flood storm. The
        # cap collapses anything above it anyway.
        return min(2.0 ** min(len(events), 60), self._flood_backoff_cap)

    def try_acquire(
        self, phone: str, category: str, *, slots: int = 1, peer: str | None = None
    ) -> float:
        if category in {"resolve", "reaction"}:
            return 0.0
        # A reported flood scales every defer for this phone until the events
        # decay; 0.0 stays 0.0 — backoff slows callers, it invents nothing.
        multiplier = self._flood_multiplier(phone)
        if peer is not None:
            peer_retry_after = self._try_acquire_peer(phone, category, peer, slots)
            if peer_retry_after > 0:
                # The tighter per-peer bucket refused first and its slot for
                # the broad category is left untouched, so a burst aimed at
                # one peer cannot burn the account-wide budget.
                return peer_retry_after * multiplier
        limiter = self._limiters.get(category, self._limiters["default"])
        return limiter.try_acquire_many(phone, slots) * multiplier

    async def acquire(
        self,
        phone: str,
        category: str,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Wait for one category slot; a deferral reserves nothing.

        Re-check after every sleep, including when another waiter took the
        available slot first. Cancellation propagates without reserving a slot.
        Per-peer/compound reservations retain the explicit ``try_acquire`` API.
        """
        while (retry_after := self.try_acquire(phone, category)) > 0:
            logger.info("%s: rate-limit gate defers %.1fs", category, retry_after)
            await sleep(min(retry_after, self.ACQUIRE_SLEEP_CAP_SEC))

    def _drop_peer_buckets(self, phone: str | None, category: str | None) -> None:
        if phone is None and category is None:
            self._peer_buckets.clear()
            return
        stale = [
            key
            for key in self._peer_buckets
            if (phone is None or key[0] == phone) and (category is None or key[1] == category)
        ]
        for key in stale:
            del self._peer_buckets[key]

    def reset(self, phone: str | None = None, category: str | None = None) -> None:
        if category is None:
            for limiter in self._limiters.values():
                limiter.reset(phone)
            # Flood events have no category dimension — they follow the full
            # or per-phone reset only; a category-scoped reset must not drop
            # every account's accumulated backoff as a side effect.
            if phone is None:
                self._flood_events.clear()
            else:
                self._flood_events.pop(phone, None)
        else:
            # "send_peer" is what TelegramPeerRateLimitedError carries as its
            # category, but the buckets it refuses live under "send": map the
            # endorsed unblock reset(err.phone, category=err.category) onto
            # the real category instead of silently matching nothing.
            if category == "send_peer":
                category = "send"
            # Unknown/no-op categories ("resolve", "reaction", typos) own no
            # bucket here: a no-op beats a KeyError on the consumer's unblock
            # path.
            limiter = self._limiters.get(category)
            if limiter is not None:
                limiter.reset(phone)
        self._drop_peer_buckets(phone, category)

    def _peer_spec_for(self, category: str, peer: str) -> RateLimitSpec | None:
        kind = peer.split(":", 1)[0] if ":" in peer else ""
        return self._peer_specs.get(f"{category}:{kind}")

    def _try_acquire_peer(self, phone: str, category: str, peer: str, slots: int) -> float:
        """Reserve a slot in the (phone, category, peer) bucket.

        Returns ``0.0`` when allowed (and records it) or the seconds to defer.
        Buckets are created lazily and kept in LRU order, bounded by
        ``peer_max_buckets``; eviction may briefly re-allow an idle peer — the
        bound trades that for a hard cap on memory.
        """
        spec = self._peer_spec_for(category, peer)
        if spec is None:
            return 0.0
        key = (phone, category, peer)
        limiter = self._peer_buckets.get(key)
        if limiter is None:
            limiter = ResolveRateLimiter(
                max_calls=spec.max_calls,
                window_sec=spec.window_sec,
                jitter_sec=_effective_jitter(spec),
                time_func=self._time_func,
                **({"jitter_func": self._jitter_func} if self._jitter_func is not None else {}),
            )
            self._peer_buckets[key] = limiter
            while len(self._peer_buckets) > self._peer_max_buckets:
                self._peer_buckets.popitem(last=False)
        else:
            self._peer_buckets.move_to_end(key)
        return limiter.try_acquire_many(phone, slots)
