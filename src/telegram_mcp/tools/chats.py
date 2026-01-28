"""Chat management tools for telegram-mcp."""

import json
from typing import Union, List, Optional
from datetime import datetime, timedelta

from telethon import functions
from telethon.tl.types import User, Chat, Channel
import telethon.errors.rpcerrorlist

from ..app import mcp, client
from ..exceptions import log_and_format_error
from ..validators import validate_id
from ..formatters import format_entity, get_sender_name, get_engagement_info
from ..logging_config import logger
from mcp.types import ToolAnnotations


@mcp.tool(annotations=ToolAnnotations(title="Get Chats", openWorldHint=True, readOnlyHint=True))
async def get_chats(page: int = 1, page_size: int = 20) -> str:
    """
    Get a paginated list of chats.
    Args:
        page: Page number (1-indexed).
        page_size: Number of chats per page.
    """
    try:
        dialogs = await client.get_dialogs()
        start = (page - 1) * page_size
        end = start + page_size
        if start >= len(dialogs):
            return "Page out of range."
        chats = dialogs[start:end]
        lines = []
        for dialog in chats:
            entity = dialog.entity
            chat_id = entity.id
            title = getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
            lines.append(f"Chat ID: {chat_id}, Title: {title}")
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_chats", e)


@mcp.tool(annotations=ToolAnnotations(title="List Chats", openWorldHint=True, readOnlyHint=True))
async def list_chats(chat_type: str = None, limit: int = 20) -> str:
    """
    List available chats with metadata.

    Args:
        chat_type: Filter by chat type ('user', 'group', 'channel', or None for all)
        limit: Maximum number of chats to retrieve.
    """
    try:
        dialogs = await client.get_dialogs(limit=limit)

        results = []
        for dialog in dialogs:
            entity = dialog.entity

            # Filter by type if requested
            current_type = None
            if isinstance(entity, User):
                current_type = "user"
            elif isinstance(entity, Chat):
                current_type = "group"
            elif isinstance(entity, Channel):
                if getattr(entity, "broadcast", False):
                    current_type = "channel"
                else:
                    current_type = "group"  # Supergroup

            if chat_type and current_type != chat_type.lower():
                continue

            # Format chat info
            chat_info = f"Chat ID: {entity.id}"

            if hasattr(entity, "title"):
                chat_info += f", Title: {entity.title}"
            elif hasattr(entity, "first_name"):
                name = f"{entity.first_name}"
                if hasattr(entity, "last_name") and entity.last_name:
                    name += f" {entity.last_name}"
                chat_info += f", Name: {name}"

            chat_info += f", Type: {current_type}"

            if hasattr(entity, "username") and entity.username:
                chat_info += f", Username: @{entity.username}"

            # Add unread count if available
            unread_count = getattr(dialog, "unread_count", 0) or 0
            # Also check unread_mark (manual "mark as unread" flag)
            inner_dialog = getattr(dialog, "dialog", None)
            unread_mark = (
                bool(getattr(inner_dialog, "unread_mark", False)) if inner_dialog else False
            )

            if unread_count > 0:
                chat_info += f", Unread: {unread_count}"
            elif unread_mark:
                chat_info += ", Unread: marked"
            else:
                chat_info += ", No unread messages"

            results.append(chat_info)

        if not results:
            return f"No chats found matching the criteria."

        return "\n".join(results)
    except Exception as e:
        return log_and_format_error("list_chats", e, chat_type=chat_type, limit=limit)


@mcp.tool(annotations=ToolAnnotations(title="Get Chat", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_chat(chat_id: Union[int, str]) -> str:
    """
    Get detailed information about a specific chat.

    Args:
        chat_id: The ID or username of the chat.
    """
    try:
        entity = await client.get_entity(chat_id)

        result = []
        result.append(f"ID: {entity.id}")

        is_channel = isinstance(entity, Channel)
        is_chat = isinstance(entity, Chat)
        is_user = isinstance(entity, User)

        if hasattr(entity, "title"):
            result.append(f"Title: {entity.title}")
            chat_type = (
                "Channel" if is_channel and getattr(entity, "broadcast", False) else "Group"
            )
            if is_channel and getattr(entity, "megagroup", False):
                chat_type = "Supergroup"
            elif is_chat:
                chat_type = "Group (Basic)"
            result.append(f"Type: {chat_type}")
            if hasattr(entity, "username") and entity.username:
                result.append(f"Username: @{entity.username}")

            # Fetch participants count reliably
            try:
                participants_count = (await client.get_participants(entity, limit=0)).total
                result.append(f"Participants: {participants_count}")
            except Exception as pe:
                result.append(f"Participants: Error fetching ({pe})")

        elif is_user:
            name = f"{entity.first_name}"
            if entity.last_name:
                name += f" {entity.last_name}"
            result.append(f"Name: {name}")
            result.append(f"Type: User")
            if entity.username:
                result.append(f"Username: @{entity.username}")
            if entity.phone:
                result.append(f"Phone: {entity.phone}")
            result.append(f"Bot: {'Yes' if entity.bot else 'No'}")
            result.append(f"Verified: {'Yes' if entity.verified else 'No'}")

        # Get last activity if it's a dialog
        try:
            dialog = await client.get_dialogs(limit=1, offset_id=0, offset_peer=entity)
            if dialog:
                dialog = dialog[0]
                result.append(f"Unread Messages: {dialog.unread_count}")
                if dialog.message:
                    last_msg = dialog.message
                    sender_name = "Unknown"
                    if last_msg.sender:
                        sender_name = getattr(last_msg.sender, "first_name", "") or getattr(
                            last_msg.sender, "title", "Unknown"
                        )
                        if hasattr(last_msg.sender, "last_name") and last_msg.sender.last_name:
                            sender_name += f" {last_msg.sender.last_name}"
                    sender_name = sender_name.strip() or "Unknown"
                    result.append(f"Last Message: From {sender_name} at {last_msg.date}")
                    result.append(f"Message: {last_msg.message or '[Media/No text]'}")
        except Exception as diag_ex:
            logger.warning(f"Could not get dialog info for {chat_id}: {diag_ex}")
            pass

        return "\n".join(result)
    except Exception as e:
        return log_and_format_error("get_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Subscribe Public Channel",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@validate_id("channel")
async def subscribe_public_channel(channel: Union[int, str]) -> str:
    """
    Subscribe (join) to a public channel or supergroup by username or ID.
    """
    try:
        entity = await client.get_entity(channel)
        await client(functions.channels.JoinChannelRequest(channel=entity))
        title = getattr(entity, "title", getattr(entity, "username", "Unknown channel"))
        return f"Subscribed to {title}."
    except telethon.errors.rpcerrorlist.UserAlreadyParticipantError:
        title = getattr(entity, "title", getattr(entity, "username", "this channel"))
        return f"Already subscribed to {title}."
    except telethon.errors.rpcerrorlist.ChannelPrivateError:
        return "Cannot subscribe: this channel is private or requires an invite link."
    except Exception as e:
        return log_and_format_error("subscribe_public_channel", e, channel=channel)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Leave Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def leave_chat(chat_id: Union[int, str]) -> str:
    """
    Leave a group or channel by chat ID.

    Args:
        chat_id: The chat ID or username to leave.
    """
    try:
        entity = await client.get_entity(chat_id)

        # Check the entity type carefully
        if isinstance(entity, Channel):
            # Handle both channels and supergroups (which are also channels in Telegram)
            try:
                await client(functions.channels.LeaveChannelRequest(channel=entity))
                chat_name = getattr(entity, "title", str(chat_id))
                return f"Left channel/supergroup {chat_name} (ID: {chat_id})."
            except Exception as chan_err:
                return log_and_format_error("leave_chat", chan_err, chat_id=chat_id)

        elif isinstance(entity, Chat):
            # Traditional basic groups (not supergroups)
            try:
                # First try with InputPeerUser
                me = await client.get_me(input_peer=True)
                await client(
                    functions.messages.DeleteChatUserRequest(
                        chat_id=entity.id,
                        user_id=me,
                    )
                )
                chat_name = getattr(entity, "title", str(chat_id))
                return f"Left basic group {chat_name} (ID: {chat_id})."
            except Exception as chat_err:
                # If the above fails, try the second approach
                logger.warning(
                    f"First leave attempt failed: {chat_err}, trying alternative method"
                )

                try:
                    # Alternative approach - sometimes this works better
                    me_full = await client.get_me()
                    await client(
                        functions.messages.DeleteChatUserRequest(
                            chat_id=entity.id, user_id=me_full.id
                        )
                    )
                    chat_name = getattr(entity, "title", str(chat_id))
                    return f"Left basic group {chat_name} (ID: {chat_id})."
                except Exception as alt_err:
                    return log_and_format_error("leave_chat", alt_err, chat_id=chat_id)
        else:
            # Cannot leave a user chat this way
            entity_type = type(entity).__name__
            return log_and_format_error(
                "leave_chat",
                Exception(
                    f"Cannot leave chat ID {chat_id} of type {entity_type}. This function is for groups and channels only."
                ),
                chat_id=chat_id,
            )

    except Exception as e:
        logger.exception(f"leave_chat failed (chat_id={chat_id})")

        # Provide helpful hint for common errors
        error_str = str(e).lower()
        if "invalid" in error_str and "chat" in error_str:
            return log_and_format_error(
                "leave_chat",
                Exception(
                    f"Error leaving chat: This appears to be a channel/supergroup. Please check the chat ID and try again."
                ),
                chat_id=chat_id,
            )

        return log_and_format_error("leave_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Participants", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_participants(chat_id: Union[int, str]) -> str:
    """
    List all participants in a group or channel.
    Args:
        chat_id: The group or channel ID or username.
    """
    try:
        participants = await client.get_participants(chat_id)
        lines = [
            f"ID: {p.id}, Name: {getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}"
            for p in participants
        ]
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_participants", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Create Group", openWorldHint=True, destructiveHint=True)
)
@validate_id("user_ids")
async def create_group(title: str, user_ids: List[Union[int, str]]) -> str:
    """
    Create a new group or supergroup and add users.

    Args:
        title: Title for the new group
        user_ids: List of user IDs or usernames to add to the group
    """
    import asyncio
    try:
        # Convert user IDs to entities
        users = []
        for user_id in user_ids:
            try:
                user = await client.get_entity(user_id)
                users.append(user)
            except Exception as e:
                logger.error(f"Failed to get entity for user ID {user_id}: {e}")
                return f"Error: Could not find user with ID {user_id}"

        if not users:
            return "Error: No valid users provided"

        # Create the group with the users
        try:
            # Create a new chat with selected users
            result = await client(functions.messages.CreateChatRequest(users=users, title=title))

            # Check what type of response we got
            if hasattr(result, "chats") and result.chats:
                created_chat = result.chats[0]
                return f"Group created with ID: {created_chat.id}"
            elif hasattr(result, "chat") and result.chat:
                return f"Group created with ID: {result.chat.id}"
            elif hasattr(result, "chat_id"):
                return f"Group created with ID: {result.chat_id}"
            else:
                # If we can't determine the chat ID directly from the result
                # Try to find it in recent dialogs
                await asyncio.sleep(1)  # Give Telegram a moment to register the new group
                dialogs = await client.get_dialogs(limit=5)  # Get recent dialogs
                for dialog in dialogs:
                    if dialog.title == title:
                        return f"Group created with ID: {dialog.id}"

                # If we still can't find it, at least return success
                return f"Group created successfully. Please check your recent chats for '{title}'."

        except Exception as create_err:
            if "PEER_FLOOD" in str(create_err):
                return "Error: Cannot create group due to Telegram limits. Try again later."
            else:
                raise  # Let the outer exception handler catch it
    except Exception as e:
        logger.exception(f"create_group failed (title={title}, user_ids={user_ids})")
        return log_and_format_error("create_group", e, title=title, user_ids=user_ids)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Invite To Group", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("group_id", "user_ids")
async def invite_to_group(group_id: Union[int, str], user_ids: List[Union[int, str]]) -> str:
    """
    Invite users to a group or channel.

    Args:
        group_id: The ID or username of the group/channel.
        user_ids: List of user IDs or usernames to invite.
    """
    try:
        entity = await client.get_entity(group_id)
        users_to_add = []

        for user_id in user_ids:
            try:
                user = await client.get_entity(user_id)
                users_to_add.append(user)
            except ValueError as e:
                return f"Error: User with ID {user_id} could not be found. {e}"

        try:
            result = await client(
                functions.channels.InviteToChannelRequest(channel=entity, users=users_to_add)
            )

            invited_count = 0
            if hasattr(result, "users") and result.users:
                invited_count = len(result.users)
            elif hasattr(result, "count"):
                invited_count = result.count

            return f"Successfully invited {invited_count} users to {entity.title}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot invite users who are not mutual contacts. Please ensure the users are in your contacts and have added you back."
        except telethon.errors.rpcerrorlist.UserPrivacyRestrictedError:
            return (
                "Error: One or more users have privacy settings that prevent you from adding them."
            )
        except Exception as e:
            return log_and_format_error("invite_to_group", e, group_id=group_id, user_ids=user_ids)

    except Exception as e:
        logger.error(
            f"telegram_mcp invite_to_group failed (group_id={group_id}, user_ids={user_ids})",
            exc_info=True,
        )
        return log_and_format_error("invite_to_group", e, group_id=group_id, user_ids=user_ids)


@mcp.tool(
    annotations=ToolAnnotations(title="Create Channel", openWorldHint=True, destructiveHint=True)
)
async def create_channel(title: str, about: str = "", megagroup: bool = False) -> str:
    """
    Create a new channel or supergroup.
    """
    try:
        result = await client(
            functions.channels.CreateChannelRequest(title=title, about=about, megagroup=megagroup)
        )
        return f"Channel '{title}' created with ID: {result.chats[0].id}"
    except Exception as e:
        return log_and_format_error(
            "create_channel", e, title=title, about=about, megagroup=megagroup
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Chat Title", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def edit_chat_title(chat_id: Union[int, str], title: str) -> str:
    """
    Edit the title of a chat, group, or channel.
    """
    try:
        entity = await client.get_entity(chat_id)
        if isinstance(entity, Channel):
            await client(functions.channels.EditTitleRequest(channel=entity, title=title))
        elif isinstance(entity, Chat):
            await client(functions.messages.EditChatTitleRequest(chat_id=chat_id, title=title))
        else:
            return f"Cannot edit title for this entity type ({type(entity)})."
        return f"Chat {chat_id} title updated to '{title}'."
    except Exception as e:
        logger.exception(f"edit_chat_title failed (chat_id={chat_id}, title='{title}')")
        return log_and_format_error("edit_chat_title", e, chat_id=chat_id, title=title)


@mcp.tool(annotations=ToolAnnotations(title="List Topics", openWorldHint=True, readOnlyHint=True))
async def list_topics(
    chat_id: int,
    limit: int = 200,
    offset_topic: int = 0,
    search_query: str = None,
) -> str:
    """
    Retrieve forum topics from a supergroup with the forum feature enabled.

    Note for LLM: You can send a message to a selected topic via reply_to_message tool
    by using Topic ID as the message_id parameter.

    Args:
        chat_id: The ID of the forum-enabled chat (supergroup).
        limit: Maximum number of topics to retrieve.
        offset_topic: Topic ID offset for pagination.
        search_query: Optional query to filter topics by title.
    """
    try:
        entity = await client.get_entity(chat_id)

        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "The specified chat is not a supergroup."

        if not getattr(entity, "forum", False):
            return "The specified supergroup does not have forum topics enabled."

        result = await client(
            functions.channels.GetForumTopicsRequest(
                channel=entity,
                offset_date=0,
                offset_id=0,
                offset_topic=offset_topic,
                limit=limit,
                q=search_query or None,
            )
        )

        topics = getattr(result, "topics", None) or []
        if not topics:
            return "No topics found for this chat."

        messages_map = {}
        if getattr(result, "messages", None):
            messages_map = {message.id: message for message in result.messages}

        lines = []
        for topic in topics:
            line_parts = [f"Topic ID: {topic.id}"]

            title = getattr(topic, "title", None) or "(no title)"
            line_parts.append(f"Title: {title}")

            total_messages = getattr(topic, "total_messages", None)
            if total_messages is not None:
                line_parts.append(f"Messages: {total_messages}")

            unread_count = getattr(topic, "unread_count", None)
            if unread_count:
                line_parts.append(f"Unread: {unread_count}")

            if getattr(topic, "closed", False):
                line_parts.append("Closed: Yes")

            if getattr(topic, "hidden", False):
                line_parts.append("Hidden: Yes")

            top_message_id = getattr(topic, "top_message", None)
            top_message = messages_map.get(top_message_id)
            if top_message and getattr(top_message, "date", None):
                line_parts.append(f"Last Activity: {top_message.date.isoformat()}")

            lines.append(" | ".join(line_parts))

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "list_topics",
            e,
            chat_id=chat_id,
            limit=limit,
            offset_topic=offset_topic,
            search_query=search_query,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Search Public Chats", openWorldHint=True, readOnlyHint=True)
)
async def search_public_chats(query: str) -> str:
    """
    Search for public chats, channels, or bots by username or title.
    """
    try:
        result = await client(functions.contacts.SearchRequest(q=query, limit=20))
        return json.dumps([format_entity(u) for u in result.users], indent=2)
    except Exception as e:
        return log_and_format_error("search_public_chats", e, query=query)


@mcp.tool(
    annotations=ToolAnnotations(title="Mute Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def mute_chat(chat_id: Union[int, str]) -> str:
    """
    Mute notifications for a chat.
    """
    try:
        from telethon.tl.types import InputPeerNotifySettings

        peer = await client.get_entity(chat_id)
        await client(
            functions.account.UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(mute_until=2**31 - 1)
            )
        )
        return f"Chat {chat_id} muted."
    except (ImportError, AttributeError) as type_err:
        try:
            peer = await client.get_input_entity(chat_id)
            await client(
                functions.account.UpdateNotifySettingsRequest(
                    peer=peer,
                    settings={
                        "mute_until": 2**31 - 1,
                        "show_previews": False,
                        "silent": True,
                    },
                )
            )
            return f"Chat {chat_id} muted (using alternative method)."
        except Exception as alt_e:
            logger.exception(f"mute_chat (alt method) failed (chat_id={chat_id})")
            return log_and_format_error("mute_chat", alt_e, chat_id=chat_id)
    except Exception as e:
        logger.exception(f"mute_chat failed (chat_id={chat_id})")
        return log_and_format_error("mute_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Unmute Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def unmute_chat(chat_id: Union[int, str]) -> str:
    """
    Unmute notifications for a chat.
    """
    try:
        from telethon.tl.types import InputPeerNotifySettings

        peer = await client.get_entity(chat_id)
        await client(
            functions.account.UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(mute_until=0)
            )
        )
        return f"Chat {chat_id} unmuted."
    except (ImportError, AttributeError) as type_err:
        try:
            peer = await client.get_input_entity(chat_id)
            await client(
                functions.account.UpdateNotifySettingsRequest(
                    peer=peer,
                    settings={
                        "mute_until": 0,
                        "show_previews": True,
                        "silent": False,
                    },
                )
            )
            return f"Chat {chat_id} unmuted (using alternative method)."
        except Exception as alt_e:
            logger.exception(f"unmute_chat (alt method) failed (chat_id={chat_id})")
            return log_and_format_error("unmute_chat", alt_e, chat_id=chat_id)
    except Exception as e:
        logger.exception(f"unmute_chat failed (chat_id={chat_id})")
        return log_and_format_error("unmute_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Archive Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def archive_chat(chat_id: Union[int, str]) -> str:
    """
    Archive a chat.
    """
    try:
        await client(
            functions.messages.ToggleDialogPinRequest(
                peer=await client.get_entity(chat_id), pinned=True
            )
        )
        return f"Chat {chat_id} archived."
    except Exception as e:
        return log_and_format_error("archive_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Unarchive Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def unarchive_chat(chat_id: Union[int, str]) -> str:
    """
    Unarchive a chat.
    """
    try:
        await client(
            functions.messages.ToggleDialogPinRequest(
                peer=await client.get_entity(chat_id), pinned=False
            )
        )
        return f"Chat {chat_id} unarchived."
    except Exception as e:
        return log_and_format_error("unarchive_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Invite Link", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_invite_link(chat_id: Union[int, str]) -> str:
    """
    Get the invite link for a group or channel.
    """
    try:
        entity = await client.get_entity(chat_id)

        try:
            result = await client(functions.messages.ExportChatInviteRequest(peer=entity))
            return result.link
        except AttributeError:
            logger.warning("ExportChatInviteRequest not available, using alternative method")
        except Exception as e1:
            logger.warning(f"ExportChatInviteRequest failed: {e1}")

        try:
            invite_link = await client.export_chat_invite_link(entity)
            return invite_link
        except Exception as e2:
            logger.warning(f"export_chat_invite_link failed: {e2}")

        try:
            if isinstance(entity, (Chat, Channel)):
                full_chat = await client(functions.messages.GetFullChatRequest(chat_id=entity.id))
                if hasattr(full_chat, "full_chat") and hasattr(full_chat.full_chat, "invite_link"):
                    return full_chat.full_chat.invite_link or "No invite link available."
        except Exception as e3:
            logger.warning(f"GetFullChatRequest failed: {e3}")

        return "Could not retrieve invite link for this chat."
    except Exception as e:
        logger.exception(f"get_invite_link failed (chat_id={chat_id})")
        return log_and_format_error("get_invite_link", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Join Chat By Link", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def join_chat_by_link(link: str) -> str:
    """
    Join a chat by invite link.
    """
    try:
        # Extract the hash from the invite link
        if "/" in link:
            hash_part = link.split("/")[-1]
            if hash_part.startswith("+"):
                hash_part = hash_part[1:]
        else:
            hash_part = link

        try:
            invite_info = await client(functions.messages.CheckChatInviteRequest(hash=hash_part))
            if hasattr(invite_info, "chat") and invite_info.chat:
                chat_title = getattr(invite_info.chat, "title", "Unknown Chat")
                return f"You are already a member of this chat: {chat_title}"
        except Exception:
            pass

        result = await client(functions.messages.ImportChatInviteRequest(hash=hash_part))
        if result and hasattr(result, "chats") and result.chats:
            chat_title = getattr(result.chats[0], "title", "Unknown Chat")
            return f"Successfully joined chat: {chat_title}"
        return f"Joined chat via invite hash."
    except Exception as e:
        err_str = str(e).lower()
        if "expired" in err_str:
            return "The invite hash has expired and is no longer valid."
        elif "invalid" in err_str:
            return "The invite hash is invalid or malformed."
        elif "already" in err_str and "participant" in err_str:
            return "You are already a member of this chat."
        logger.exception(f"join_chat_by_link failed (link={link})")
        return f"Error joining chat: {e}"


@mcp.tool(
    annotations=ToolAnnotations(title="Export Chat Invite", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def export_chat_invite(chat_id: Union[int, str]) -> str:
    """
    Export a chat invite link.
    """
    try:
        entity = await client.get_entity(chat_id)

        try:
            result = await client(functions.messages.ExportChatInviteRequest(peer=entity))
            return result.link
        except AttributeError:
            logger.warning("ExportChatInviteRequest not available, using alternative method")
        except Exception as e1:
            logger.warning(f"ExportChatInviteRequest failed: {e1}")

        try:
            invite_link = await client.export_chat_invite_link(entity)
            return invite_link
        except Exception as e2:
            logger.warning(f"export_chat_invite_link failed: {e2}")
            return log_and_format_error("export_chat_invite", e2, chat_id=chat_id)

    except Exception as e:
        logger.exception(f"export_chat_invite failed (chat_id={chat_id})")
        return log_and_format_error("export_chat_invite", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Import Chat Invite", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def import_chat_invite(hash: str) -> str:
    """
    Import a chat invite by hash.
    """
    try:
        if hash.startswith("+"):
            hash = hash[1:]

        try:
            invite_info = await client(functions.messages.CheckChatInviteRequest(hash=hash))
            if hasattr(invite_info, "chat") and invite_info.chat:
                chat_title = getattr(invite_info.chat, "title", "Unknown Chat")
                return f"You are already a member of this chat: {chat_title}"
        except Exception as check_err:
            pass

        try:
            result = await client(functions.messages.ImportChatInviteRequest(hash=hash))
            if result and hasattr(result, "chats") and result.chats:
                chat_title = getattr(result.chats[0], "title", "Unknown Chat")
                return f"Successfully joined chat: {chat_title}"
            return f"Joined chat via invite hash."
        except Exception as join_err:
            err_str = str(join_err).lower()
            if "expired" in err_str:
                return "The invite hash has expired and is no longer valid."
            elif "invalid" in err_str:
                return "The invite hash is invalid or malformed."
            elif "already" in err_str and "participant" in err_str:
                return "You are already a member of this chat."
            elif "admin" in err_str:
                return "Cannot join this chat - requires admin approval."
            elif "too much" in err_str or "too many" in err_str:
                return "Cannot join this chat - it has reached maximum number of participants."
            else:
                raise

    except Exception as e:
        logger.exception(f"import_chat_invite failed (hash={hash})")
        return log_and_format_error("import_chat_invite", e, hash=hash)
