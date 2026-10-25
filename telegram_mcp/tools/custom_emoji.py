"""Custom Emoji MCP tools for browsing, searching, inspecting, and previewing custom emoji."""

import asyncio
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from mcp.server.fastmcp import Context, FastMCP, Image
from mcp.types import ToolAnnotations
from telethon import functions, types
from telethon.errors.rpcerrorlist import StickersetInvalidError

from sanitize import sanitize_name, sanitize_user_content
from telegram_mcp.runtime import (
    _resolve_writable_file_path,
    ensure_connected,
    get_client,
    json_serializer,
    log_and_format_error,
    mcp,
    with_account,
)


def extract_pack_short_name(pack: str) -> str:
    """
    Extract the sticker or emoji pack short name from a URL or raw identifier.

    Supports:
        - https://t.me/addemoji/<name>
        - t.me/addemoji/<name>
        - https://t.me/addstickers/<name>
        - t.me/addstickers/<name>
        - tg://addemoji?set=<name>
        - tg://addstickers?set=<name>
        - plain short name (e.g. 'AnimatedDogs')
    """
    if not pack:
        return ""
    s = pack.strip()
    m = re.search(r"(?:addemoji|addstickers)/([a-zA-Z0-9_]+)", s)
    if m:
        return m.group(1)
    m = re.search(r"set=([a-zA-Z0-9_]+)", s)
    if m:
        return m.group(1)
    m = re.match(r"^[a-zA-Z0-9_]+$", s)
    if m:
        return s
    clean = s.rstrip("/").split("/")[-1]
    clean = re.sub(r"[^a-zA-Z0-9_]", "", clean)
    return clean


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Custom Emoji Packs", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
async def get_custom_emoji_packs(
    query: Optional[str] = None,
    featured: bool = False,
    limit: int = 50,
    account: Optional[str] = None,
) -> str:
    """
    Browse installed or featured custom emoji packs, or search for emoji packs by query.

    Args:
        query: Optional search keyword to search for emoji packs.
        featured: If True, fetches featured emoji packs instead of installed ones.
        limit: Maximum number of emoji packs to return (default: 50).
        account: Optional account alias or phone number.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)

        if query and query.strip():
            result = await cl(
                functions.messages.SearchEmojiStickerSetsRequest(q=query.strip(), hash=0)
            )
            raw_sets = getattr(result, "sets", []) or []
        elif featured:
            result = await cl(functions.messages.GetFeaturedEmojiStickersRequest(hash=0))
            raw_sets = getattr(result, "sets", []) or []
        else:
            result = await cl(functions.messages.GetEmojiStickersRequest(hash=0))
            raw_sets = getattr(result, "sets", []) or []

        packs = []
        for item in raw_sets:
            s = getattr(item, "set", item)
            short_name = getattr(s, "short_name", "")
            pack_info = {
                "id": str(getattr(s, "id", "")),
                "title": sanitize_name(getattr(s, "title", "")),
                "short_name": short_name,
                "count": getattr(s, "count", 0),
                "archived": bool(getattr(s, "archived", False)),
                "official": bool(getattr(s, "official", False)),
                "masks": bool(getattr(s, "masks", False)),
                "emojis": bool(getattr(s, "emojis", True)),
                "url": f"https://t.me/addemoji/{short_name}" if short_name else None,
            }
            if getattr(s, "installed_date", None):
                pack_info["installed_date"] = s.installed_date.isoformat()
            packs.append(pack_info)
            if len(packs) >= limit:
                break

        return json.dumps(packs, indent=2, default=json_serializer, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error(
            "get_custom_emoji_packs", e, query=query, featured=featured, limit=limit
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Custom Emoji Pack", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
async def get_custom_emoji_pack(
    pack: str,
    account: Optional[str] = None,
) -> str:
    """
    Open a custom emoji pack (by short name or t.me/addemoji/... link) and
    get its emoji with document IDs and fallback characters.

    Args:
        pack: The emoji pack short name or URL (e.g. 'AnimatedDogs',
            't.me/addemoji/AnimatedDogs', 'https://t.me/addstickers/...').
        account: Optional account alias or phone number.
    """
    try:
        short_name = extract_pack_short_name(pack)
        if not short_name:
            return "Invalid pack name or URL provided."

        cl = get_client(account)
        await ensure_connected(cl)

        try:
            result = await cl(
                functions.messages.GetStickerSetRequest(
                    stickerset=types.InputStickerSetShortName(short_name=short_name),
                    hash=0,
                )
            )
        except StickersetInvalidError:
            return f"Emoji pack '{short_name}' not found or is invalid."

        set_meta = getattr(result, "set", None)
        if not set_meta:
            return f"Emoji pack '{short_name}' has no set metadata."

        is_emoji = bool(getattr(set_meta, "emojis", False))
        url_prefix = "addemoji" if is_emoji else "addstickers"
        pack_info = {
            "id": str(getattr(set_meta, "id", "")),
            "title": sanitize_name(getattr(set_meta, "title", "")),
            "short_name": getattr(set_meta, "short_name", short_name),
            "count": getattr(set_meta, "count", 0),
            "is_emoji": is_emoji,
            "archived": bool(getattr(set_meta, "archived", False)),
            "official": bool(getattr(set_meta, "official", False)),
            "url": (
                f"https://t.me/{url_prefix}/{set_meta.short_name}"
                if getattr(set_meta, "short_name", None)
                else None
            ),
        }

        # Build emoticon mapping from packs
        doc_to_emoticon: Dict[int, str] = {}
        for sp in getattr(result, "packs", []) or []:
            emoticon = getattr(sp, "emoticon", "")
            for doc_id in getattr(sp, "documents", []) or []:
                if doc_id not in doc_to_emoticon:
                    doc_to_emoticon[doc_id] = emoticon

        emojis = []
        for doc in getattr(result, "documents", []) or []:
            doc_id = getattr(doc, "id", None)
            if doc_id is None:
                continue

            alt = doc_to_emoticon.get(doc_id, "")
            free = False
            for attr in getattr(doc, "attributes", []) or []:
                if isinstance(attr, types.DocumentAttributeCustomEmoji):
                    alt = getattr(attr, "alt", alt) or alt
                    free = bool(getattr(attr, "free", False))
                elif isinstance(attr, types.DocumentAttributeSticker):
                    alt = getattr(attr, "alt", alt) or alt

            mime_type = getattr(doc, "mime_type", "")
            is_animated = mime_type == "application/x-tgsticker" or any(
                isinstance(a, types.DocumentAttributeAnimated)
                for a in getattr(doc, "attributes", []) or []
            )
            is_video = mime_type == "video/webm" or any(
                isinstance(a, types.DocumentAttributeVideo)
                for a in getattr(doc, "attributes", []) or []
            )
            if is_animated:
                fmt = "animated"
            elif is_video:
                fmt = "video"
            else:
                fmt = "static"

            emojis.append(
                {
                    "document_id": str(doc_id),
                    "id": str(doc_id),
                    "alt": sanitize_user_content(alt),
                    "emoji": sanitize_user_content(alt),
                    "free": free,
                    "format": fmt,
                    "mime_type": mime_type,
                }
            )

        return json.dumps(
            {"pack": pack_info, "emojis": emojis},
            indent=2,
            default=json_serializer,
            ensure_ascii=False,
        )
    except Exception as e:
        return log_and_format_error("get_custom_emoji_pack", e, pack=pack)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Preview Custom Emoji", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
async def preview_custom_emoji(
    document_id: Union[int, str],
    thumbnail: bool = True,
    save_path: Optional[str] = None,
    ctx: Optional[Context] = None,
    account: Optional[str] = None,
):
    """
    Preview or download a custom emoji by its Telegram document ID.

    Args:
        document_id: Telegram document ID of the custom emoji (from
        get_custom_emoji_pack or message metadata).
        thumbnail: If True, downloads the static thumbnail image preview
        (ideal for animated TGS and video WebM emojis).
        save_path: Optional path under allowed roots to save the preview or emoji file.
        account: Optional account alias or phone number.
    """
    try:
        try:
            doc_id = int(str(document_id).strip())
            if doc_id <= 0:
                raise ValueError()
        except (ValueError, TypeError):
            return "Invalid document_id: must be a positive integer."

        cl = get_client(account)
        await ensure_connected(cl)

        docs = await cl(functions.messages.GetCustomEmojiDocumentsRequest(document_id=[doc_id]))
        if not docs:
            return f"Custom emoji with document ID {doc_id} not found."

        doc = docs[0]

        data = None
        if thumbnail:
            try:
                data = await cl.download_media(doc, file=bytes, thumb=-1)
            except Exception:
                data = None
        if not data:
            data = await cl.download_media(doc, file=bytes)

        if not data:
            return f"Failed to download media for custom emoji {doc_id}."

        ext = ".webp"
        img_format = None
        if data.startswith(b"\xff\xd8\xff"):
            ext = ".jpg"
            img_format = "jpeg"
        elif data.startswith(b"\x89PNG"):
            ext = ".png"
            img_format = "png"
        elif data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
            ext = ".webp"
            img_format = "webp"
        elif data.startswith(b"\x1f\x8b"):
            ext = ".tgs"
        elif data.startswith(b"\x1aE\xdf\xa3"):
            ext = ".webm"

        saved_path_str = None
        if save_path:
            kept_path, path_error = await _resolve_writable_file_path(
                raw_path=save_path,
                default_filename=f"custom_emoji_{doc_id}{ext}",
                ctx=ctx,
                tool_name="preview_custom_emoji",
            )
            if path_error:
                return path_error
            kept_path.write_bytes(data)
            saved_path_str = str(kept_path)

        if img_format:
            image = Image(data=data, format=img_format)
            if saved_path_str:
                return [f"Custom emoji {doc_id} saved to {saved_path_str}.", image]
            return image

        if saved_path_str:
            return f"Custom emoji {doc_id} downloaded and saved to {saved_path_str}."

        return (
            f"Custom emoji {doc_id} has format '{ext.lstrip('.')}' "
            f"({getattr(doc, 'mime_type', 'unknown')}) "
            "which cannot be rendered as a static image without thumbnail. "
            "Please call with thumbnail=True or provide save_path to download."
        )
    except Exception as e:
        return log_and_format_error(
            "preview_custom_emoji", e, document_id=document_id, save_path=save_path
        )


# Telegram's emoji keywords (the words that turn into emoji when you type them in a
# client) are one large dictionary per language that rarely changes, so each is fetched
# once and kept in memory: lang_code -> (monotonic time fetched, {keyword: [emoticon]}).
EMOJI_KEYWORDS_TTL = 24 * 60 * 60.0
_emoji_keywords: Dict[str, Tuple[float, Dict[str, List[str]]]] = {}
_emoji_keyword_locks: Dict[str, asyncio.Lock] = {}


def emoji_search_language() -> str:
    """``TELEGRAM_EMOJI_SEARCH_LANGUAGE``: language search_custom_emoji reads words in
    (default ``en``). A per-call ``lang_code`` takes precedence."""
    raw = os.getenv("TELEGRAM_EMOJI_SEARCH_LANGUAGE", "").strip().lower()
    return raw or "en"


async def emoji_keywords(cl, lang_code: str) -> Dict[str, List[str]]:
    """{keyword: [emoticon, ...]} for a language, from messages.getEmojiKeywords.

    Keywords are lower-cased so a lookup ignores case. The dictionary is cached per
    language for EMOJI_KEYWORDS_TTL; concurrent first calls share one request. A language
    Telegram has no keywords for comes back empty and is not cached.
    """
    async with _emoji_keyword_locks.setdefault(lang_code, asyncio.Lock()):
        cached = _emoji_keywords.get(lang_code)
        if cached and time.monotonic() - cached[0] < EMOJI_KEYWORDS_TTL:
            return cached[1]
        result = await cl(functions.messages.GetEmojiKeywordsRequest(lang_code=lang_code))
        keywords: Dict[str, List[str]] = {}
        for item in getattr(result, "keywords", []) or []:
            if not isinstance(item, types.EmojiKeyword):
                continue
            emoticons = keywords.setdefault(item.keyword.casefold(), [])
            for emoticon in item.emoticons:
                if emoticon not in emoticons:
                    emoticons.append(emoticon)
        if keywords:
            _emoji_keywords[lang_code] = (time.monotonic(), keywords)
        return keywords


async def find_custom_emoji(cl, emoticons: List[str], limit: int) -> Dict[int, str]:
    """{document ID: emoticon it was found under} for up to ``limit`` custom emoji.

    Emoticons are searched in order and the search stops once ``limit`` is reached, so a
    word that maps to many emoji does not cost a request for each.
    """
    found: Dict[int, str] = {}
    for emoticon in emoticons:
        if len(found) >= limit:
            break
        reply = await cl(functions.messages.SearchCustomEmojiRequest(emoticon=emoticon, hash=0))
        for doc_id in getattr(reply, "document_id", None) or []:
            found.setdefault(doc_id, emoticon)
    return dict(list(found.items())[: max(limit, 0)])


@mcp.tool(
    annotations=ToolAnnotations(title="Search Custom Emoji", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def search_custom_emoji(
    query: str,
    limit: int = 20,
    lang_code: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """
    Search custom emoji by a word or by an emoji. A word is turned into emoji through
    Telegram's emoji keywords and the custom emoji made for each of them are returned;
    an emoji is searched as it is. Each result is an {"emoji", "id"} entry, the form
    send_message takes as custom_emojis; its id is the document_id for
    preview_custom_emoji.

    Args:
        query: A word (e.g. 'snow', 'fire', 'heart') or an emoji (e.g. '❄️').
        limit: Maximum number of custom emoji to return (default: 20).
        lang_code: Language of the word (e.g. 'en', 'ru'). Defaults to
            TELEGRAM_EMOJI_SEARCH_LANGUAGE, or 'en'. Not used for an emoji.
        account: Optional account alias or phone number.
    """
    try:
        text = " ".join((query or "").split())
        if not text:
            return "Invalid query: provide a word or an emoji."

        cl = get_client(account)
        await ensure_connected(cl)

        result: Dict[str, Any] = {"query": text}
        if any(char.isalnum() for char in text):
            language = (lang_code or emoji_search_language()).strip().lower()
            keywords = await emoji_keywords(cl, language)
            emoticons = keywords.get(text.casefold(), [])
            result["lang_code"] = language
        else:
            emoticons = [text]

        found = await find_custom_emoji(cl, emoticons, limit)
        result["matched_emoji"] = emoticons
        result["count"] = len(found)
        result["custom_emojis"] = [
            {
                "emoji": sanitize_user_content(emoticon, max_length=64, preserve_emoji=True),
                "id": str(doc_id),
            }
            for doc_id, emoticon in found.items()
        ]

        return json.dumps(result, indent=2, default=json_serializer, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error(
            "search_custom_emoji", e, query=query, limit=limit, lang_code=lang_code
        )


__all__ = [
    "extract_pack_short_name",
    "get_custom_emoji_packs",
    "get_custom_emoji_pack",
    "preview_custom_emoji",
    "search_custom_emoji",
]
