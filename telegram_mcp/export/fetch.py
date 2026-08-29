"""Resolving chats and streaming their history into normalized records."""

import datetime as dt
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from telethon import utils as tl_utils
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    ReactionCustomEmoji,
    ReactionEmoji,
    User,
)

from . import media as media_mod
from .entities import entities_to_json
from .util import log


def display_name(entity: Any) -> Optional[str]:
    if entity is None:
        return None
    title = getattr(entity, "title", None)
    if title:
        return title
    first = getattr(entity, "first_name", "") or ""
    last = getattr(entity, "last_name", "") or ""
    name = f"{first} {last}".strip()
    return name or getattr(entity, "username", None) or None


def entity_kind(entity: Any) -> str:
    if isinstance(entity, User):
        return "bot" if getattr(entity, "bot", False) else "user"
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    return "group"


async def resolve_target(client: Any, target: str) -> Any:
    """Accept @username, t.me link, numeric id, or a chat title (exact, then
    case-insensitive substring over the dialog list)."""
    raw = target.strip()
    if not raw:
        raise SystemExit("Empty chat target.")

    if raw.startswith("https://t.me/") or raw.startswith("t.me/"):
        raw = "@" + raw.rsplit("/", 1)[-1]

    if raw.startswith("@") or (
        raw.lstrip("-").isdigit() is False and " " not in raw and "." in raw
    ):
        try:
            return await client.get_entity(raw)
        except Exception:
            pass

    if raw.lstrip("-").isdigit():
        try:
            return await client.get_entity(int(raw))
        except Exception as exc:
            raise SystemExit(f"Cannot resolve chat id {raw}: {exc}")

    try:
        return await client.get_entity(raw)
    except Exception:
        pass

    needle = raw.casefold()
    matches = []
    async for dialog in client.iter_dialogs():
        title = (dialog.name or "").casefold()
        if title == needle:
            return dialog.entity
        if needle in title:
            matches.append(dialog)
    if len(matches) == 1:
        return matches[0].entity
    if not matches:
        raise SystemExit(
            f"No chat matches '{target}'. Run 'telegram-mcp-export chats' to see the list."
        )
    listing = "\n".join(f"  {d.id}\t{d.name}" for d in matches[:15])
    raise SystemExit(
        f"'{target}' matches {len(matches)} chats - be more specific or use an id:\n{listing}"
    )


async def chat_meta(client: Any, entity: Any) -> dict:
    return {
        "id": tl_utils.get_peer_id(entity),
        "raw_id": getattr(entity, "id", None),
        "type": entity_kind(entity),
        "title": display_name(entity),
        "username": getattr(entity, "username", None),
        "phone": getattr(entity, "phone", None),
        "is_self": bool(getattr(entity, "is_self", False)),
    }


async def _anchor_min_id(client: Any, entity: Any, since: Optional[dt.datetime]) -> int:
    """Message id just before ``since``.

    Telegram's ``offset_date`` returns the newest message strictly older than
    the date, which makes an exact ``min_id`` for a forward (chronological)
    scan. Without it a date filter would have to walk the whole history.
    """
    if since is None:
        return 0
    found = await client.get_messages(entity, limit=1, offset_date=since)
    return found[0].id if found else 0


def _reactions(message: Any) -> list[dict]:
    reactions = getattr(message, "reactions", None)
    if not reactions or not getattr(reactions, "results", None):
        return []
    out = []
    for item in reactions.results:
        reaction = getattr(item, "reaction", None)
        if isinstance(reaction, ReactionEmoji):
            key = reaction.emoticon
        elif isinstance(reaction, ReactionCustomEmoji):
            key = f"custom:{reaction.document_id}"
        else:
            key = str(reaction)
        out.append({"reaction": key, "count": getattr(item, "count", None)})
    return out


def _forward(message: Any) -> Optional[dict]:
    fwd = getattr(message, "forward", None)
    if fwd is None:
        return None
    return {
        "from_id": getattr(fwd, "sender_id", None),
        "from_name": getattr(fwd, "from_name", None)
        or display_name(getattr(fwd, "sender", None))
        or display_name(getattr(fwd, "chat", None)),
        "date": getattr(fwd, "date", None),
        "channel_post": getattr(fwd, "channel_post", None),
        "post_author": getattr(fwd, "post_author", None),
    }


def _reply(message: Any) -> Optional[dict]:
    reply = getattr(message, "reply_to", None)
    if reply is None:
        return None
    return {
        "reply_to_msg_id": getattr(reply, "reply_to_msg_id", None),
        "top_msg_id": getattr(reply, "reply_to_top_id", None),
        "forum_topic": bool(getattr(reply, "forum_topic", False)),
    }


def _service(message: Any) -> Optional[dict]:
    action = getattr(message, "action", None)
    if action is None:
        return None
    payload = {"action": type(action).__name__}
    for field in ("title", "users", "user_id", "duration", "photo", "message"):
        value = getattr(action, field, None)
        if value is not None and field != "photo":
            payload[field] = value if not isinstance(value, list) else list(value)
    return payload


async def iter_records(
    client: Any,
    entity: Any,
    *,
    since: Optional[dt.datetime],
    until: Optional[dt.datetime],
    min_id: int = 0,
    media_root: Optional[Path] = None,
    media_max_bytes: Optional[int] = None,
    transcribe_engine: Optional[str] = None,
    include_raw: bool = True,
    progress_every: int = 200,
    people: Optional[dict] = None,
) -> AsyncIterator[dict]:
    """Yield chat history oldest-first as plain dicts ready for JSONL.

    ``people``, when given, is filled with every sender id seen -> display
    name, so the caller can write a participant map next to the messages.
    """
    anchor = await _anchor_min_id(client, entity, since)
    floor = max(min_id, anchor)
    count = 0
    if people is None:
        people = {}
    # Imported here, not at module scope: telegram_mcp.transcription pulls in
    # runtime, which reads the API credentials at import time.
    from telegram_mcp import transcription

    iterator = client.iter_messages(entity, reverse=True, min_id=floor)
    while True:
        try:
            message = await iterator.__anext__()
        except StopAsyncIteration:
            break
        except FloodWaitError as exc:
            log(f"  … Telegram asked to wait {exc.seconds}s (flood limit); sleeping.")
            import asyncio

            await asyncio.sleep(exc.seconds + 1)
            continue

        if until is not None and message.date and message.date > until:
            break

        sender = getattr(message, "sender", None)
        sender_id = getattr(message, "sender_id", None)
        sender_name = display_name(sender)
        if sender_id and sender_name:
            people[sender_id] = sender_name

        record: dict[str, Any] = {
            "id": message.id,
            "date": message.date,
            "edit_date": getattr(message, "edit_date", None),
            "from_id": sender_id,
            "from_name": sender_name,
            "from_username": getattr(sender, "username", None),
            "outgoing": bool(getattr(message, "out", False)),
            "text": message.message or "",
            "entities": entities_to_json(getattr(message, "entities", None)),
            "reply": _reply(message),
            "forward": _forward(message),
            "via_bot_id": getattr(message, "via_bot_id", None),
            "reactions": _reactions(message),
            "views": getattr(message, "views", None),
            "pinned": bool(getattr(message, "pinned", False)),
            "service": _service(message),
            "media": None,
            "transcript": None,
        }

        kind = media_mod.classify(message)
        if kind:
            info = media_mod.describe(message, kind)
            if media_root is not None:
                info = await media_mod.download(client, message, info, media_root, media_max_bytes)
            record["media"] = info

        if transcribe_engine and transcription.is_transcribable(message):
            result = await transcription.transcribe(client, entity, message, transcribe_engine)
            if result.get("status") == "ok":
                record["transcript"] = {
                    "text": result["text"],
                    "engine": transcribe_engine,
                    "lang": result.get("lang"),
                    "duration": transcription.voice_duration(message),
                }
            else:
                record["transcript"] = {
                    "text": None,
                    "engine": transcribe_engine,
                    "error": result.get("error") or result.get("status"),
                    "duration": transcription.voice_duration(message),
                }

        if include_raw:
            record["raw"] = message.to_dict()

        count += 1
        if progress_every and count % progress_every == 0:
            stamp = message.date.strftime("%Y-%m-%d") if message.date else "?"
            log(f"  … {count} messages (at {stamp})")

        yield record
