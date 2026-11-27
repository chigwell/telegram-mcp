"""Shared MCP Events filters and bounded reads of the existing Telegram event log."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import datetime, timezone

EVENT_NAME = "telegram.message.created"


class EventError(Exception):
    def __init__(self, message, code=-32602, reason=None, data=None):
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.data = data if data is not None else ({"reason": reason} if reason else None)


def utc_timestamp(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


def event_definition(delivery=("poll", "push")):
    return {
        "name": EVENT_NAME,
        "description": (
            "New message metadata in a selected Telegram chat, "
            "or explicitly all chats of one account."
        ),
        "delivery": list(delivery),
        "inputSchema": {
            "type": "object",
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        **_common_properties(),
                        "chat_id": {
                            "type": ["integer", "string"],
                            "description": "Chat ID, username or saved alias.",
                        },
                        "all_chats": {"const": False, "default": False},
                    },
                    "required": ["chat_id"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        **_common_properties(),
                        "all_chats": {
                            "const": True,
                            "description": "Explicitly monitor all allowed chats of this account.",
                        },
                    },
                    "required": ["all_chats"],
                    "additionalProperties": False,
                },
            ],
        },
        "payloadSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "chat_id": {"type": "integer"},
                "message_id": {"type": "integer"},
                "timestamp": {"type": "string", "format": "date-time"},
            },
            "required": ["account", "chat_id", "message_id", "timestamp"],
            "additionalProperties": False,
        },
    }


def _common_properties():
    return {
        "account": {
            "type": "string",
            "description": "Account label; required with multiple accounts.",
        },
        "include_outgoing": {"type": "boolean", "default": False},
    }


async def normalize_arguments(arguments, *, resolve=True):
    from telegram_mcp import runtime as rt
    from telegram_mcp.account_labels import canonical_account_label

    if not isinstance(arguments, dict) or set(arguments) - {
        "account",
        "chat_id",
        "all_chats",
        "include_outgoing",
    }:
        raise EventError("Invalid event arguments.")
    args = dict(arguments)
    if any(type(args.get(key, False)) is not bool for key in ("all_chats", "include_outgoing")):
        raise EventError("all_chats and include_outgoing must be booleans.")
    account = args.get("account")
    if resolve and account is None and len(rt.clients) == 1:
        account = next(iter(rt.clients))
    if account is not None and not isinstance(account, str):
        raise EventError("account must be a label.")
    args["account"] = canonical_account_label(account) if account is not None else None
    if resolve and args["account"] not in rt.clients:
        raise EventError("Select a configured account label.")
    args.setdefault("all_chats", False)
    args.setdefault("include_outgoing", False)
    if args["all_chats"]:
        if "chat_id" in args:
            raise EventError("Choose chat_id or all_chats, not both.")
    else:
        chat_id = args.get("chat_id")
        if (
            isinstance(chat_id, bool)
            or not isinstance(chat_id, (int, str))
            or not str(chat_id).strip()
        ):
            raise EventError("Select chat_id, or explicitly set all_chats=true.")
        if not resolve:
            return args
        args["chat_id"], username = await _resolve_chat(chat_id, args["account"])
        args["_chat_username"] = username
    return args


async def _resolve_chat(chat_id, account):
    from telegram_mcp import runtime as rt
    from telegram_mcp.tools import events

    entity = await rt.resolve_entity(rt.apply_alias(chat_id), rt.get_client(account))
    if not rt.is_chat_allowed(chat_id, entity):
        raise EventError("Chat access denied.", -32024)
    if events._chat_kind(entity) is None:
        raise EventError("Only private chats, groups and supergroups are supported.")
    return rt.get_marked_id(entity), getattr(entity, "username", None)


def event_allowed(args, record):
    from telegram_mcp import runtime as rt

    if args["account"] not in rt.clients:
        return False
    if record.get("account") != args["account"]:
        return False
    if not args["all_chats"] and record["chat_id"] != args["chat_id"]:
        return False
    if record.get("out") and not args["include_outgoing"]:
        return False
    chat = record.get("_chat")
    if chat is None and any(isinstance(x, str) for x in rt.get_blocked_chat_ids()):
        return False
    if not rt.is_chat_allowed(record["chat_id"], chat):
        return False
    topics = rt.get_allowed_topics(record["chat_id"], chat)
    return topics is None or record.get("topic") in topics


def message_event(record, position=None):
    stamp = record.get("date") or utc_timestamp(time.time())
    identity = f"{record['account']}:{record['chat_id']}:{record['message_id']}"
    return {
        "eventId": "evt_" + hashlib.sha256(identity.encode()).hexdigest(),
        "name": EVENT_NAME,
        "timestamp": stamp,
        "data": {
            "account": record["account"],
            "chat_id": record["chat_id"],
            "message_id": record["message_id"],
            "timestamp": stamp,
        },
        "cursor": position if position is not None else cursor(record["seq"]),
    }


def cursor(seq):
    from telegram_mcp.tools import events

    return f"v1.{events._event_boot}.{seq}"


def position(value):
    """Null starts at head; a lost buffer restarts at its oldest retained position."""
    from telegram_mcp.tools import events

    if value is None:
        return events._event_seq, False
    if not isinstance(value, str) or not re.fullmatch(r"v1\.[0-9a-f]{16}\.[0-9]{1,20}", value):
        raise EventError("Invalid event cursor.")
    _, boot, number = value.split(".")
    seq = int(number)
    oldest = events._event_log[0]["seq"] if events._event_log else events._event_seq + 1
    if boot != events._event_boot or seq < oldest - 1 or seq > events._event_seq:
        return oldest - 1, True
    return seq, False


def replay_options(params):
    age = params.get("maxAgeMs")
    if age is not None and (type(age) is not int or age < 0):
        raise EventError("maxAgeMs must be a non-negative integer.")
    return age


def read_page(args, after, limit=100, *, max_age=None, replay_head=None, replay_time=None):
    """Count matching occurrences, and never advance over an unread matching event."""
    from telegram_mcp.tools import events

    if type(limit) is not int or limit <= 0:
        raise EventError("maxEvents must be a positive integer.")
    limit = min(limit, 100)
    head = events._event_seq
    oldest = events._event_log[0]["seq"] if events._event_log else head + 1
    gap = after < oldest - 1
    after = max(after, oldest - 1)
    selected = []
    cutoff = (
        (time.time() if replay_time is None else replay_time) - max_age / 1000
        if max_age is not None
        else None
    )
    for record in events._event_log:
        if record["seq"] <= after or record["seq"] > head:
            continue
        matches = event_allowed(args, record)
        if matches and cutoff is not None and record["seq"] <= replay_head:
            stamp = datetime.fromisoformat(record["date"].replace("Z", "+00:00")).timestamp()
            if stamp < cutoff:
                after = record["seq"]
                gap = True
                continue
        if matches:
            if len(selected) == limit:
                return selected, after, gap, True
            selected.append(record)
        after = record["seq"]
    return selected, head, gap, False


def replay_start(args, after, **options):
    """Choose a safe replay boundary without advancing past an unread occurrence."""
    records, scanned, clipped, _ = read_page(args, after, **options)
    if clipped:
        return (records[0]["seq"] - 1 if records else scanned), True
    return after, False


def require_scope(args):
    """Recheck configured scope without reading Telegram history."""
    from telegram_mcp import runtime as rt

    if args["account"] not in rt.clients:
        raise EventError("Account access revoked.", -32024)
    if not args["all_chats"]:
        from types import SimpleNamespace

        identity = SimpleNamespace(username=args.get("_chat_username"))
        if not rt.is_chat_allowed(args["chat_id"], identity):
            raise EventError("Chat access revoked.", -32024)
