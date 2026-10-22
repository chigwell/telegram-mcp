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

Tool names map onto gate categories through ``TOOL_CATEGORIES`` below
(exact names, unknown tools fall to the broad ``default`` bucket). The
upstream ``dialogs`` bucket (1/60s) is deliberately unused here: it was
calibrated for collector sweeps and would throttle a normal ``get_chats``.
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
    "TOOL_CATEGORIES",
    "TelegramOperationSuspendedError",
    "TelegramPeerRateLimitedError",
    "TelegramRateLimitedError",
    "TelegramRateLimitGate",
    "category_for_tool",
]

# Exact tool name -> gate category. Anything absent uses the broad "default"
# bucket (1000/60s) — reads, media, contacts, profile, folders, waits.
TOOL_CATEGORIES = {
    # send (30/min): everything that pushes content into a chat
    "send_message": "send",
    "reply_to_message": "send",
    "send_file": "send",
    "send_album": "send",
    "send_voice": "send",
    "send_sticker": "send",
    "send_gif": "send",
    "send_contact": "send",
    "send_scheduled_message": "send",
    "forward_message": "send",
    "forward_messages": "send",
    "edit_message": "send",
    "delete_message": "send",
    "delete_messages_bulk": "send",
    "pin_message": "send",
    "unpin_message": "send",
    "unpin_all_messages": "send",
    "save_draft": "send",
    "clear_draft": "send",
    "create_poll": "send",
    "vote_poll": "send",
    "send_reaction": "send",
    "remove_reaction": "send",
    "mark_as_read": "send",
    "send_typing": "send",
    "press_inline_button": "send",
    "upload_file": "send",
    # history (24/30s): high-volume reads of chat content
    "get_history": "history",
    "list_messages": "history",
    "get_messages": "history",
    "get_messages_after": "history",
    "get_message_context": "history",
    "search_messages": "history",
    "get_pinned_messages": "history",
    "get_scheduled_messages": "history",
    "delete_scheduled_message": "history",
    "export_unread_messages": "history",
    "get_message_reactions": "history",
    "get_message_read_by": "history",
    "list_inline_buttons": "history",
    # admin_action (10/min): chat-level mutations
    "ban_user": "admin_action",
    "unban_user": "admin_action",
    "remove_user": "admin_action",
    "promote_admin": "admin_action",
    "demote_admin": "admin_action",
    "edit_admin_rights": "admin_action",
    "set_default_chat_permissions": "admin_action",
    "toggle_slow_mode": "admin_action",
    "set_bot_commands": "admin_action",
    "set_default_send_as": "admin_action",
    "set_privacy_settings": "admin_action",
    "block_user": "admin_action",
    "unblock_user": "admin_action",
    "invite_to_group": "admin_action",
    "edit_chat_title": "admin_action",
    "edit_chat_photo": "admin_action",
    "edit_chat_about": "admin_action",
    "delete_chat_photo": "admin_action",
    "create_forum_topic": "admin_action",
    "delete_forum_topic": "admin_action",
    "edit_forum_topic": "admin_action",
    "export_chat_invite": "admin_action",
    # resolve_guard (20/60s, built in runtime._floodgate): username resolution
    # is the operation production showed escalating to 15-18h flood bans
    # (160+ auth.resolveUsername calls in seconds). NOT the vendored "resolve"
    # category — that one is a deliberate no-op reserved for upstream's
    # ResolveGuardMixin, which this server does not have.
    "resolve_username": "resolve_guard",
    "search_public_chats": "resolve_guard",
    # channel_lifecycle (3/5min): creating/joining/leaving/destroying chats
    "create_channel": "channel_lifecycle",
    "create_group": "channel_lifecycle",
    "upgrade_group_to_supergroup": "channel_lifecycle",
    "join_chat_by_link": "channel_lifecycle",
    "import_chat_invite": "channel_lifecycle",
    "subscribe_public_channel": "channel_lifecycle",
    "leave_chat": "channel_lifecycle",
    "delete_chat_history": "channel_lifecycle",
}


def category_for_tool(tool_name: str) -> str:
    """Gate category for a tool name; unknown tools take the default bucket."""
    return TOOL_CATEGORIES.get(tool_name, "default")
