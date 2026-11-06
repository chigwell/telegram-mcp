"""Local pacing for resolve_phone, opt-in through TELEGRAM_RESOLVE_PHONE_* settings.

Telegram publishes no quota for contacts.resolvePhone and answers bulk lookups with long
FloodWaits, so an operator can cap lookups below what their account tolerates. Every
attempt that reaches Telegram counts, whatever it returns, inside a rolling 24-hour window.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Callable, Final, List, NamedTuple, Optional

from telegram_mcp.runtime import _alias_lock

WINDOW_SECONDS: Final[float] = 24 * 60 * 60.0
MAX_PER_DAY_CEILING: Final[int] = 10_000

MAX_PER_DAY_ENV: Final[str] = "TELEGRAM_RESOLVE_PHONE_MAX_PER_DAY"
MIN_INTERVAL_ENV: Final[str] = "TELEGRAM_RESOLVE_PHONE_MIN_INTERVAL_SECONDS"
STATE_FILE_ENV: Final[str] = "TELEGRAM_RESOLVE_PHONE_STATE_FILE"


class LimitConfigError(ValueError):
    """A TELEGRAM_RESOLVE_PHONE_* value is malformed; lookups stay refused until it is fixed."""


class LimitStateUnreadable(Exception):
    """The state file exists but cannot be trusted, so the remaining budget is unknown."""


class ResolvePhoneLimits(NamedTuple):
    max_per_day: int  # 0 = no daily cap
    min_interval: float  # 0 = no spacing between lookups
    state_file: Optional[Path]

    @property
    def enabled(self) -> bool:
        return self.max_per_day > 0 or self.min_interval > 0


def limits_from_env() -> ResolvePhoneLimits:
    """Read the limits on every call so a changed environment applies without a restart."""
    raw_max = os.getenv(MAX_PER_DAY_ENV, "").strip()
    raw_interval = os.getenv(MIN_INTERVAL_ENV, "").strip()
    raw_state = os.getenv(STATE_FILE_ENV, "").strip()
    try:
        max_per_day = int(raw_max) if raw_max else 0
    except ValueError:
        max_per_day = -1
    if not 0 <= max_per_day <= MAX_PER_DAY_CEILING:
        raise LimitConfigError(
            f"{MAX_PER_DAY_ENV} must be a whole number from 0 to {MAX_PER_DAY_CEILING}."
        )
    try:
        min_interval = float(raw_interval) if raw_interval else 0.0
    except ValueError:
        min_interval = -1.0
    if not 0 <= min_interval <= WINDOW_SECONDS:
        raise LimitConfigError(
            f"{MIN_INTERVAL_ENV} must be a number of seconds from 0 to {int(WINDOW_SECONDS)}."
        )
    return ResolvePhoneLimits(max_per_day, min_interval, Path(raw_state) if raw_state else None)


def _load_attempts(path: Path) -> List[float]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except ValueError as error:
        raise LimitStateUnreadable() from error
    attempts = raw.get("attempts") if isinstance(raw, dict) else None
    if (
        not isinstance(attempts, list)
        or len(attempts) > MAX_PER_DAY_CEILING
        or not all(
            isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t)
            for t in attempts
        )
    ):
        raise LimitStateUnreadable()
    return [float(t) for t in attempts]


def _save_attempts(path: Path, attempts: List[float]) -> None:
    # mkstemp creates a fresh 0600 file; os.replace leaves the old state intact on a crash.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"attempts": attempts}, f)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _admit(
    attempts: List[float], limits: ResolvePhoneLimits, now: float
) -> tuple[Optional[str], List[float]]:
    """Return (refusal, attempts to keep). A refusal records no new attempt."""
    # A timestamp ahead of the clock (clock moved back) is pulled back to "now" and kept
    # that way: still counted, but never holding lookups back for more than one window.
    recent = sorted(min(t, now) for t in attempts if now - t < WINDOW_SECONDS)
    refusals = []
    if limits.min_interval and recent:
        reason = f"lookups are spaced {limits.min_interval:g} s apart"
        refusals.append((recent[-1] + limits.min_interval - now, reason))
    if limits.max_per_day and len(recent) >= limits.max_per_day:
        frees_at = recent[len(recent) - limits.max_per_day] + WINDOW_SECONDS
        reason = f"the limit of {limits.max_per_day} lookups per 24 h is used up"
        refusals.append((frees_at - now, reason))
    wait, reason = max(refusals, default=(0.0, ""))
    if wait > 0:
        return _refusal(reason, wait), recent
    return None, (recent + [now])[-max(limits.max_per_day, 1) :]


def _refusal(reason: str, wait_seconds: float) -> str:
    return (
        f"Error: resolve_phone refused locally ({reason}); no request was sent to Telegram. "
        f"Retry in {max(1, math.ceil(wait_seconds))} seconds, not sooner."
    )


class ResolvePhoneLimiter:
    """Admit or refuse one lookup.

    reserve() is synchronous on purpose: checking and recording an attempt with no await
    in between is what keeps two parallel tool calls in one event loop from both passing.
    Keep it await-free. Other processes sharing the state file are serialised by the
    file lock.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        # Wall-clock time, because persisted timestamps must survive a restart.
        self._clock = clock
        self._attempts: List[float] = []

    def reserve(self) -> Optional[str]:
        """Record an attempt and return None, or return why the lookup is refused."""
        try:
            limits = limits_from_env()
        except LimitConfigError as error:
            return f"Error: resolve_phone is disabled until the configuration is fixed: {error}"
        if not limits.enabled:
            return None
        if limits.state_file is None:
            refusal, self._attempts = _admit(self._attempts, limits, self._clock())
            return refusal
        return self._reserve_in_file(limits.state_file, limits)

    def _reserve_in_file(self, path: Path, limits: ResolvePhoneLimits) -> Optional[str]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # The file lock also covers other server processes that share this state file.
            with _alias_lock(path):
                loaded = _load_attempts(path)
                refusal, kept = _admit(loaded, limits, self._clock())
                if kept != loaded:
                    _save_attempts(path, kept)
                return refusal
        except LimitStateUnreadable:
            return (
                f"Error: resolve_phone refused: the file in {STATE_FILE_ENV} is not valid "
                "lookup state, so the remaining budget is unknown. Fix or delete the file "
                "(deleting it resets the count)."
            )
        except OSError:
            return (
                f"Error: resolve_phone refused: the file in {STATE_FILE_ENV} cannot be "
                "read or written, so this lookup could not be counted."
            )
