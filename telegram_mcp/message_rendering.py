"""Message output formatting, independent of tool registration and fetching."""

import json
from typing import Optional

from telethon import types, utils

from sanitize import sanitize_name, sanitize_user_content
from telegram_mcp import transcription
from telegram_mcp.entity_formatting import (
    get_engagement_dict,
    get_engagement_info,
    get_sender_info,
    get_sender_name,
    get_sender_username,
)
from telegram_mcp.rich_text import rich_message_text


def get_media_label(msg) -> str:
    """Short label of attached media for a message, or "" if none.

    The media object is already present on the fetched message (msg.media /
    msg.photo / msg.document etc.) — no extra API call needed. Surfacing it in
    listings prevents the classic miss where a photo/file WITH a caption shows
    up looking like a plain text message (Telethon puts the caption in
    msg.message but the media stays in msg.media).
    """
    try:
        # Link web preview is NOT an attachment. Check it FIRST: for a message with a
        # link, Telethon returns the preview image via msg.photo; otherwise it would
        # be incorrectly classified as a "photo".
        if getattr(msg, "web_preview", None) is not None:
            return ""
        # Sticker/voice/video/audio/GIF are also represented as documents, so check
        # them BEFORE the generic document handler.
        sticker = getattr(msg, "sticker", None)
        if sticker is not None:
            alt = ""
            for attr in getattr(sticker, "attributes", []) or []:
                a = getattr(attr, "alt", None)
                if a:
                    alt = a
                    break
            return f"sticker {alt}".strip()
        if getattr(msg, "photo", None) is not None:
            return "photo"
        if getattr(msg, "voice", None) is not None:
            return "voice"
        if getattr(msg, "video_note", None) is not None:
            return "video_note"
        if getattr(msg, "video", None) is not None:
            return "video"
        if getattr(msg, "audio", None) is not None:
            return "audio"
        if getattr(msg, "gif", None) is not None:
            return "gif"
        if getattr(msg, "document", None) is not None:
            name = None
            f = getattr(msg, "file", None)
            if f is not None:
                name = getattr(f, "name", None)
            return f"document: {name}" if name else "document"
        if getattr(msg, "contact", None) is not None:
            return "contact"
        if getattr(msg, "geo", None) is not None:
            return "geo"
        if getattr(msg, "poll", None) is not None:
            return "poll"
        if getattr(msg, "media", None) is not None:
            return "media"
        return ""
    except Exception:
        return ""


def _inline_button_texts(msg):
    """Inline button texts of the message (flat list), [] if none."""
    out = []
    try:
        for row in getattr(msg, "buttons", None) or []:
            for b in row:
                t = getattr(b, "text", None)
                if t:
                    out.append(t)
    except Exception:
        pass
    return out


def _link_urls(msg):
    """Explicit URLs from entities (links hidden behind text), [] if none."""
    out = []
    try:
        for e in getattr(msg, "entities", None) or []:
            u = getattr(e, "url", None)
            if u:
                out.append(u)
    except Exception:
        pass
    return out


def _rich_custom_emojis(node):
    """Walk nested PageBlock/RichText objects without fetching their documents."""
    if isinstance(node, types.TextCustomEmoji):
        yield node, node.alt
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _rich_custom_emojis(item)
    else:
        for child in getattr(node, "__dict__", {}).values():
            yield from _rich_custom_emojis(child)


def get_custom_emoji_metadata(msg) -> dict:
    """Reusable custom emoji variants, deduplicated by their string document ID.

    Extract from the original text: sanitizing it first would shift Telegram's
    UTF-16 offsets. Telethon handles those offsets without another API request.
    Block-format messages carry TextCustomEmoji nodes in rich_message instead.
    """
    entities = [
        entity
        for entity in getattr(msg, "entities", None) or []
        if isinstance(entity, types.MessageEntityCustomEmoji)
    ]
    text = getattr(msg, "message", None)
    pairs = list(zip(entities, utils.get_inner_text(text, entities))) if text and entities else []
    blocks = getattr(getattr(msg, "rich_message", None), "blocks", None)
    pairs.extend(_rich_custom_emojis(blocks))
    emojis = {}
    for entity, emoji in pairs:
        document_id = str(entity.document_id)
        if document_id not in emojis:
            emojis[document_id] = {
                "emoji": sanitize_user_content(emoji, max_length=64, preserve_emoji=True),
                "id": document_id,
            }
    return {"custom_emojis": list(emojis.values())} if emojis else {}


def get_reply_quote(msg) -> Optional[dict]:
    """Quoted fragment when a reply targets only *part* of the replied-to message.

    Telegram lets you select a span of another message and reply to just that
    span. Telethon exposes it on msg.reply_to as quote_text (the selected text)
    and quote_offset (its UTF-16 character offset inside the original message).
    Returns {"text": ..., "offset": ...} for such a partial-quote reply, or None
    for a plain whole-message reply (or no reply at all). Independent of
    reply_to_msg_id so a cross-chat quote reply still surfaces its quote.
    """
    reply = getattr(msg, "reply_to", None)
    if reply is None:
        return None
    quote_text = getattr(reply, "quote_text", None)
    if not quote_text:
        return None
    quote = {"text": sanitize_user_content(quote_text)}
    offset = getattr(reply, "quote_offset", None)
    if offset is not None:
        quote["offset"] = offset
    return quote


def _forwarded_info(msg, fwd, link_domain):
    """Original attribution, resolved exclusively from already-fetched entities."""
    finfo = {}
    fdate = getattr(fwd, "date", None)
    if fdate:
        finfo["date"] = fdate
    fname = getattr(fwd, "from_name", None)
    if fname:
        finfo["from_name"] = sanitize_name(fname)
    # from_name is set only when the original author hides their profile.
    # For an ordinary channel forward the origin sits in fwd.from_id, and
    # reading just from_name loses the attribution the Telegram UI shows as
    # "Forwarded from …". Telethon's msg.forward wrapper resolves that peer
    # from entities already present in the response — no extra API call.
    fo = getattr(msg, "forward", None)
    if fo is not None:
        chat = getattr(fo, "chat", None)
        if chat is not None:
            title = getattr(chat, "title", None) or " ".join(
                x
                for x in (getattr(chat, "first_name", None), getattr(chat, "last_name", None))
                if x
            )
            if title:
                finfo["from_chat"] = sanitize_name(title)
            uname = getattr(chat, "username", None)
            if uname:
                finfo["from_username"] = uname
        fwd_chat_id = getattr(fo, "chat_id", None)
        if fwd_chat_id is not None:
            finfo["from_chat_id"] = fwd_chat_id
        sender = getattr(fo, "sender", None)
        if sender is not None:
            sname = " ".join(
                x
                for x in (
                    getattr(sender, "first_name", None),
                    getattr(sender, "last_name", None),
                )
                if x
            )
            if sname:
                finfo["from_user"] = sanitize_name(sname)
    post_id = getattr(fwd, "channel_post", None)
    if post_id is not None:
        finfo["channel_post"] = post_id
    author = getattr(fwd, "post_author", None)
    if author:
        finfo["post_author"] = sanitize_name(author)
    # Canonical permalink, when the pieces are there: a public channel gives
    # <domain>/<username>/<post>, a private one the <domain>/c/<id>/<post>
    # form that only resolves for members.
    if post_id is not None:
        if finfo.get("from_username"):
            finfo["post_link"] = f"https://{link_domain}/{finfo['from_username']}/{post_id}"
        elif finfo.get("from_chat_id") is not None:
            finfo["post_link"] = (
                f"https://{link_domain}/c/{abs(finfo['from_chat_id']) % 10**10}/{post_id}"
            )
    return finfo or True


def message_to_dict(msg, chat_id: Optional[int] = None, *, link_domain: str = "t.me") -> dict:
    """API-complete but compact Telethon message view (omit empty fields).

    The goal is for the MCP output to match the API object in completeness, rather
    than losing data such as media, albums, forwards, edits, buttons, reactions,
    and so on. All these fields are already present in the message object returned
    by the same get_messages request.

    chat_id (the numeric chat this message belongs to) enables voice/video-note
    transcript enrichment via the cache - omit it to get the old text-only
    behavior (used by existing tests with bare fake messages).
    """
    d = {"id": msg.id, "sender": get_sender_name(msg), "date": msg.date}

    sender_id = getattr(msg, "sender_id", None)
    if sender_id is not None:
        d["sender_id"] = sender_id
    username = get_sender_username(msg)
    if username:
        d["username"] = username
    if getattr(msg, "out", False):
        d["out"] = True

    text = sanitize_user_content(msg.message) if getattr(msg, "message", None) else ""
    rich = False
    if not text:
        # Block-format posts leave .message empty and keep the words in
        # .rich_message; without this the whole post reads back as "[empty]".
        rich_text = rich_message_text(msg)
        if rich_text:
            text = sanitize_user_content(rich_text)
            rich = True
    if text:
        d["text"] = text
    if rich:
        d["rich"] = True  # text rebuilt from page blocks, not a verbatim .message
    d.update(get_custom_emoji_metadata(msg))

    media_label = get_media_label(msg)
    if media_label:
        d["media"] = media_label

    if not text:
        voice_info = transcription.voice_attachment_info(msg, chat_id)
        if voice_info is not None:
            if voice_info["duration"] is not None:
                d["duration"] = voice_info["duration"]
            if voice_info["transcript_status"] == "ready":
                d["transcript"] = voice_info["transcript"]
                d["transcript_source"] = voice_info["transcript_source"]
                d["transcript_note"] = "Machine transcript, not a verbatim quote."
            elif voice_info["transcript_status"] == "pending":
                d["transcript_status"] = "pending"

    grouped_id = getattr(msg, "grouped_id", None)
    if grouped_id:
        d["grouped_id"] = grouped_id  # album: messages sharing one grouped_id form a single group

    reply_to_id = (
        getattr(msg.reply_to, "reply_to_msg_id", None) if getattr(msg, "reply_to", None) else None
    )
    if reply_to_id:
        d["reply_to"] = reply_to_id
    reply_quote = get_reply_quote(msg)
    if reply_quote:
        d["reply_quote"] = reply_quote  # reply to a selected span of the original

    fwd = getattr(msg, "fwd_from", None)
    if fwd is not None:
        d["forwarded"] = _forwarded_info(msg, fwd, link_domain)

    via_bot_id = getattr(msg, "via_bot_id", None)
    if via_bot_id:
        d["via_bot_id"] = via_bot_id

    edit_date = getattr(msg, "edit_date", None)
    if edit_date:
        d["edited"] = edit_date

    if getattr(msg, "pinned", False):
        d["pinned"] = True

    engagement = get_engagement_dict(msg)
    if engagement:
        d["engagement"] = engagement

    replies = getattr(msg, "replies", None)
    if replies is not None:
        cnt = getattr(replies, "replies", None)
        if cnt is not None:
            d["comments"] = cnt

    buttons = _inline_button_texts(msg)
    if buttons:
        d["buttons"] = buttons

    urls = _link_urls(msg)
    if urls:
        d["link_urls"] = urls

    action = getattr(msg, "action", None)
    if action is not None:
        d["action"] = type(action).__name__  # service message (joined/pinned/…)

    ttl = getattr(msg, "ttl_period", None)
    if ttl:
        d["ttl_period"] = ttl

    return d


def format_message_line(msg, chat_id: Optional[int] = None) -> str:
    """Single-line human-readable message representation with ALL key flags.

    chat_id enables voice/video-note transcript enrichment via the cache -
    see message_to_dict for why it's optional.
    """
    parts = [f"ID: {msg.id}", get_sender_info(msg), f"Date: {msg.date}"]

    reply_to_id = (
        getattr(msg.reply_to, "reply_to_msg_id", None) if getattr(msg, "reply_to", None) else None
    )
    if reply_to_id:
        parts.append(f"reply to {reply_to_id}")
    reply_quote = get_reply_quote(msg)
    if reply_quote:
        preview = reply_quote["text"].replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:60] + "…"
        parts.append(f'quoting "{preview}"')

    flags = []
    media_label = get_media_label(msg)
    if media_label:
        flags.append(f"📎 {media_label}")
    grouped_id = getattr(msg, "grouped_id", None)
    if grouped_id:
        flags.append(f"album:{grouped_id}")
    if getattr(msg, "fwd_from", None) is not None:
        flags.append("forwarded")
    if getattr(msg, "edit_date", None):
        flags.append("edited")
    if getattr(msg, "via_bot_id", None):
        flags.append("via_bot")
    if getattr(msg, "pinned", False):
        flags.append("pinned")
    btn = _inline_button_texts(msg)
    if btn:
        flags.append(f"buttons:{len(btn)}")
    action = getattr(msg, "action", None)
    if action is not None:
        flags.append(f"service:{type(action).__name__}")
    if flags:
        parts.append(", ".join(flags))

    engagement_info = get_engagement_info(msg).lstrip(" |").strip()
    if engagement_info:
        parts.append(engagement_info)

    custom_emojis = get_custom_emoji_metadata(msg)
    if custom_emojis:
        parts.append(
            f"custom_emojis: {json.dumps(custom_emojis['custom_emojis'], ensure_ascii=False)}"
        )

    raw = sanitize_user_content(msg.message) if getattr(msg, "message", None) else ""
    if not raw:
        rich_text = rich_message_text(msg)
        if rich_text:
            raw = sanitize_user_content(rich_text)
            parts.append("rich")
    if raw:
        safe_text = raw.replace("\n", "\\n")
    else:
        voice_info = transcription.voice_attachment_info(msg, chat_id)
        safe_text = transcription.render_voice_text(voice_info) if voice_info else "[empty]"
    return " | ".join(parts) + f" | Message: {safe_text}"
