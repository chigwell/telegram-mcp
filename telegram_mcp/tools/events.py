"""Event-driven incoming-message tracking + debounce (settle window).

Lets agents react to new client messages instead of polling. A Telethon
NewMessage(incoming=True) handler records incoming private (non-bot, non-self)
messages per chat; the two tools below expose them, with wait_for_settled_message
debouncing a burst (several messages typed in a row) into a single settled event.
"""

import asyncio
import json
import time
import logging
from typing import Any, Dict, Optional

from telethon import events as _events
from telethon import utils

from telegram_mcp.runtime import *  # mcp, clients, ToolAnnotations, log_and_format_error

# chat_id -> {first_ts, last_ts, count, first_id, last_id, name, username}
_pending_msgs: Dict[int, Dict[str, Any]] = {}
_activity_event: Optional[asyncio.Event] = None


def _get_activity_event() -> asyncio.Event:
    """Lazily create the asyncio.Event on the running loop."""
    global _activity_event
    if _activity_event is None:
        _activity_event = asyncio.Event()
    return _activity_event


async def _on_new_incoming(event) -> None:
    """Record incoming private (non-bot, non-self) messages for the debounce tools."""
    try:
        if not event.is_private:
            return
        sender = await event.get_sender()
        if sender is None:
            return
        if getattr(sender, "bot", False) or getattr(sender, "is_self", False):
            return
        chat_id = event.chat_id
        now = time.time()
        msg_id = event.message.id
        rec = _pending_msgs.get(chat_id)
        if rec is None:
            _pending_msgs[chat_id] = {
                "first_ts": now,
                "last_ts": now,
                "count": 1,
                "first_id": msg_id,
                "last_id": msg_id,
                "name": utils.get_display_name(sender) or str(chat_id),
                "username": getattr(sender, "username", None),
            }
        else:
            rec["last_ts"] = now
            rec["last_id"] = msg_id
            rec["count"] += 1
        _get_activity_event().set()
    except Exception:
        logging.getLogger("telegram_mcp").exception("error in _on_new_incoming")


def register_incoming_handlers() -> None:
    """Attach the incoming-message handler to every configured client.

    Safe to call before clients connect — Telethon registers the handler and
    delivers events once connected. Called at import time so the package's
    `import telegram_mcp.tools` registration also wires up the listener.
    """
    for cl in clients.values():
        try:
            cl.add_event_handler(_on_new_incoming, _events.NewMessage(incoming=True))
        except Exception:
            logging.getLogger("telegram_mcp").exception("failed to register incoming handler")


@mcp.tool(
    annotations=ToolAnnotations(
        title="Wait For New Message", openWorldHint=True, readOnlyHint=True
    )
)
async def wait_for_new_message(timeout: float = 50.0) -> str:
    """
    Block until a new incoming private message from a non-bot user arrives, then
    return immediately with the list of chats that currently have pending
    (unprocessed) incoming messages. If nothing arrives within `timeout` seconds,
    returns {"event": false, "reason": "timeout"}. Lets the agent react to events
    instead of polling. Does NOT consume the pending set — use
    wait_for_settled_message to consume a debounced burst.

    Args:
        timeout: Max seconds to block (default 50).
    """
    try:
        ev = _get_activity_event()
        if not _pending_msgs:
            ev.clear()
            try:
                await asyncio.wait_for(ev.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return json.dumps({"event": False, "reason": "timeout"}, ensure_ascii=False)
        chats = [
            {
                "chat_id": cid,
                "name": rec["name"],
                "username": rec["username"],
                "count": rec["count"],
                "last_message_id": rec["last_id"],
            }
            for cid, rec in _pending_msgs.items()
        ]
        return json.dumps({"event": True, "pending_chats": chats}, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error("wait_for_new_message", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Wait For Settled Message", openWorldHint=True, readOnlyHint=True
    )
)
async def wait_for_settled_message(settle_ms: int = 6000, max_wait_ms: int = 50000) -> str:
    """
    Event-driven, DEBOUNCED wait. Blocks until some private user chat has received
    one or more incoming messages AND then gone quiet for `settle_ms` — so a client
    who types several messages (or sends file + text) in a row is delivered as ONE
    settled burst instead of waking the agent on every message. Returns that chat's
    burst summary and removes it from the pending set, so the next call returns the
    next settled chat. If no chat settles within `max_wait_ms`, returns
    {"event": false, "reason": "timeout"} (caller should simply call again).

    Recommended usage (replaces blind per-minute polling): call this, get a settled
    chat, process it (read full history -> draft -> notify -> mark read), call again.

    Args:
        settle_ms: Quiet period after the LAST message before a burst is "settled"
            (default 6000 = 6s). Each new message in the chat resets this timer.
        max_wait_ms: Max total time to block before returning a timeout (default 50000).
    """
    try:
        settle = settle_ms / 1000.0
        deadline = time.time() + max_wait_ms / 1000.0
        ev = _get_activity_event()
        while True:
            now = time.time()
            settled_cid = None
            soonest_remaining = None
            for cid, rec in list(_pending_msgs.items()):
                quiet = now - rec["last_ts"]
                if quiet >= settle:
                    settled_cid = cid
                    break
                rem = settle - quiet
                if soonest_remaining is None or rem < soonest_remaining:
                    soonest_remaining = rem
            if settled_cid is not None:
                rec = _pending_msgs.pop(settled_cid)
                return json.dumps(
                    {
                        "event": True,
                        "chat_id": settled_cid,
                        "name": rec["name"],
                        "username": rec["username"],
                        "message_count": rec["count"],
                        "first_message_id": rec["first_id"],
                        "last_message_id": rec["last_id"],
                        "burst_seconds": round(rec["last_ts"] - rec["first_ts"], 2),
                    },
                    ensure_ascii=False,
                )
            remaining_total = deadline - now
            if remaining_total <= 0:
                return json.dumps({"event": False, "reason": "timeout"}, ensure_ascii=False)
            if soonest_remaining is not None:
                # A chat is pending but not yet quiet — sleep until it would settle,
                # then re-check (a new message meanwhile resets its timer).
                await asyncio.sleep(min(soonest_remaining, remaining_total))
            else:
                # Nothing pending — block on new activity until deadline.
                ev.clear()
                try:
                    await asyncio.wait_for(ev.wait(), timeout=remaining_total)
                except asyncio.TimeoutError:
                    return json.dumps({"event": False, "reason": "timeout"}, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error("wait_for_settled_message", e)


# Wire up the listener as soon as this module is imported (alongside tool registration).
register_incoming_handlers()


__all__ = ["wait_for_new_message", "wait_for_settled_message", "register_incoming_handlers"]
