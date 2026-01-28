"""Admin management tools for telegram-mcp."""

import json
from typing import Union

from telethon import functions
from telethon.tl.types import (
    ChatAdminRights,
    ChatBannedRights,
    ChannelParticipantsKicked,
    ChannelParticipantsAdmins,
    InputChatUploadedPhoto,
    InputChatPhotoEmpty,
    Chat,
    Channel,
)
import telethon.errors.rpcerrorlist
import os

from ..app import mcp, client
from ..exceptions import log_and_format_error
from ..validators import validate_id
from ..formatters import json_serializer
from ..logging_config import logger
from mcp.types import ToolAnnotations


@mcp.tool(
    annotations=ToolAnnotations(
        title="Promote Admin", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("group_id", "user_id")
async def promote_admin(
    group_id: Union[int, str], user_id: Union[int, str], rights: dict = None
) -> str:
    """
    Promote a user to admin in a group/channel.

    Args:
        group_id: ID or username of the group/channel
        user_id: User ID or username to promote
        rights: Admin rights to give (optional)
    """
    try:
        chat = await client.get_entity(group_id)
        user = await client.get_entity(user_id)

        if not rights:
            rights = {
                "change_info": True,
                "post_messages": True,
                "edit_messages": True,
                "delete_messages": True,
                "ban_users": True,
                "invite_users": True,
                "pin_messages": True,
                "add_admins": False,
                "anonymous": False,
                "manage_call": True,
                "other": True,
            }

        admin_rights = ChatAdminRights(
            change_info=rights.get("change_info", True),
            post_messages=rights.get("post_messages", True),
            edit_messages=rights.get("edit_messages", True),
            delete_messages=rights.get("delete_messages", True),
            ban_users=rights.get("ban_users", True),
            invite_users=rights.get("invite_users", True),
            pin_messages=rights.get("pin_messages", True),
            add_admins=rights.get("add_admins", False),
            anonymous=rights.get("anonymous", False),
            manage_call=rights.get("manage_call", True),
            other=rights.get("other", True),
        )

        try:
            result = await client(
                functions.channels.EditAdminRequest(
                    channel=chat, user_id=user, admin_rights=admin_rights, rank="Admin"
                )
            )
            return f"Successfully promoted user {user_id} to admin in {chat.title}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot promote users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("promote_admin", e, group_id=group_id, user_id=user_id)

    except Exception as e:
        logger.error(
            f"telegram_mcp promote_admin failed (group_id={group_id}, user_id={user_id})",
            exc_info=True,
        )
        return log_and_format_error("promote_admin", e, group_id=group_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Demote Admin", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("group_id", "user_id")
async def demote_admin(group_id: Union[int, str], user_id: Union[int, str]) -> str:
    """
    Demote a user from admin in a group/channel.

    Args:
        group_id: ID or username of the group/channel
        user_id: User ID or username to demote
    """
    try:
        chat = await client.get_entity(group_id)
        user = await client.get_entity(user_id)

        admin_rights = ChatAdminRights(
            change_info=False,
            post_messages=False,
            edit_messages=False,
            delete_messages=False,
            ban_users=False,
            invite_users=False,
            pin_messages=False,
            add_admins=False,
            anonymous=False,
            manage_call=False,
            other=False,
        )

        try:
            result = await client(
                functions.channels.EditAdminRequest(
                    channel=chat, user_id=user, admin_rights=admin_rights, rank=""
                )
            )
            return f"Successfully demoted user {user_id} from admin in {chat.title}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot modify admin status of users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("demote_admin", e, group_id=group_id, user_id=user_id)

    except Exception as e:
        logger.error(
            f"telegram_mcp demote_admin failed (group_id={group_id}, user_id={user_id})",
            exc_info=True,
        )
        return log_and_format_error("demote_admin", e, group_id=group_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Ban User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id", "user_id")
async def ban_user(chat_id: Union[int, str], user_id: Union[int, str]) -> str:
    """
    Ban a user from a group or channel.

    Args:
        chat_id: ID or username of the group/channel
        user_id: User ID or username to ban
    """
    try:
        chat = await client.get_entity(chat_id)
        user = await client.get_entity(user_id)

        banned_rights = ChatBannedRights(
            until_date=None,
            view_messages=True,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True,
            send_polls=True,
            change_info=True,
            invite_users=True,
            pin_messages=True,
        )

        try:
            await client(
                functions.channels.EditBannedRequest(
                    channel=chat, participant=user, banned_rights=banned_rights
                )
            )
            return f"User {user_id} banned from chat {chat.title} (ID: {chat_id})."
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot ban users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.exception(f"ban_user failed (chat_id={chat_id}, user_id={user_id})")
        return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unban User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id", "user_id")
async def unban_user(chat_id: Union[int, str], user_id: Union[int, str]) -> str:
    """
    Unban a user from a group or channel.

    Args:
        chat_id: ID or username of the group/channel
        user_id: User ID or username to unban
    """
    try:
        chat = await client.get_entity(chat_id)
        user = await client.get_entity(user_id)

        unbanned_rights = ChatBannedRights(
            until_date=None,
            view_messages=False,
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_inline=False,
            embed_links=False,
            send_polls=False,
            change_info=False,
            invite_users=False,
            pin_messages=False,
        )

        try:
            await client(
                functions.channels.EditBannedRequest(
                    channel=chat, participant=user, banned_rights=unbanned_rights
                )
            )
            return f"User {user_id} unbanned from chat {chat.title} (ID: {chat_id})."
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot modify status of users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.exception(f"unban_user failed (chat_id={chat_id}, user_id={user_id})")
        return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Admins", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_admins(chat_id: Union[int, str]) -> str:
    """
    Get all admins in a group or channel.
    """
    try:
        participants = await client.get_participants(chat_id, filter=ChannelParticipantsAdmins())
        lines = [
            f"ID: {p.id}, Name: {getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
            for p in participants
        ]
        return "\n".join(lines) if lines else "No admins found."
    except Exception as e:
        logger.exception(f"get_admins failed (chat_id={chat_id})")
        return log_and_format_error("get_admins", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Banned Users", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_banned_users(chat_id: Union[int, str]) -> str:
    """
    Get all banned users in a group or channel.
    """
    try:
        participants = await client.get_participants(
            chat_id, filter=ChannelParticipantsKicked(q="")
        )
        lines = [
            f"ID: {p.id}, Name: {getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
            for p in participants
        ]
        return "\n".join(lines) if lines else "No banned users found."
    except Exception as e:
        logger.exception(f"get_banned_users failed (chat_id={chat_id})")
        return log_and_format_error("get_banned_users", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Recent Actions", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_recent_actions(chat_id: Union[int, str]) -> str:
    """
    Get recent admin actions (admin log) in a group or channel.
    """
    try:
        result = await client(
            functions.channels.GetAdminLogRequest(
                channel=chat_id,
                q="",
                events_filter=None,
                admins=[],
                max_id=0,
                min_id=0,
                limit=20,
            )
        )

        if not result or not result.events:
            return "No recent admin actions found."

        return json.dumps([e.to_dict() for e in result.events], indent=2, default=json_serializer)
    except Exception as e:
        logger.exception(f"get_recent_actions failed (chat_id={chat_id})")
        return log_and_format_error("get_recent_actions", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Chat Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def edit_chat_photo(chat_id: Union[int, str], file_path: str) -> str:
    """
    Edit the photo of a chat, group, or channel. Requires a file path to an image.
    """
    try:
        if not os.path.isfile(file_path):
            return f"Photo file not found: {file_path}"
        if not os.access(file_path, os.R_OK):
            return f"Photo file not readable: {file_path}"

        entity = await client.get_entity(chat_id)
        uploaded_file = await client.upload_file(file_path)

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

        return f"Chat {chat_id} photo updated."
    except Exception as e:
        logger.exception(f"edit_chat_photo failed (chat_id={chat_id}, file_path='{file_path}')")
        return log_and_format_error("edit_chat_photo", e, chat_id=chat_id, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Chat Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def delete_chat_photo(chat_id: Union[int, str]) -> str:
    """
    Delete the photo of a chat, group, or channel.
    """
    try:
        entity = await client.get_entity(chat_id)
        if isinstance(entity, Channel):
            await client(
                functions.channels.EditPhotoRequest(channel=entity, photo=InputChatPhotoEmpty())
            )
        elif isinstance(entity, Chat):
            await client(
                functions.messages.EditChatPhotoRequest(
                    chat_id=chat_id, photo=InputChatPhotoEmpty()
                )
            )
        else:
            return f"Cannot delete photo for this entity type ({type(entity)})."

        return f"Chat {chat_id} photo deleted."
    except Exception as e:
        logger.exception(f"delete_chat_photo failed (chat_id={chat_id})")
        return log_and_format_error("delete_chat_photo", e, chat_id=chat_id)
