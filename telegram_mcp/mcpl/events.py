"""Telethon event → MCPL push translation.

Wires `@client.on(events.NewMessage)` (and friends) for each configured
TelegramClient. Each event is converted to a `ChannelIncomingMessage` with
metadata rich enough to drive the host's `shouldTriggerInference` filter:

  - botUserId, senderId, senderUsername
  - chatType, chatTitle
  - mentionIds, isMention
  - isReply, replyToMessageId, replyToAuthorId, replyToContent (snippet)
  - threadId for forum-topic supergroups
  - isPrivate, accountLabel

The handler emits `channels/incoming` notifications via the transport.
Errors are swallowed and logged so a buggy mapping never tears down the
event loop.
"""

from __future__ import annotations

import logging
from datetime import timezone
from typing import TYPE_CHECKING, Any

from telethon import TelegramClient, events
from telethon.tl.types import (
    Channel,
    Chat,
    MessageEntityMention,
    MessageEntityMentionName,
    User,
)

from .channels import channel_id_for, entity_to_descriptor
from .content import message_to_content_blocks
from .types import ChannelIncomingMessage

if TYPE_CHECKING:
    from .transport import McplTransport

log = logging.getLogger("telegram_mcp.mcpl")

REPLY_SNIPPET_MAX = 200

# Tracks clients that already have MCPL handlers attached, so a host
# reconnect (which fires on_ready again) doesn't double-attach and cause
# every NewMessage to be pushed twice.
_attached_clients: set[int] = set()


def reset_attached_clients() -> None:
    """Test helper: clear the attachment tracker between tests."""
    _attached_clients.clear()


def _peer_kind(chat: Any) -> str | None:
    if isinstance(chat, User):
        return "dm"
    if isinstance(chat, Chat):
        return "group"
    if isinstance(chat, Channel):
        if getattr(chat, "megagroup", False):
            return "supergroup"
        if getattr(chat, "broadcast", False):
            return "channel"
        return "supergroup"
    return None


def _author_dict(sender: Any) -> dict[str, Any]:
    if sender is None:
        return {"id": "unknown", "name": "Unknown"}
    name_parts: list[str] = []
    first = getattr(sender, "first_name", None)
    last = getattr(sender, "last_name", None)
    title = getattr(sender, "title", None)
    if title:
        name_parts.append(str(title))
    else:
        if first:
            name_parts.append(first)
        if last:
            name_parts.append(last)
    name = " ".join(p for p in name_parts if p) or (
        f"@{sender.username}" if getattr(sender, "username", None) else f"id:{getattr(sender, 'id', '?')}"
    )
    return {"id": str(getattr(sender, "id", "unknown")), "name": name}


def _extract_mention_ids(message: Any) -> list[int]:
    """Extract user IDs explicitly mentioned via MessageEntityMentionName."""
    entities = getattr(message, "entities", None) or []
    out: list[int] = []
    for ent in entities:
        if isinstance(ent, MessageEntityMentionName):
            out.append(ent.user_id)
    return out


def _has_username_mention(message: Any, username: str | None) -> bool:
    if not username:
        return False
    entities = getattr(message, "entities", None) or []
    text = getattr(message, "message", "") or ""
    target = f"@{username}".lower()
    for ent in entities:
        if isinstance(ent, MessageEntityMention):
            slice_ = text[ent.offset : ent.offset + ent.length]
            if slice_.lower() == target:
                return True
    return False


async def build_incoming_message(
    event: events.NewMessage.Event,
    *,
    account_label: str,
    self_id: int,
    self_username: str | None,
) -> ChannelIncomingMessage | None:
    """Translate a Telethon NewMessage event to a ChannelIncomingMessage.

    Returns None when the event is for an entity we can't map (forbidden,
    unsupported peer type) — the caller skips silently.
    """
    message = event.message
    chat = await event.get_chat()
    descriptor = entity_to_descriptor(
        chat, account_label=account_label, self_id=self_id
    )
    if descriptor is None:
        return None

    sender = await event.get_sender()
    author = _author_dict(sender)
    sender_id = getattr(sender, "id", None)
    sender_username = getattr(sender, "username", None) if sender else None

    mention_ids = _extract_mention_ids(message)
    is_username_mention = _has_username_mention(message, self_username)
    is_explicit_mention = self_id in mention_ids
    is_mention = bool(getattr(message, "mentioned", False)) or is_username_mention or is_explicit_mention

    chat_kind = descriptor["metadata"]["kind"]
    chat_id = descriptor["address"]["peerId"]

    metadata: dict[str, Any] = {
        "account": account_label,
        "botUserId": str(self_id),
        "chatType": chat_kind,
        "chatTitle": descriptor["label"],
        "isPrivate": chat_kind in ("dm", "saved"),
        "isMention": is_mention,
        "isReply": False,
        "isReplyToBot": False,
        "mentionIds": [str(uid) for uid in mention_ids],
    }
    if sender_id is not None:
        metadata["senderId"] = str(sender_id)
    if sender_username:
        metadata["senderUsername"] = sender_username

    # Reply context — best-effort.
    if getattr(message, "is_reply", False):
        metadata["isReply"] = True
        reply_to = getattr(message, "reply_to", None)
        reply_to_id = getattr(reply_to, "reply_to_msg_id", None) if reply_to else None
        if reply_to_id is not None:
            metadata["replyToMessageId"] = str(reply_to_id)
        try:
            replied = await message.get_reply_message()
        except Exception:  # noqa: BLE001 — never block on reply lookup
            replied = None
        if replied is not None:
            replied_author_id = getattr(replied.sender_id, "user_id", None) or getattr(
                replied, "sender_id", None
            )
            if replied_author_id is not None:
                metadata["replyToAuthorId"] = str(replied_author_id)
                if int(replied_author_id) == int(self_id):
                    metadata["isReplyToBot"] = True
            replied_text = (getattr(replied, "message", "") or "").strip()
            if replied_text:
                metadata["replyToContent"] = replied_text[:REPLY_SNIPPET_MAX]

    # Forum topic id (supergroups only)
    thread_id: str | None = None
    reply_to = getattr(message, "reply_to", None)
    if reply_to is not None and getattr(reply_to, "forum_topic", False):
        top_id = getattr(reply_to, "reply_to_top_id", None) or getattr(
            reply_to, "reply_to_msg_id", None
        )
        if top_id is not None:
            thread_id = str(top_id)

    timestamp = message.date.astimezone(timezone.utc).isoformat() if message.date else ""

    incoming: ChannelIncomingMessage = {
        "channelId": descriptor["id"],
        "messageId": str(message.id),
        "author": author,
        "timestamp": timestamp,
        "content": message_to_content_blocks(
            message, account_label=account_label, chat_id=chat_id
        ),
        "metadata": metadata,
    }
    if thread_id is not None:
        incoming["threadId"] = thread_id
    return incoming


async def _build_chat_action_payload(
    event: events.ChatAction.Event,
    *,
    account_label: str,
    self_id: int,
) -> dict[str, Any] | None:
    """Map ChatAction events into channels/changed payloads.

    Returns one of:
      - {"removed": [channelId]}    — we left or were kicked
      - {"added":   [descriptor]}   — we joined a new chat
      - None                         — uninteresting action (e.g., other user
                                        joined or left, message pinned, etc.)
    """
    user_kicked = bool(getattr(event, "user_kicked", False))
    user_left = bool(getattr(event, "user_left", False))
    user_added = bool(getattr(event, "user_added", False))
    user_joined = bool(getattr(event, "user_joined", False))

    # Only react to events affecting OUR own membership.
    affected_self = False
    user_id = getattr(event, "user_id", None)
    if isinstance(user_id, list):
        affected_self = self_id in user_id
    elif user_id is not None:
        affected_self = int(user_id) == self_id
    elif user_kicked or user_left or user_added or user_joined:
        # Some Telethon paths set just the action flags + chat
        affected_self = True

    if not affected_self:
        return None

    chat = await event.get_chat()
    if user_left or user_kicked:
        descriptor = entity_to_descriptor(
            chat, account_label=account_label, self_id=self_id
        )
        if descriptor is None:
            return None
        return {"removed": [descriptor["id"]]}
    if user_added or user_joined:
        descriptor = entity_to_descriptor(
            chat, account_label=account_label, self_id=self_id
        )
        if descriptor is None:
            return None
        return {"added": [descriptor]}
    return None


async def attach_event_handlers(
    client: TelegramClient,
    *,
    account_label: str,
    transport: McplTransport,
) -> None:
    """Register Telethon event handlers for one client.

    Currently:
      - NewMessage → channels/incoming
      - ChatAction → channels/changed (joins, leaves, kicks affecting us)

    Idempotent: attaching twice on the same client is a no-op. Telethon
    happily registers duplicate handlers, so we guard with a per-client
    sentinel — protects against host-side reconnect re-firing on_ready.
    """
    if id(client) in _attached_clients:
        log.info(
            "MCPL handlers already attached for account '%s' — skipping (host reconnect?)",
            account_label,
        )
        return

    me = await client.get_me()
    self_id = int(getattr(me, "id"))
    self_username = getattr(me, "username", None)

    @client.on(events.NewMessage)
    async def on_new_message(event):  # pragma: no cover — real-Telegram path
        try:
            payload = await build_incoming_message(
                event,
                account_label=account_label,
                self_id=self_id,
                self_username=self_username,
            )
            if payload is None:
                return
            await transport.send_notification(
                "channels/incoming", {"messages": [payload]}
            )
        except Exception:
            log.exception("Failed to push NewMessage event")

    @client.on(events.ChatAction)
    async def on_chat_action(event):  # pragma: no cover — real-Telegram path
        try:
            payload = await _build_chat_action_payload(
                event, account_label=account_label, self_id=self_id
            )
            if payload is None:
                return
            await transport.send_notification("channels/changed", payload)
        except Exception:
            log.exception("Failed to push ChatAction event")

    _attached_clients.add(id(client))
    log.info(
        "Attached MCPL handlers (NewMessage, ChatAction) for account '%s' "
        "(self_id=%s)",
        account_label,
        self_id,
    )
