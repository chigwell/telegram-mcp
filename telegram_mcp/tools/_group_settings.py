"""Private groups implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Optional, Union
from mcp.server.fastmcp import Context


async def edit_chat_title(
    chat_id: Union[int, str],
    title: str,
    account: str = None,
    *,
    Channel,
    Chat,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        if isinstance(entity, Channel):
            await cl(functions.channels.EditTitleRequest(channel=entity, title=title))
        elif isinstance(entity, Chat):
            # messages.* requests take the positive Chat.id; the raw argument may be a
            # negative Bot-API-style id or a username, which Telegram rejects.
            await cl(functions.messages.EditChatTitleRequest(chat_id=entity.id, title=title))
        else:
            return f"Cannot edit title for this entity type ({type(entity)})."
        return f"Chat {chat_id} title updated to '{sanitize_name(title)}'."
    except Exception as e:
        return log_and_format_error("edit_chat_title", e, chat_id=chat_id, title=title)


async def edit_chat_photo(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
    account: str = None,
    *,
    Channel,
    Chat,
    InputChatUploadedPhoto,
    _resolve_readable_file_path,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="edit_chat_photo",
        )
        if path_error:
            return path_error

        entity = await resolve_entity(chat_id, cl)
        uploaded_file = await cl.upload_file(str(safe_path))

        if isinstance(entity, Channel):
            # For channels/supergroups, use EditPhotoRequest with InputChatUploadedPhoto
            input_photo = InputChatUploadedPhoto(file=uploaded_file)
            await cl(functions.channels.EditPhotoRequest(channel=entity, photo=input_photo))
        elif isinstance(entity, Chat):
            # For basic groups, use EditChatPhotoRequest with InputChatUploadedPhoto
            input_photo = InputChatUploadedPhoto(file=uploaded_file)
            await cl(functions.messages.EditChatPhotoRequest(chat_id=entity.id, photo=input_photo))
        else:
            return f"Cannot edit photo for this entity type ({type(entity)})."

        return f"Chat {chat_id} photo updated from {safe_path}."
    except Exception as e:
        return log_and_format_error("edit_chat_photo", e, chat_id=chat_id, file_path=file_path)


async def edit_chat_about(
    chat_id: Union[int, str],
    about: str,
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        await cl(functions.messages.EditChatAboutRequest(peer=entity, about=about))
        return f"Chat {chat_id} description updated."
    except telethon.errors.rpcerrorlist.ChatAboutNotModifiedError:
        return f"Chat {chat_id} description is already set to the requested value."
    except telethon.errors.rpcerrorlist.ChatAboutTooLongError:
        return "Error: description exceeds Telegram's 255 character limit."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: admin rights required to edit the chat description."
    except Exception as e:
        return log_and_format_error("edit_chat_about", e, chat_id=chat_id)


async def delete_chat_photo(
    chat_id: Union[int, str],
    account: str = None,
    *,
    Channel,
    Chat,
    InputChatPhotoEmpty,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        if isinstance(entity, Channel):
            # Use InputChatPhotoEmpty for channels/supergroups
            await cl(
                functions.channels.EditPhotoRequest(channel=entity, photo=InputChatPhotoEmpty())
            )
        elif isinstance(entity, Chat):
            # Use None (or InputChatPhotoEmpty) for basic groups
            await cl(
                functions.messages.EditChatPhotoRequest(
                    chat_id=entity.id, photo=InputChatPhotoEmpty()
                )
            )
        else:
            return f"Cannot delete photo for this entity type ({type(entity)})."

        return f"Chat {chat_id} photo deleted."
    except Exception as e:
        return log_and_format_error("delete_chat_photo", e, chat_id=chat_id)


async def set_default_chat_permissions(
    chat_id: Union[int, str],
    send_messages: bool = True,
    send_media: bool = True,
    send_stickers: bool = True,
    send_gifs: bool = True,
    send_games: bool = True,
    send_inline: bool = True,
    embed_links: bool = True,
    send_polls: bool = True,
    change_info: bool = False,
    invite_users: bool = True,
    pin_messages: bool = False,
    until_date: int = 0,
    account: str = None,
    *,
    ChatBannedRights,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        banned_rights = ChatBannedRights(
            until_date=until_date if until_date else None,
            send_messages=not send_messages,
            send_media=not send_media,
            send_stickers=not send_stickers,
            send_gifs=not send_gifs,
            send_games=not send_games,
            send_inline=not send_inline,
            embed_links=not embed_links,
            send_polls=not send_polls,
            change_info=not change_info,
            invite_users=not invite_users,
            pin_messages=not pin_messages,
        )
        await cl(
            functions.messages.EditChatDefaultBannedRightsRequest(
                peer=entity, banned_rights=banned_rights
            )
        )
        return f"Default permissions for chat {chat_id} updated."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: admin rights required to change default permissions."
    except telethon.errors.rpcerrorlist.ChatNotModifiedError:
        return f"Chat {chat_id} default permissions unchanged (already matched)."
    except Exception as e:
        return log_and_format_error("set_default_chat_permissions", e, chat_id=chat_id)


async def toggle_slow_mode(
    chat_id: Union[int, str],
    seconds: int = 0,
    account: str = None,
    *,
    Channel,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "Error: slow mode is only supported for supergroups."
        await cl(functions.channels.ToggleSlowModeRequest(channel=entity, seconds=seconds))
        if seconds == 0:
            return f"Slow mode disabled for chat {chat_id}."
        return f"Slow mode enabled for chat {chat_id} (interval: {seconds}s)."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: admin rights required to toggle slow mode."
    except Exception as e:
        return log_and_format_error("toggle_slow_mode", e, chat_id=chat_id, seconds=seconds)
