"""Media handling tools for telegram-mcp."""

import json
import os
import mimetypes
import tempfile
import urllib.request
from typing import Union, List
from urllib.parse import urlparse

from telethon import functions, types

from ..app import mcp, client
from ..exceptions import log_and_format_error
from ..validators import validate_id
from ..formatters import json_serializer
from ..logging_config import logger
from mcp.types import ToolAnnotations


def _validate_image_url(url: str) -> tuple[bool, str]:
    """Validate a single image URL.

    Args:
        url: URL string to validate.

    Returns:
        Tuple of (is_valid, error_message). If valid, error_message is empty.
    """
    if not isinstance(url, str):
        return False, f"URL must be a string, got {type(url).__name__}"
    if not url or not url.strip():
        return False, "URL cannot be empty"

    url = url.strip()
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False, f"URL must use http or https protocol, got '{parsed.scheme}'"
        if not parsed.netloc:
            return False, "Invalid URL format: missing domain"
    except Exception as e:
        return False, f"Invalid URL format: {e}"

    return True, ""


@mcp.tool(annotations=ToolAnnotations(title="Send File", openWorldHint=True, destructiveHint=True))
@validate_id("chat_id")
async def send_file(chat_id: Union[int, str], file_path: str, caption: str = None) -> str:
    """
    Send a file to a chat.
    Args:
        chat_id: The chat ID or username.
        file_path: Absolute path to the file to send (must exist and be readable).
        caption: Optional caption for the file.
    """
    try:
        if not os.path.isfile(file_path):
            return f"File not found: {file_path}"
        if not os.access(file_path, os.R_OK):
            return f"File is not readable: {file_path}"
        entity = await client.get_entity(chat_id)
        await client.send_file(entity, file_path, caption=caption)
        return f"File sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error(
            "send_file", e, chat_id=chat_id, file_path=file_path, caption=caption
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Download Media", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def download_media(chat_id: Union[int, str], message_id: int, file_path: str) -> str:
    """
    Download media from a message in a chat.
    Args:
        chat_id: The chat ID or username.
        message_id: The message ID containing the media.
        file_path: Absolute path to save the downloaded file (must be writable).
    """
    try:
        entity = await client.get_entity(chat_id)
        msg = await client.get_messages(entity, ids=message_id)
        if not msg or not msg.media:
            return "No media found in the specified message."
        dir_path = os.path.dirname(file_path) or "."
        if not os.access(dir_path, os.W_OK):
            return f"Directory not writable: {dir_path}"
        await client.download_media(msg, file=file_path)
        if not os.path.isfile(file_path):
            return f"Download failed: file not created at {file_path}"
        return f"Media downloaded to {file_path}."
    except Exception as e:
        return log_and_format_error(
            "download_media",
            e,
            chat_id=chat_id,
            message_id=message_id,
            file_path=file_path,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Voice", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def send_voice(chat_id: Union[int, str], file_path: str) -> str:
    """
    Send a voice message to a chat. File must be an OGG/OPUS voice note.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute path to the OGG/OPUS file.
    """
    try:
        if not os.path.isfile(file_path):
            return f"File not found: {file_path}"
        if not os.access(file_path, os.R_OK):
            return f"File is not readable: {file_path}"

        mime, _ = mimetypes.guess_type(file_path)
        if not (
            mime
            and (
                mime == "audio/ogg"
                or file_path.lower().endswith(".ogg")
                or file_path.lower().endswith(".opus")
            )
        ):
            return "Voice file must be .ogg or .opus format."

        entity = await client.get_entity(chat_id)
        await client.send_file(entity, file_path, voice_note=True)
        return f"Voice message sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error("send_voice", e, chat_id=chat_id, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Media Info", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_media_info(chat_id: Union[int, str], message_id: int) -> str:
    """
    Get info about media in a message.

    Args:
        chat_id: The chat ID or username.
        message_id: The message ID.
    """
    try:
        entity = await client.get_entity(chat_id)
        msg = await client.get_messages(entity, ids=message_id)

        if not msg or not msg.media:
            return "No media found in the specified message."

        return str(msg.media)
    except Exception as e:
        return log_and_format_error("get_media_info", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Sticker Sets", openWorldHint=True, readOnlyHint=True)
)
async def get_sticker_sets() -> str:
    """
    Get all sticker sets.
    """
    try:
        result = await client(functions.messages.GetAllStickersRequest(hash=0))
        return json.dumps([s.title for s in result.sets], indent=2)
    except Exception as e:
        return log_and_format_error("get_sticker_sets", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Send Sticker", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def send_sticker(chat_id: Union[int, str], file_path: str) -> str:
    """
    Send a sticker to a chat. File must be a valid .webp sticker file.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute path to the .webp sticker file.
    """
    try:
        if not os.path.isfile(file_path):
            return f"Sticker file not found: {file_path}"
        if not os.access(file_path, os.R_OK):
            return f"Sticker file is not readable: {file_path}"
        if not file_path.lower().endswith(".webp"):
            return "Sticker file must be a .webp file."

        entity = await client.get_entity(chat_id)
        await client.send_file(entity, file_path, force_document=False)
        return f"Sticker sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error("send_sticker", e, chat_id=chat_id, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Gif Search", openWorldHint=True, readOnlyHint=True)
)
async def get_gif_search(query: str, limit: int = 10) -> str:
    """
    Search for GIFs by query. Returns a list of Telegram document IDs (not file paths).

    Args:
        query: Search term for GIFs.
        limit: Max number of GIFs to return.
    """
    try:
        try:
            result = await client(
                functions.messages.SearchGifsRequest(q=query, offset_id=0, limit=limit)
            )
            if not result.gifs:
                return "[]"
            return json.dumps(
                [g.document.id for g in result.gifs], indent=2, default=json_serializer
            )
        except (AttributeError, ImportError):
            try:
                from telethon.tl.types import InputMessagesFilterGif

                result = await client(
                    functions.messages.SearchRequest(
                        peer="gif",
                        q=query,
                        filter=InputMessagesFilterGif(),
                        min_date=None,
                        max_date=None,
                        offset_id=0,
                        add_offset=0,
                        limit=limit,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    )
                )
                if not result or not hasattr(result, "messages") or not result.messages:
                    return "[]"
                gif_ids = []
                for msg in result.messages:
                    if hasattr(msg, "media") and msg.media and hasattr(msg.media, "document"):
                        gif_ids.append(msg.media.document.id)
                return json.dumps(gif_ids, default=json_serializer)
            except Exception as inner_e:
                return f"Could not search GIFs using available methods: {inner_e}"
    except Exception as e:
        logger.exception(f"get_gif_search failed (query={query}, limit={limit})")
        return log_and_format_error("get_gif_search", e, query=query, limit=limit)


@mcp.tool(annotations=ToolAnnotations(title="Send Gif", openWorldHint=True, destructiveHint=True))
@validate_id("chat_id")
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
        entity = await client.get_entity(chat_id)
        await client.send_file(entity, gif_id)
        return f"GIF sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error("send_gif", e, chat_id=chat_id, gif_id=gif_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Bot Info", openWorldHint=True, readOnlyHint=True))
async def get_bot_info(bot_username: str) -> str:
    """
    Get information about a bot by username.
    """
    try:
        entity = await client.get_entity(bot_username)
        if not entity:
            return f"Bot with username {bot_username} not found."

        result = await client(functions.users.GetFullUserRequest(id=entity))

        if hasattr(result, "to_dict"):
            return json.dumps(result.to_dict(), indent=2, default=json_serializer)
        else:
            info = {
                "bot_info": {
                    "id": entity.id,
                    "username": entity.username,
                    "first_name": entity.first_name,
                    "last_name": getattr(entity, "last_name", ""),
                    "is_bot": getattr(entity, "bot", False),
                    "verified": getattr(entity, "verified", False),
                }
            }
            if hasattr(result, "full_user") and hasattr(result.full_user, "about"):
                info["bot_info"]["about"] = result.full_user.about
            return json.dumps(info, indent=2)
    except Exception as e:
        logger.exception(f"get_bot_info failed (bot_username={bot_username})")
        return log_and_format_error("get_bot_info", e, bot_username=bot_username)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Bot Commands", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def set_bot_commands(bot_username: str, commands: list) -> str:
    """
    Set bot commands for a bot you own.
    Note: This function can only be used if the Telegram client is a bot account.
    Regular user accounts cannot set bot commands.

    Args:
        bot_username: The username of the bot to set commands for.
        commands: List of command dictionaries with 'command' and 'description' keys.
    """
    try:
        me = await client.get_me()
        if not getattr(me, "bot", False):
            return "Error: This function can only be used by bot accounts. Your current Telegram account is a regular user account, not a bot."

        from telethon.tl.types import BotCommand, BotCommandScopeDefault
        from telethon.tl.functions.bots import SetBotCommandsRequest

        bot_commands = [
            BotCommand(command=c["command"], description=c["description"]) for c in commands
        ]

        bot = await client.get_entity(bot_username)

        await client(
            SetBotCommandsRequest(
                scope=BotCommandScopeDefault(),
                lang_code="en",
                commands=bot_commands,
            )
        )

        return f"Bot commands set for {bot_username}."
    except ImportError as ie:
        logger.exception(f"set_bot_commands failed - ImportError: {ie}")
        return log_and_format_error("set_bot_commands", ie)
    except Exception as e:
        logger.exception(f"set_bot_commands failed (bot_username={bot_username})")
        return log_and_format_error("set_bot_commands", e, bot_username=bot_username)


def _download_url_to_temp(url: str, index: int) -> str:
    """Download a URL to a temporary file.

    Args:
        url: URL to download.
        index: Index for naming the temp file.

    Returns:
        Path to the temporary file.

    Raises:
        Exception: If download fails.
    """
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, f"telegram_mcp_photo_{index}_{os.getpid()}.jpg")

    opener = urllib.request.build_opener()
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]
    urllib.request.install_opener(opener)
    urllib.request.urlretrieve(url.strip(), temp_path)

    return temp_path


@mcp.tool(
    annotations=ToolAnnotations(
        title="Send Photo From URL", openWorldHint=True, destructiveHint=True
    )
)
@validate_id("chat_id")
async def send_photo_from_url(
    chat_id: Union[int, str],
    url: Union[str, List[str]],
    caption: str = None,
) -> str:
    """
    Send photo(s) from URL(s) to a chat. Supports single image or media group (album).

    Args:
        chat_id: The chat ID or username.
        url: Single URL or list of URLs (max 10 for media group/album).
        caption: Optional caption for the photo(s).
    """
    temp_files = []
    try:
        # Normalize to list
        urls = [url] if isinstance(url, str) else list(url)

        # Validate count
        if len(urls) == 0:
            return "Error: At least one URL is required."
        if len(urls) > 10:
            return "Error: Media group cannot exceed 10 images."

        # Validate each URL
        for i, u in enumerate(urls):
            is_valid, error = _validate_image_url(u)
            if not is_valid:
                return f"Error: Invalid URL at index {i}: {error}"

        entity = await client.get_entity(chat_id)

        # Send single image using InputMediaPhotoExternal
        if len(urls) == 1:
            media = types.InputMediaPhotoExternal(url=urls[0].strip())
            await client(
                functions.messages.SendMediaRequest(
                    peer=entity,
                    media=media,
                    message=caption or "",
                )
            )
            return f"Photo sent to chat {chat_id}."

        # For multiple images, download first then send as album
        # (Telegram API doesn't support external URLs for albums directly)
        for i, u in enumerate(urls):
            temp_path = _download_url_to_temp(u, i)
            temp_files.append(temp_path)

        await client.send_file(entity, temp_files, caption=caption, force_document=False)
        return f"Media group ({len(urls)} photos) sent to chat {chat_id}."

    except Exception as e:
        return log_and_format_error(
            "send_photo_from_url", e, chat_id=chat_id, url=url, caption=caption
        )
    finally:
        # Clean up temp files
        for f in temp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except OSError:
                pass
