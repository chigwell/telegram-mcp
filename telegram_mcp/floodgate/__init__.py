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

"""Flood-prevention layer for the tool runtime (vendored telethon-floodgate).

Two layers guard the Telegram account behind this MCP server:

1. ``TelegramRateLimitGate`` — a proactive sliding-window bucket per
   (account, category), checked BEFORE the RPC. Categories are calibrated
   against production incidents (see rate_limit_gate.py).
2. ``FloodCircuitBreaker`` — a reactive breaker per (tool, account): repeated
   flood waits suspend the pair instead of hammering a half-limited endpoint.

The runtime wires these in telegram_mcp.runtime: every ``with_account`` tool
checks the gate first and, on a deferral, returns a rate-limit string to the
agent instead of making the call; real FloodWaitErrors feed the breaker.

Tools declare their gate category with the ``@flood_category`` decorator on
the tool definitions themselves (telegram_mcp/tools/*), so the mapping is
discovered from the tools and cannot drift; undecorated tools fall to the
broad ``default`` bucket. The upstream ``dialogs`` bucket (1/60s) is
deliberately unused here: it was calibrated for collector sweeps and would
throttle a normal ``get_chats``.
"""

from telegram_mcp.floodgate.flood_breaker import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_FLOOD_THRESHOLD,
    FloodCircuitBreaker,
    TelegramOperationSuspendedError,
)
from telegram_mcp.floodgate.rate_limit_gate import (
    RateLimitSpec,
    TelegramPeerRateLimitedError,
    TelegramRateLimitedError,
    TelegramRateLimitGate,
)
from telegram_mcp.floodgate.rate_limiter import ResolveRateLimiter

__all__ = [
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_FLOOD_THRESHOLD",
    "FloodCircuitBreaker",
    "RateLimitSpec",
    "ResolveRateLimiter",
    "TelegramOperationSuspendedError",
    "TelegramPeerRateLimitedError",
    "TelegramRateLimitedError",
    "TelegramRateLimitGate",
]
