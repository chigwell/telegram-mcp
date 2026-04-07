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


async def get_bot_info(bot_username: str) -> str:
    """
    Get information about a bot by username.
    """
    try:
        entity = await resolve_entity(bot_username)
        if not entity:
            return f"Bot with username {bot_username} not found."

        result = await client(functions.users.GetFullUserRequest(id=entity))

        # Create a more structured, serializable response
        if hasattr(result, "to_dict"):
            # Use custom serializer to handle non-serializable types
            return json.dumps(result.to_dict(), indent=2, default=json_serializer)
        else:
            # Fallback if to_dict is not available
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
        chat = await resolve_entity(group_id)
        user = await resolve_entity(user_id)
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


async def demote_admin(group_id: Union[int, str], user_id: Union[int, str]) -> str:
    """
    Demote a user from admin in a group/channel.

    Args:
        group_id: ID or username of the group/channel
        user_id: User ID or username to demote
    """
    try:
        chat = await resolve_entity(group_id)
        user = await resolve_entity(user_id)
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


async def ban_user(chat_id: Union[int, str], user_id: Union[int, str]) -> str:
    """
    Ban a user from a group or channel.

    Args:
        chat_id: ID or username of the group/channel
        user_id: User ID or username to ban
    """
    try:
        chat = await resolve_entity(chat_id)
        user = await resolve_entity(user_id)
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


async def unban_user(chat_id: Union[int, str], user_id: Union[int, str]) -> str:
    """
    Unban a user from a group or channel.

    Args:
        chat_id: ID or username of the group/channel
        user_id: User ID or username to unban
    """
    try:
        chat = await resolve_entity(chat_id)
        user = await resolve_entity(user_id)
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
