import os
import time
import asyncio
from typing import List, Dict, Optional, Union, Any
from pathlib import Path
from telethon.tl.types import *
from telethon.tl.functions.messages import GetForumTopicsRequest, ReadDiscussionRequest
from telethon import functions, types, utils
from mcp.server.fastmcp import Context

# Use absolute imports for our own module as it's the standard for PyPI packages
from telegram_mcp.client import client, logger
from telegram_mcp.utils import *
from telegram_mcp.security import *


async def send_file(
    chat_id: Union[int, str],
    file_path: str,
    caption: str = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    Send a file to a chat.
    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path to the file under allowed roots.
        caption: Optional caption for the file.
    """
    try:
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="send_file",
        )
        if path_error:
            return path_error
        entity = await resolve_entity(chat_id)
        await client.send_file(entity, str(safe_path), caption=caption)
        return f"File sent to chat {chat_id} from {safe_path}."
    except Exception as e:
        return log_and_format_error(
            "send_file", e, chat_id=chat_id, file_path=file_path, caption=caption
        )


async def send_gif(chat_id: Union[int, str], gif_id: int) -> str:
    """
    Send a GIF to a chat by Telegram GIF document ID (not a file path).

    Args:
        chat_id: The chat ID or username.
        gif_id: Telegram document ID for the GIF (from get_gif_search).
    """
    try:
        if not isinstance(gif_id, int):
            return "gif_id must be a Telegram document ID (integer), not a file path. Use get_gif_search to find IDs."
        entity = await resolve_entity(chat_id)
        await client.send_file(entity, gif_id)
        return f"GIF sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error("send_gif", e, chat_id=chat_id, gif_id=gif_id)


async def download_media(
    chat_id: Union[int, str],
    message_id: int,
    file_path: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> str:
    """
    Download media from a message in a chat.
    Args:
        chat_id: The chat ID or username.
        message_id: The message ID containing the media.
        file_path: Optional absolute or relative path under allowed roots.
            If omitted, saves into `<first_root>/downloads/`.
    """
    try:
        entity = await resolve_entity(chat_id)
        msg = await client.get_messages(entity, ids=message_id)
        if not msg or not msg.media:
            return "No media found in the specified message."
        default_name = f"telegram_{chat_id}_{message_id}_{int(time.time())}"
        out_path, path_error = await _resolve_writable_file_path(
            raw_path=file_path, default_filename=default_name, ctx=ctx, tool_name="download_media"
        )
        if path_error:
            return path_error
        downloaded = await client.download_media(msg, file=str(out_path))
        if not downloaded:
            return f"Download failed for message {message_id}."
        final_path = Path(downloaded).resolve(strict=True)
        roots, roots_error = await _ensure_allowed_roots(ctx, "download_media")
        if roots_error:
            return roots_error
        if not _path_is_within_any_root(final_path, roots):
            return "Download failed: resulting path is outside allowed roots."
        return f"Media downloaded to {final_path}."
    except Exception as e:
        return log_and_format_error(
            "download_media", e, chat_id=chat_id, message_id=message_id, file_path=file_path
        )


async def set_profile_photo(file_path: str, ctx: Optional[Context] = None) -> str:
    """
    Set a new profile photo.
    """
    try:
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path, ctx=ctx, tool_name="set_profile_photo"
        )
        if path_error:
            return path_error
        await client(
            functions.photos.UploadProfilePhotoRequest(
                file=await client.upload_file(str(safe_path))
            )
        )
        return f"Profile photo updated from {safe_path}."
    except Exception as e:
        return log_and_format_error("set_profile_photo", e, file_path=file_path)


async def edit_chat_photo(
    chat_id: Union[int, str], file_path: str, ctx: Optional[Context] = None
) -> str:
    """
    Edit the photo of a chat, group, or channel. Requires a file path to an image.
    """
    try:
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path, ctx=ctx, tool_name="edit_chat_photo"
        )
        if path_error:
            return path_error
        entity = await resolve_entity(chat_id)
        uploaded_file = await client.upload_file(str(safe_path))
        if isinstance(entity, Channel):
            input_photo = InputChatUploadedPhoto(file=uploaded_file)
            await client(functions.channels.EditPhotoRequest(channel=entity, photo=input_photo))
        elif isinstance(entity, Chat):
            input_photo = InputChatUploadedPhoto(file=uploaded_file)
            await client(
                functions.messages.EditChatPhotoRequest(chat_id=chat_id, photo=input_photo)
            )
        else:
            return f"Cannot edit photo for this entity type ({type(entity)})."
        return f"Chat {chat_id} photo updated from {safe_path}."
    except Exception as e:
        logger.exception(f"edit_chat_photo failed (chat_id={chat_id}, file_path='{file_path}')")
        return log_and_format_error("edit_chat_photo", e, chat_id=chat_id, file_path=file_path)


async def send_voice(
    chat_id: Union[int, str], file_path: str, ctx: Optional[Context] = None
) -> str:
    """
    Send a voice message to a chat. File must be an OGG/OPUS voice note.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path under allowed roots to the OGG/OPUS file.
    """
    try:
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path, ctx=ctx, tool_name="send_voice"
        )
        if path_error:
            return path_error
        mime, _ = mimetypes.guess_type(str(safe_path))
        if not (
            mime
            and (
                mime == "audio/ogg"
                or str(safe_path).lower().endswith(".ogg")
                or str(safe_path).lower().endswith(".opus")
            )
        ):
            return "Voice file must be .ogg or .opus format."
        entity = await resolve_entity(chat_id)
        await client.send_file(entity, str(safe_path), voice_note=True)
        return f"Voice message sent to chat {chat_id} from {safe_path}."
    except Exception as e:
        return log_and_format_error("send_voice", e, chat_id=chat_id, file_path=file_path)


async def upload_file(file_path: str, ctx: Optional[Context] = None) -> str:
    """
    Upload a local file to Telegram and return upload metadata.

    Args:
        file_path: Absolute or relative path under allowed roots.
    """
    try:
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path, ctx=ctx, tool_name="upload_file"
        )
        if path_error:
            return path_error
        uploaded = await client.upload_file(str(safe_path))
        payload = {
            "path": str(safe_path),
            "name": getattr(uploaded, "name", safe_path.name),
            "size": getattr(uploaded, "size", safe_path.stat().st_size),
            "md5_checksum": getattr(uploaded, "md5_checksum", None),
        }
        return json.dumps(payload, indent=2, default=json_serializer)
    except Exception as e:
        return log_and_format_error("upload_file", e, file_path=file_path)
