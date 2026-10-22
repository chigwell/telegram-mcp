"""Custom Emoji MCP tools for browsing, inspecting, and previewing custom emoji packs."""

import json
import re
from typing import Optional, Union

from mcp.server.fastmcp import Context, Image
from mcp.types import ToolAnnotations
from telethon import functions

from sanitize import sanitize_name
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


def image_format(data: bytes):
    """Classify downloaded bytes as (extension, Pillow format or None)."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "jpeg"
    if data.startswith(b"\x89PNG"):
        return ".png", "png"
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return ".webp", "webp"
    if data.startswith(b"\x1f\x8b"):
        return ".tgs", None
    if data.startswith(b"\x1aE\xdf\xa3"):
        return ".webm", None
    return ".webp", None


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
        get_sticker_pack or message metadata).
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

        ext, img_format = image_format(data)

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


__all__ = [
    "extract_pack_short_name",
    "get_custom_emoji_packs",
    "preview_custom_emoji",
    "image_format",
]
