"""Inbound MCPL handlers — methods the host calls on us.

  - channels/publish — send agent's content via the right Telethon client.
    Honors a custom `replyToMessageId` extension (not in the base MCPL
    spec) which also auto-threads into the right forum topic when the
    original message lived in one.
  - channels/typing — fire a brief typing indicator on the target chat.
  - channels/list — re-enumerate live dialogs and return the current set.
  - channels/open / channels/close — minimal acknowledgements; channel
    subscription is 'auto' on the host side, so these are mostly informational.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient

from .channels import enumerate_channels
from .types import ChannelsPublishParams, ChannelsPublishResult, McplContentBlock

log = logging.getLogger("telegram_mcp.mcpl")


# ---------------------------------------------------------------------------
# Channel id parsing
# ---------------------------------------------------------------------------


def parse_channel_id(channel_id: str) -> tuple[str, str, int | None]:
    """Parse `telegram:{account}:{kind}[:{peer_id}]` into its components.

    Saved Messages has no peer_id segment (kind='saved').
    Raises ValueError on malformed input.
    """
    parts = channel_id.split(":")
    if not parts or parts[0] != "telegram":
        raise ValueError(f"Not a telegram channel id: {channel_id!r}")
    if len(parts) == 3 and parts[2] == "saved":
        return parts[1], "saved", None
    if len(parts) == 4:
        try:
            peer_id = int(parts[3])
        except ValueError as exc:
            raise ValueError(f"Invalid peer id in {channel_id!r}") from exc
        kind = parts[2]
        if kind not in {"dm", "group", "supergroup", "channel"}:
            raise ValueError(f"Unknown channel kind {kind!r} in {channel_id!r}")
        return parts[1], kind, peer_id
    raise ValueError(f"Malformed telegram channel id: {channel_id!r}")


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def extract_text(content: list[McplContentBlock]) -> str:
    """Concatenate text blocks. Non-text blocks are dropped with a warning.

    Phase 5 supports text only. Image / audio / resource blocks are noted
    in the log but not sent — sending media as the agent requires fetching
    base64 data and is deferred.
    """
    text_parts: list[str] = []
    skipped: list[str] = []
    for block in content or []:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        else:
            skipped.append(btype or "<unknown>")
    if skipped:
        log.warning(
            "channels/publish dropped %d non-text content block(s): %s",
            len(skipped),
            skipped,
        )
    return "\n".join(p for p in text_parts if p)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


def make_publish_handler(
    clients: dict[str, TelegramClient],
    *,
    resolve_entity_fn,
    ensure_connected_fn,
):
    """Build the channels/publish handler bound to this server's clients.

    `resolve_entity_fn` and `ensure_connected_fn` are the existing helpers
    in `telegram_mcp.runtime`. Passed as parameters to keep this module
    decoupled from the runtime singleton — it makes testing tractable.
    """

    async def handle_publish(params: ChannelsPublishParams) -> ChannelsPublishResult:
        channel_id = params.get("channelId")
        if not channel_id:
            raise ValueError("channels/publish missing channelId")
        content = params.get("content") or []

        account_label, kind, peer_id = parse_channel_id(channel_id)
        client = clients.get(account_label)
        if client is None:
            raise ValueError(
                f"Unknown account '{account_label}' "
                f"(known: {', '.join(sorted(clients))})"
            )
        await ensure_connected_fn(client)

        if kind == "saved":
            peer: Any = "me"
        else:
            assert peer_id is not None  # parse_channel_id guarantees this
            peer = await resolve_entity_fn(peer_id, client)

        text = extract_text(content)
        if not text:
            raise ValueError("channels/publish has no text content to send")

        kwargs: dict[str, Any] = {}
        reply_to_id = params.get("replyToMessageId")
        if reply_to_id:
            try:
                kwargs["reply_to"] = int(reply_to_id)
            except (TypeError, ValueError):
                log.warning("Ignoring non-numeric replyToMessageId: %r", reply_to_id)

        sent = await client.send_message(peer, text, **kwargs)
        return {"delivered": True, "messageId": str(sent.id)}

    return handle_publish


# ---------------------------------------------------------------------------
# channels/list
# ---------------------------------------------------------------------------


def make_list_handler(clients: dict[str, TelegramClient]):
    """Re-enumerate dialogs across all configured accounts."""

    async def handle_list(params: dict[str, Any]) -> dict[str, Any]:
        channels = []
        for label, cl in clients.items():
            try:
                channels.extend(await enumerate_channels(cl, label))
            except Exception:
                log.exception("channels/list — enumeration failed for %r", label)
        return {"channels": channels}

    return handle_list


# ---------------------------------------------------------------------------
# channels/open and channels/close — informational under 'auto' subscription
# ---------------------------------------------------------------------------


def make_open_handler():
    """Acknowledge channels/open. We auto-subscribe everywhere; nothing to do."""

    async def handle_open(params: dict[str, Any]) -> dict[str, Any]:
        # The spec returns a ChannelDescriptor on success. The host already
        # has it from the original channels/register; echo back what they
        # asked about so the contract is satisfied.
        return {
            "channel": {
                "id": params.get("address", {}).get("id")
                or f"telegram:{params.get('type', 'unknown')}",
                "type": params.get("type", "telegram"),
                "label": "open-acknowledged",
                "direction": "bidirectional",
            }
        }

    return handle_open


def make_close_handler():
    """Acknowledge channels/close. No-op under auto subscription."""

    async def handle_close(params: dict[str, Any]) -> dict[str, Any]:
        return {"closed": True}

    return handle_close


# ---------------------------------------------------------------------------
# channels/typing
# ---------------------------------------------------------------------------


def make_typing_handler(
    clients: dict[str, TelegramClient],
    *,
    resolve_entity_fn,
    ensure_connected_fn,
    default_duration: float = 3.0,
):
    """Fire a brief typing pulse on the named channel.

    channels/typing is a notification, so we kick off the actual typing in
    a background task and return immediately — long pulses must not block
    the dispatcher's reader loop.
    """

    async def _do_typing(client: TelegramClient, peer: Any, duration: float) -> None:
        try:
            async with client.action(peer, "typing"):
                await asyncio.sleep(min(duration, 30.0))  # cap at Telegram's 30s
        except Exception:
            log.exception("channels/typing — action failed")

    async def handle_typing(params: dict[str, Any]) -> None:
        channel_id = params.get("channelId")
        if not channel_id:
            return
        try:
            account_label, kind, peer_id = parse_channel_id(channel_id)
        except ValueError:
            log.warning("channels/typing — bad channelId: %r", channel_id)
            return
        client = clients.get(account_label)
        if client is None:
            return
        await ensure_connected_fn(client)
        peer: Any = "me" if kind == "saved" else await resolve_entity_fn(peer_id, client)
        duration = float(params.get("duration", default_duration))
        # Fire and forget — the host doesn't expect a response.
        asyncio.create_task(_do_typing(client, peer, duration))

    return handle_typing
