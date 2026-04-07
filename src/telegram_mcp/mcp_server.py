import os
from typing import *
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

mcp = FastMCP("telegram")


allowed_tools_env = os.getenv("ALLOWED_TOOLS")
if allowed_tools_env:
    allowed_tools_list = [t.strip() for t in allowed_tools_env.split(",")]
    original_tool = mcp.tool

    def filtered_tool(*args, **kwargs):
        def decorator(func):
            if func.__name__ in allowed_tools_list:
                return original_tool(*args, **kwargs)(func)
            return func

        return decorator

    mcp.tool = filtered_tool

from . import chats
from . import messages
from . import media
from . import contacts
from . import groups
from . import folders
from .dtos import CreateFolderPayload, ImportContactsPayload
from .utils import validate_id


@mcp.tool(annotations=ToolAnnotations(title="Get Chats", openWorldHint=True, readOnlyHint=True))
async def get_chats(page: int = 1, page_size: int = 20) -> str:
    """
    Get a paginated list of chats.
    Args:
        page: Page number (1-indexed).
        page_size: Number of chats per page.
    """
    return await chats.get_chats(page, page_size)


@mcp.tool(annotations=ToolAnnotations(title="Get Messages", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_messages(chat_id: Union[int, str], page: int = 1, page_size: int = 20) -> str:
    """
    Get paginated messages from a specific chat.
    Args:
        chat_id: The ID or username of the chat.
        page: Page number (1-indexed).
        page_size: Number of messages per page.
    """
    return await messages.get_messages(chat_id, page, page_size)


@mcp.tool(
    annotations=ToolAnnotations(title="Send Message", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def send_message(
    chat_id: Union[int, str], message: str, parse_mode: Optional[str] = None
) -> str:
    """
    Send a message to a specific chat.
    Args:
        chat_id: The ID or username of the chat.
        message: The message content to send.
        parse_mode: Optional formatting mode. Use 'html' for HTML tags (<b>, <i>, <code>, <pre>,
            <a href="...">), 'md' or 'markdown' for Markdown (**bold**, __italic__, `code`,
            ```pre```), or omit for plain text (no formatting).
    """
    return await messages.send_message(chat_id, message, parse_mode)


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
    return await chats.subscribe_public_channel(channel)


@mcp.tool(
    annotations=ToolAnnotations(title="List Inline Buttons", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def list_inline_buttons(
    chat_id: Union[int, str], message_id: Optional[Union[int, str]] = None, limit: int = 20
) -> str:
    """
    Inspect inline buttons on a recent message to discover their indices/text/URLs.
    """
    return await messages.list_inline_buttons(chat_id, message_id, limit)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Press Inline Button", openWorldHint=True, destructiveHint=True
    )
)
@validate_id("chat_id")
async def press_inline_button(
    chat_id: Union[int, str],
    message_id: Optional[Union[int, str]] = None,
    button_text: Optional[str] = None,
    button_index: Optional[int] = None,
) -> str:
    """
    Press an inline button (callback) in a chat message.

    Args:
        chat_id: Chat or bot where the inline keyboard exists.
        message_id: Specific message ID to inspect. If omitted, searches recent messages for one containing buttons.
        button_text: Exact text of the button to press (case-insensitive).
        button_index: Zero-based index among all buttons if you prefer positional access.
    """
    return await messages.press_inline_button(chat_id, message_id, button_text, button_index)


@mcp.tool(
    annotations=ToolAnnotations(title="List Contacts", openWorldHint=True, readOnlyHint=True)
)
async def list_contacts() -> str:
    """
    List all contacts in your Telegram account.
    """
    return await contacts.list_contacts()


@mcp.tool(
    annotations=ToolAnnotations(title="Search Contacts", openWorldHint=True, readOnlyHint=True)
)
async def search_contacts(query: str) -> str:
    """
    Search for contacts by name, username, or phone number using Telethon's SearchRequest.
    Args:
        query: The search term to look for in contact names, usernames, or phone numbers.
    """
    return await contacts.search_contacts(query)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Contact Ids", openWorldHint=True, readOnlyHint=True)
)
async def get_contact_ids() -> str:
    """
    Get all contact IDs in your Telegram account.
    """
    return await contacts.get_contact_ids()


@mcp.tool(
    annotations=ToolAnnotations(title="List Messages", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def list_messages(
    chat_id: Union[int, str],
    limit: int = 20,
    search_query: str = None,
    from_date: str = None,
    to_date: str = None,
) -> str:
    """
    Retrieve messages with optional filters.

    Args:
        chat_id: The ID or username of the chat to get messages from.
        limit: Maximum number of messages to retrieve.
        search_query: Filter messages containing this text.
        from_date: Filter messages starting from this date (format: YYYY-MM-DD).
        to_date: Filter messages until this date (format: YYYY-MM-DD).
    """
    return await messages.list_messages(chat_id, limit, search_query, from_date, to_date)


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
    return await chats.list_topics(chat_id, limit, offset_topic, search_query)


@mcp.tool(annotations=ToolAnnotations(title="List Chats", openWorldHint=True, readOnlyHint=True))
async def list_chats(
    chat_type: str = None, limit: int = 20, unread_only: bool = False, unmuted_only: bool = False
) -> str:
    """
    List available chats with metadata.

    Args:
        chat_type: Filter by chat type ('user', 'group', 'channel', or None for all)
        limit: Maximum number of chats to retrieve from Telegram API (applied before filtering, so fewer results may be returned when filters are active).
        unread_only: If True, only return chats with unread messages.
        unmuted_only: If True, only return unmuted chats.
    """
    return await chats.list_chats(chat_type, limit, unread_only, unmuted_only)


@mcp.tool(annotations=ToolAnnotations(title="Get Chat", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_chat(chat_id: Union[int, str]) -> str:
    """
    Get detailed information about a specific chat.

    Args:
        chat_id: The ID or username of the chat.
    """
    return await chats.get_chat(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Direct Chat By Contact", openWorldHint=True, readOnlyHint=True
    )
)
async def get_direct_chat_by_contact(contact_query: str) -> str:
    """
    Find a direct chat with a specific contact by name, username, or phone.

    Args:
        contact_query: Name, username, or phone number to search for.
    """
    return await chats.get_direct_chat_by_contact(contact_query)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Contact Chats", openWorldHint=True, readOnlyHint=True)
)
@validate_id("contact_id")
async def get_contact_chats(contact_id: Union[int, str]) -> str:
    """
    List all chats involving a specific contact.

    Args:
        contact_id: The ID or username of the contact.
    """
    return await chats.get_contact_chats(contact_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Last Interaction", openWorldHint=True, readOnlyHint=True
    )
)
@validate_id("contact_id")
async def get_last_interaction(contact_id: Union[int, str]) -> str:
    """
    Get the most recent message with a contact.

    Args:
        contact_id: The ID or username of the contact.
    """
    return await chats.get_last_interaction(contact_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Message Context", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_message_context(
    chat_id: Union[int, str], message_id: int, context_size: int = 3
) -> str:
    """
    Retrieve context around a specific message.

    Args:
        chat_id: The ID or username of the chat.
        message_id: The ID of the central message.
        context_size: Number of messages before and after to include.
    """
    return await chats.get_message_context(chat_id, message_id, context_size)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add Contact", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def add_contact(
    phone: Optional[str] = None,
    first_name: str = "",
    last_name: str = "",
    username: Optional[str] = None,
) -> str:
    """
    Add a new contact to your Telegram account.
    Args:
        phone: The phone number of the contact (with country code). Required if username is not provided.
        first_name: The contact's first name.
        last_name: The contact's last name (optional).
        username: The Telegram username (without @). Use this for adding contacts without phone numbers.

    Note: Either phone or username must be provided. If username is provided, the function will resolve it
    and add the contact using contacts.addContact API (which supports adding contacts without phone numbers).
    """
    return await contacts.add_contact(phone, first_name, last_name, username)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Contact", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("user_id")
async def delete_contact(user_id: Union[int, str]) -> str:
    """
    Delete a contact by user ID.
    Args:
        user_id: The Telegram user ID or username of the contact to delete.
    """
    return await contacts.delete_contact(user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Block User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("user_id")
async def block_user(user_id: Union[int, str]) -> str:
    """
    Block a user by user ID.
    Args:
        user_id: The Telegram user ID or username to block.
    """
    return await contacts.block_user(user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unblock User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("user_id")
async def unblock_user(user_id: Union[int, str]) -> str:
    """
    Unblock a user by user ID.
    Args:
        user_id: The Telegram user ID or username to unblock.
    """
    return await contacts.unblock_user(user_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Me", openWorldHint=True, readOnlyHint=True))
async def get_me() -> str:
    """
    Get your own user information.
    """
    return await contacts.get_me()


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
    return await chats.create_group(title, user_ids)


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
    return await chats.invite_to_group(group_id, user_ids)


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
    return await chats.leave_chat(chat_id)


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
    return await chats.get_participants(chat_id)


@mcp.tool(annotations=ToolAnnotations(title="Send File", openWorldHint=True, destructiveHint=True))
@validate_id("chat_id")
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
    return await media.send_file(chat_id, file_path, caption, ctx)


@mcp.tool(
    annotations=ToolAnnotations(title="Download Media", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
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
    return await media.download_media(chat_id, message_id, file_path, ctx)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Profile", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def update_profile(first_name: str = None, last_name: str = None, about: str = None) -> str:
    """
    Update your profile information (name, bio).
    """
    return await contacts.update_profile(first_name, last_name, about)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Profile Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def set_profile_photo(file_path: str, ctx: Optional[Context] = None) -> str:
    """
    Set a new profile photo.
    """
    return await media.set_profile_photo(file_path, ctx)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Profile Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def delete_profile_photo() -> str:
    """
    Delete your current profile photo.
    """
    return await contacts.delete_profile_photo()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Privacy Settings", openWorldHint=True, readOnlyHint=True
    )
)
async def get_privacy_settings() -> str:
    """
    Get your privacy settings for last seen status.
    """
    return await contacts.get_privacy_settings()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Privacy Settings", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("allow_users", "disallow_users")
async def set_privacy_settings(
    key: str,
    allow_users: Optional[List[Union[int, str]]] = None,
    disallow_users: Optional[List[Union[int, str]]] = None,
) -> str:
    """
    Set privacy settings (e.g., last seen, phone, etc.).

    Args:
        key: The privacy setting to modify ('status' for last seen, 'phone', 'profile_photo', etc.)
        allow_users: List of user IDs or usernames to allow
        disallow_users: List of user IDs or usernames to disallow
    """
    return await contacts.set_privacy_settings(key, allow_users, disallow_users)


@mcp.tool(
    annotations=ToolAnnotations(title="Import Contacts", openWorldHint=True, destructiveHint=True)
)
async def import_contacts(payload: ImportContactsPayload) -> str:
    """
    Import a list of contacts.
    """
    return await contacts.import_contacts(payload.contact_list)


@mcp.tool(
    annotations=ToolAnnotations(title="Export Contacts", openWorldHint=True, readOnlyHint=True)
)
async def export_contacts() -> str:
    """
    Export all contacts as a JSON string.
    """
    return await contacts.export_contacts()


@mcp.tool(
    annotations=ToolAnnotations(title="Get Blocked Users", openWorldHint=True, readOnlyHint=True)
)
async def get_blocked_users() -> str:
    """
    Get a list of blocked users.
    """
    return await contacts.get_blocked_users()


@mcp.tool(
    annotations=ToolAnnotations(title="Create Channel", openWorldHint=True, destructiveHint=True)
)
async def create_channel(title: str, about: str = "", megagroup: bool = False) -> str:
    """
    Create a new channel or supergroup.
    """
    return await chats.create_channel(title, about, megagroup)


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
    return await chats.edit_chat_title(chat_id, title)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Chat Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def edit_chat_photo(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
) -> str:
    """
    Edit the photo of a chat, group, or channel. Requires a file path to an image.
    """
    return await media.edit_chat_photo(chat_id, file_path, ctx)


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
    return await chats.delete_chat_photo(chat_id)


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
    return await groups.promote_admin(group_id, user_id, rights)


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
    return await groups.demote_admin(group_id, user_id)


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
    return await groups.ban_user(chat_id, user_id)


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
    return await groups.unban_user(chat_id, user_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Admins", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_admins(chat_id: Union[int, str]) -> str:
    """
    Get all admins in a group or channel.
    """
    return await chats.get_admins(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Banned Users", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_banned_users(chat_id: Union[int, str]) -> str:
    """
    Get all banned users in a group or channel.
    """
    return await chats.get_banned_users(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Invite Link", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_invite_link(chat_id: Union[int, str]) -> str:
    """
    Get the invite link for a group or channel.
    """
    return await chats.get_invite_link(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Join Chat By Link", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def join_chat_by_link(link: str) -> str:
    """
    Join a chat by invite link.
    """
    return await chats.join_chat_by_link(link)


@mcp.tool(
    annotations=ToolAnnotations(title="Export Chat Invite", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def export_chat_invite(chat_id: Union[int, str]) -> str:
    """
    Export a chat invite link.
    """
    return await chats.export_chat_invite(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Import Chat Invite", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def import_chat_invite(hash: str) -> str:
    """
    Import a chat invite by hash.
    """
    return await chats.import_chat_invite(hash)


@mcp.tool(
    annotations=ToolAnnotations(title="Send Voice", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def send_voice(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
) -> str:
    """
    Send a voice message to a chat. File must be an OGG/OPUS voice note.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path under allowed roots to the OGG/OPUS file.
    """
    return await media.send_voice(chat_id, file_path, ctx)


@mcp.tool(
    annotations=ToolAnnotations(title="Upload File", openWorldHint=True, destructiveHint=True)
)
async def upload_file(file_path: str, ctx: Optional[Context] = None) -> str:
    """
    Upload a local file to Telegram and return upload metadata.

    Args:
        file_path: Absolute or relative path under allowed roots.
    """
    return await media.upload_file(file_path, ctx)


@mcp.tool(
    annotations=ToolAnnotations(title="Forward Message", openWorldHint=True, destructiveHint=True)
)
@validate_id("from_chat_id", "to_chat_id")
async def forward_message(
    from_chat_id: Union[int, str], message_id: int, to_chat_id: Union[int, str]
) -> str:
    """
    Forward a message from one chat to another.
    """
    return await messages.forward_message(from_chat_id, message_id, to_chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def edit_message(chat_id: Union[int, str], message_id: int, new_text: str) -> str:
    """
    Edit a message you sent.
    """
    return await messages.edit_message(chat_id, message_id, new_text)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def delete_message(chat_id: Union[int, str], message_id: int) -> str:
    """
    Delete a message by ID.
    """
    return await messages.delete_message(chat_id, message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Pin Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def pin_message(chat_id: Union[int, str], message_id: int) -> str:
    """
    Pin a message in a chat.
    """
    return await messages.pin_message(chat_id, message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unpin Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def unpin_message(chat_id: Union[int, str], message_id: int) -> str:
    """
    Unpin a message in a chat.
    """
    return await messages.unpin_message(chat_id, message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Mark As Read", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def mark_as_read(chat_id: Union[int, str]) -> str:
    """
    Mark all messages as read in a chat, including forum topics if applicable.
    """
    return await messages.mark_as_read(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Reply To Message", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def reply_to_message(
    chat_id: Union[int, str], message_id: int, text: str, parse_mode: Optional[str] = None
) -> str:
    """
    Reply to a specific message in a chat.
    Args:
        chat_id: The chat ID or username.
        message_id: The message ID to reply to.
        text: The reply text.
        parse_mode: Optional formatting mode. Use 'html' for HTML tags (<b>, <i>, <code>, <pre>,
            <a href="...">), 'md' or 'markdown' for Markdown (**bold**, __italic__, `code`,
            ```pre```), or omit for plain text (no formatting).
    """
    return await messages.reply_to_message(chat_id, message_id, text, parse_mode)


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
    return await media.get_media_info(chat_id, message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Search Public Chats", openWorldHint=True, readOnlyHint=True)
)
async def search_public_chats(query: str, limit: int = 20) -> str:
    """
    Search for public chats, channels, or bots by username or title.
    """
    return await chats.search_public_chats(query, limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Search Messages", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def search_messages(chat_id: Union[int, str], query: str, limit: int = 20) -> str:
    """
    Search for messages in a chat by text.
    """
    return await chats.search_messages(chat_id, query, limit)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search Global Messages",
        openWorldHint=True,
        readOnlyHint=True,
    )
)
async def search_global(query: str, page: int = 1, page_size: int = 20) -> str:
    """
    Search for messages across all public chats and channels by text content.
    """
    return await messages.search_global(query, page, page_size)


@mcp.tool(
    annotations=ToolAnnotations(title="Resolve Username", openWorldHint=True, readOnlyHint=True)
)
async def resolve_username(username: str) -> str:
    """
    Resolve a username to a user or chat ID.
    """
    return await contacts.resolve_username(username)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Mute Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def mute_chat(chat_id: Union[int, str]) -> str:
    """
    Mute notifications for a chat.
    """
    return await chats.mute_chat(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unmute Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def unmute_chat(chat_id: Union[int, str]) -> str:
    """
    Unmute notifications for a chat.
    """
    return await chats.unmute_chat(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Archive Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def archive_chat(chat_id: Union[int, str]) -> str:
    """
    Archive a chat.
    """
    return await chats.archive_chat(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unarchive Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def unarchive_chat(chat_id: Union[int, str]) -> str:
    """
    Unarchive a chat.
    """
    return await chats.unarchive_chat(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Sticker Sets", openWorldHint=True, readOnlyHint=True)
)
async def get_sticker_sets() -> str:
    """
    Get all sticker sets.
    """
    return await media.get_sticker_sets()


@mcp.tool(
    annotations=ToolAnnotations(title="Send Sticker", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def send_sticker(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
) -> str:
    """
    Send a sticker to a chat. File must be a valid .webp sticker file.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path under allowed roots to the .webp sticker file.
    """
    return await media.send_sticker(chat_id, file_path, ctx)


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
    return await media.get_gif_search(query, limit)


@mcp.tool(annotations=ToolAnnotations(title="Send Gif", openWorldHint=True, destructiveHint=True))
@validate_id("chat_id")
async def send_gif(chat_id: Union[int, str], gif_id: int) -> str:
    """
    Send a GIF to a chat by Telegram GIF document ID (not a file path).

    Args:
        chat_id: The chat ID or username.
        gif_id: Telegram document ID for the GIF (from get_gif_search).
    """
    return await media.send_gif(chat_id, gif_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Bot Info", openWorldHint=True, readOnlyHint=True))
async def get_bot_info(bot_username: str) -> str:
    """
    Get information about a bot by username.
    """
    return await groups.get_bot_info(bot_username)


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
    return await groups.set_bot_commands(bot_username, commands)


@mcp.tool(annotations=ToolAnnotations(title="Get History", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_history(chat_id: Union[int, str], limit: int = 100) -> str:
    """
    Get full chat history (up to limit).
    """
    return await messages.get_history(chat_id, limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Get User Photos", openWorldHint=True, readOnlyHint=True)
)
@validate_id("user_id")
async def get_user_photos(user_id: Union[int, str], limit: int = 10) -> str:
    """
    Get profile photos of a user.
    """
    return await contacts.get_user_photos(user_id, limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Get User Status", openWorldHint=True, readOnlyHint=True)
)
@validate_id("user_id")
async def get_user_status(user_id: Union[int, str]) -> str:
    """
    Get the online status of a user.
    """
    return await contacts.get_user_status(user_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Recent Actions", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_recent_actions(chat_id: Union[int, str]) -> str:
    """
    Get recent admin actions (admin log) in a group or channel.
    """
    return await chats.get_recent_actions(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Pinned Messages", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_pinned_messages(chat_id: Union[int, str]) -> str:
    """
    Get all pinned messages in a chat.
    """
    return await chats.get_pinned_messages(chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Create Poll", openWorldHint=True, destructiveHint=True)
)
async def create_poll(
    chat_id: int,
    question: str,
    options: list,
    multiple_choice: bool = False,
    quiz_mode: bool = False,
    public_votes: bool = True,
    close_date: str = None,
) -> str:
    """
    Create a poll in a chat using Telegram's native poll feature.

    Args:
        chat_id: The ID of the chat to send the poll to
        question: The poll question
        options: List of answer options (2-10 options)
        multiple_choice: Whether users can select multiple answers
        quiz_mode: Whether this is a quiz (has correct answer)
        public_votes: Whether votes are public
        close_date: Optional close date in ISO format (YYYY-MM-DD HH:MM:SS)
    """
    return await messages.create_poll(
        chat_id, question, options, multiple_choice, quiz_mode, public_votes, close_date
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Send Reaction", openWorldHint=True, destructiveHint=False, idempotentHint=True
    )
)
@validate_id("chat_id")
async def send_reaction(
    chat_id: Union[int, str],
    message_id: int,
    emoji: str,
    big: bool = False,
) -> str:
    """
    Send a reaction to a message.

    Args:
        chat_id: The chat ID or username
        message_id: The message ID to react to
        emoji: The emoji to react with (e.g., "👍", "❤️", "🔥", "😂", "😮", "😢", "🎉", "💩", "👎")
        big: Whether to show a big animation for the reaction (default: False)
    """
    return await messages.send_reaction(chat_id, message_id, emoji, big)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Remove Reaction", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def remove_reaction(
    chat_id: Union[int, str],
    message_id: int,
) -> str:
    """
    Remove your reaction from a message.

    Args:
        chat_id: The chat ID or username
        message_id: The message ID to remove reaction from
    """
    return await messages.remove_reaction(chat_id, message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Message Reactions", openWorldHint=True, readOnlyHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def get_message_reactions(
    chat_id: Union[int, str],
    message_id: int,
    limit: int = 50,
) -> str:
    """
    Get the list of reactions on a message.

    Args:
        chat_id: The chat ID or username
        message_id: The message ID to get reactions from
        limit: Maximum number of users to return per reaction (default: 50)
    """
    return await messages.get_message_reactions(chat_id, message_id, limit)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Save Draft", openWorldHint=True, destructiveHint=False, idempotentHint=True
    )
)
@validate_id("chat_id")
async def save_draft(
    chat_id: Union[int, str],
    message: str,
    reply_to_msg_id: Optional[int] = None,
    no_webpage: bool = False,
) -> str:
    """
    Save a draft message to a chat or channel. The draft will appear in the Telegram
    app's input field when you open that chat, allowing you to review and send it manually.

    Args:
        chat_id: The chat ID or username/channel to save the draft to
        message: The draft message text
        reply_to_msg_id: Optional message ID to reply to
        no_webpage: If True, disable link preview in the draft
    """
    return await messages.save_draft(chat_id, message, reply_to_msg_id, no_webpage)


@mcp.tool(annotations=ToolAnnotations(title="Get Drafts", openWorldHint=True, readOnlyHint=True))
async def get_drafts() -> str:
    """
    Get all draft messages across all chats.
    Returns a list of drafts with their chat info and message content.
    """
    return await messages.get_drafts()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Clear Draft", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def clear_draft(chat_id: Union[int, str]) -> str:
    """
    Clear/delete a draft from a specific chat.

    Args:
        chat_id: The chat ID or username to clear the draft from
    """
    return await messages.clear_draft(chat_id)


@mcp.tool(annotations=ToolAnnotations(title="List Folders", openWorldHint=True, readOnlyHint=True))
async def list_folders() -> str:
    """
    Get all dialog folders (filters) with their IDs, names, and emoji.
    Returns a list of folders that can be used with other folder tools.
    """
    return await folders.list_folders()


@mcp.tool(annotations=ToolAnnotations(title="Get Folder", openWorldHint=True, readOnlyHint=True))
async def get_folder(folder_id: int) -> str:
    """
    Get detailed information about a specific folder including all included chats.

    Args:
        folder_id: The folder ID (get from list_folders)
    """
    return await folders.get_folder(folder_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Folder", openWorldHint=True, destructiveHint=True, idempotentHint=False
    )
)
async def create_folder(payload: CreateFolderPayload) -> str:
    """
    Create a new dialog folder.
    """
    return await folders.create_folder(
        payload.title,
        payload.emoticon,
        payload.chat_ids,
        payload.include_contacts,
        payload.include_non_contacts,
        payload.include_groups,
        payload.include_broadcasts,
        payload.include_bots,
        payload.exclude_muted,
        payload.exclude_read,
        payload.exclude_archived,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add Chat to Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@validate_id("chat_id")
async def add_chat_to_folder(
    folder_id: int, chat_id: Union[int, str], pinned: bool = False
) -> str:
    """
    Add a chat to an existing folder.

    Args:
        folder_id: The folder ID (get from list_folders)
        chat_id: Chat ID or username to add
        pinned: Pin the chat in this folder (default False)
    """
    return await folders.add_chat_to_folder(folder_id, chat_id, pinned)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Remove Chat from Folder",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@validate_id("chat_id")
async def remove_chat_from_folder(folder_id: int, chat_id: Union[int, str]) -> str:
    """
    Remove a chat from a folder.

    Args:
        folder_id: The folder ID (get from list_folders)
        chat_id: Chat ID or username to remove
    """
    return await folders.remove_chat_from_folder(folder_id, chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def delete_folder(folder_id: int) -> str:
    """
    Delete a folder. Chats in the folder are preserved, only the folder is removed.

    Args:
        folder_id: The folder ID to delete (get from list_folders)
    """
    return await folders.delete_folder(folder_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Reorder Folders", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
async def reorder_folders(folder_ids: List[int]) -> str:
    """
    Change the order of folders in the folder list.

    Args:
        folder_ids: List of folder IDs in the desired order
    """
    return await folders.reorder_folders(folder_ids)


async def _main() -> None:
    from telegram_mcp.client import telegram_client_lifespan

    try:
        import sys
        import sqlite3

        async with telegram_client_lifespan():
            print("Telegram client started. Running MCP server...")
            await mcp.run_stdio_async()
    except Exception as e:
        print(f"Error starting client: {e}", file=sys.stderr)
        if isinstance(e, sqlite3.OperationalError) and "database is locked" in str(e):
            print(
                "Database lock detected. Please ensure no other instances are running.",
                file=sys.stderr,
            )
        sys.exit(1)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


def main() -> None:
    import sys
    import nest_asyncio
    import asyncio
    from .security import _configure_allowed_roots_from_cli

    _configure_allowed_roots_from_cli(sys.argv[1:])
    nest_asyncio.apply()
    asyncio.run(_main())
