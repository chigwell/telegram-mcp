"""Message management tools for telegram-mcp."""

import json
from typing import Union, Optional
from datetime import datetime, timedelta

from telethon import functions
from telethon.tl.types import User, Chat, Channel

from ..app import mcp, client
from ..exceptions import log_and_format_error
from ..validators import validate_id
from ..formatters import get_sender_name, get_engagement_info, json_serializer
from ..logging_config import logger
from mcp.types import ToolAnnotations


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
    try:
        entity = await client.get_entity(chat_id)
        offset = (page - 1) * page_size
        messages = await client.get_messages(entity, limit=page_size, add_offset=offset)
        if not messages:
            return "No messages found for this page."
        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"

            engagement_info = get_engagement_info(msg)

            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info}{engagement_info} | Message: {msg.message}"
            )
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "get_messages", e, chat_id=chat_id, page=page, page_size=page_size
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Message", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def send_message(chat_id: Union[int, str], message: str) -> str:
    """
    Send a message to a specific chat.
    Args:
        chat_id: The ID or username of the chat.
        message: The message content to send.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.send_message(entity, message)
        return "Message sent successfully."
    except Exception as e:
        return log_and_format_error("send_message", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="List Messages", openWorldHint=True, readOnlyHint=True))
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
    try:
        entity = await client.get_entity(chat_id)

        from_date_obj = None
        to_date_obj = None

        if from_date:
            try:
                from_date_obj = datetime.strptime(from_date, "%Y-%m-%d")
                try:
                    from_date_obj = from_date_obj.replace(tzinfo=datetime.timezone.utc)
                except AttributeError:
                    from datetime import timezone
                    from_date_obj = from_date_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                return f"Invalid from_date format. Use YYYY-MM-DD."

        if to_date:
            try:
                to_date_obj = datetime.strptime(to_date, "%Y-%m-%d")
                to_date_obj = to_date_obj + timedelta(days=1, microseconds=-1)
                try:
                    to_date_obj = to_date_obj.replace(tzinfo=datetime.timezone.utc)
                except AttributeError:
                    from datetime import timezone
                    to_date_obj = to_date_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                return f"Invalid to_date format. Use YYYY-MM-DD."

        params = {}
        if search_query:
            params["search"] = search_query
            messages = []
            async for msg in client.iter_messages(entity, **params):
                if to_date_obj and msg.date > to_date_obj:
                    continue
                if from_date_obj and msg.date < from_date_obj:
                    break
                messages.append(msg)
                if len(messages) >= limit:
                    break
        else:
            if from_date_obj or to_date_obj:
                messages = []
                if from_date_obj:
                    async for msg in client.iter_messages(
                        entity, offset_date=from_date_obj, reverse=True
                    ):
                        if to_date_obj and msg.date > to_date_obj:
                            break
                        if msg.date < from_date_obj:
                            continue
                        messages.append(msg)
                        if len(messages) >= limit:
                            break
                else:
                    async for msg in client.iter_messages(
                        entity,
                        offset_date=to_date_obj + timedelta(microseconds=1),
                    ):
                        messages.append(msg)
                        if len(messages) >= limit:
                            break
            else:
                messages = await client.get_messages(entity, limit=limit, **params)

        if not messages:
            return "No messages found matching the criteria."

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            message_text = msg.message or "[Media/No text]"
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"

            engagement_info = get_engagement_info(msg)

            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info}{engagement_info} | Message: {message_text}"
            )

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("list_messages", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Message Context", openWorldHint=True, readOnlyHint=True))
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
    try:
        chat = await client.get_entity(chat_id)
        messages_before = await client.get_messages(chat, limit=context_size, max_id=message_id)
        central_message = await client.get_messages(chat, ids=message_id)
        if central_message is not None and not isinstance(central_message, list):
            central_message = [central_message]
        elif central_message is None:
            central_message = []
        messages_after = await client.get_messages(
            chat, limit=context_size, min_id=message_id, reverse=True
        )
        if not central_message:
            return f"Message with ID {message_id} not found in chat {chat_id}."
        all_messages = list(messages_before) + list(central_message) + list(messages_after)
        all_messages.sort(key=lambda m: m.id)
        results = [f"Context for message {message_id} in chat {chat_id}:"]
        for msg in all_messages:
            sender_name = get_sender_name(msg)
            highlight = " [THIS MESSAGE]" if msg.id == message_id else ""

            reply_content = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                try:
                    replied_msg = await client.get_messages(chat, ids=msg.reply_to.reply_to_msg_id)
                    if replied_msg:
                        replied_sender = "Unknown"
                        if replied_msg.sender:
                            replied_sender = getattr(
                                replied_msg.sender, "first_name", ""
                            ) or getattr(replied_msg.sender, "title", "Unknown")
                        reply_content = f" | reply to {msg.reply_to.reply_to_msg_id}\n  → Replied message: [{replied_sender}] {replied_msg.message or '[Media/No text]'}"
                except Exception:
                    reply_content = (
                        f" | reply to {msg.reply_to.reply_to_msg_id} (original message not found)"
                    )

            results.append(
                f"ID: {msg.id} | {sender_name} | {msg.date}{highlight}{reply_content}\n{msg.message or '[Media/No text]'}\n"
            )
        return "\n".join(results)
    except Exception as e:
        return log_and_format_error(
            "get_message_context",
            e,
            chat_id=chat_id,
            message_id=message_id,
            context_size=context_size,
        )


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
    try:
        from_entity = await client.get_entity(from_chat_id)
        to_entity = await client.get_entity(to_chat_id)
        await client.forward_messages(to_entity, message_id, from_entity)
        return f"Message {message_id} forwarded from {from_chat_id} to {to_chat_id}."
    except Exception as e:
        return log_and_format_error(
            "forward_message",
            e,
            from_chat_id=from_chat_id,
            message_id=message_id,
            to_chat_id=to_chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Edit Message", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def edit_message(chat_id: Union[int, str], message_id: int, new_text: str) -> str:
    """
    Edit a message you sent.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.edit_message(entity, message_id, new_text)
        return f"Message {message_id} edited."
    except Exception as e:
        return log_and_format_error(
            "edit_message", e, chat_id=chat_id, message_id=message_id, new_text=new_text
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Delete Message", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def delete_message(chat_id: Union[int, str], message_id: int) -> str:
    """
    Delete a message by ID.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.delete_messages(entity, message_id)
        return f"Message {message_id} deleted."
    except Exception as e:
        return log_and_format_error("delete_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Pin Message", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def pin_message(chat_id: Union[int, str], message_id: int) -> str:
    """
    Pin a message in a chat.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.pin_message(entity, message_id)
        return f"Message {message_id} pinned in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("pin_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Unpin Message", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def unpin_message(chat_id: Union[int, str], message_id: int) -> str:
    """
    Unpin a message in a chat.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.unpin_message(entity, message_id)
        return f"Message {message_id} unpinned in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("unpin_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Mark As Read", openWorldHint=True, destructiveHint=True, idempotentHint=True)
)
@validate_id("chat_id")
async def mark_as_read(chat_id: Union[int, str]) -> str:
    """
    Mark all messages as read in a chat.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.send_read_acknowledge(entity)
        return f"Marked all messages as read in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("mark_as_read", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Reply To Message", openWorldHint=True, destructiveHint=True)
)
@validate_id("chat_id")
async def reply_to_message(chat_id: Union[int, str], message_id: int, text: str) -> str:
    """
    Reply to a specific message in a chat.
    """
    try:
        entity = await client.get_entity(chat_id)
        await client.send_message(entity, text, reply_to=message_id)
        return f"Replied to message {message_id} in chat {chat_id}."
    except Exception as e:
        return log_and_format_error(
            "reply_to_message", e, chat_id=chat_id, message_id=message_id, text=text
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Search Messages", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def search_messages(chat_id: Union[int, str], query: str, limit: int = 20) -> str:
    """
    Search for messages in a chat by text.
    """
    try:
        entity = await client.get_entity(chat_id)
        messages = await client.get_messages(entity, limit=limit, search=query)

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"
            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info} | Message: {msg.message}"
            )
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "search_messages", e, chat_id=chat_id, query=query, limit=limit
        )


@mcp.tool(annotations=ToolAnnotations(title="Get History", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def get_history(chat_id: Union[int, str], limit: int = 100) -> str:
    """
    Get full chat history (up to limit).
    """
    try:
        entity = await client.get_entity(chat_id)
        messages = await client.get_messages(entity, limit=limit)

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"
            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info} | Message: {msg.message}"
            )
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_history", e, chat_id=chat_id, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Pinned Messages", openWorldHint=True, readOnlyHint=True)
)
@validate_id("chat_id")
async def get_pinned_messages(chat_id: Union[int, str]) -> str:
    """
    Get all pinned messages in a chat.
    """
    try:
        entity = await client.get_entity(chat_id)

        try:
            from telethon.tl.types import InputMessagesFilterPinned
            messages = await client.get_messages(entity, filter=InputMessagesFilterPinned())
        except (ImportError, AttributeError):
            all_messages = await client.get_messages(entity, limit=50)
            messages = [m for m in all_messages if getattr(m, "pinned", False)]

        if not messages:
            return "No pinned messages found in this chat."

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"
            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info} | Message: {msg.message or '[Media/No text]'}"
            )

        return "\n".join(lines)
    except Exception as e:
        logger.exception(f"get_pinned_messages failed (chat_id={chat_id})")
        return log_and_format_error("get_pinned_messages", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="List Inline Buttons", openWorldHint=True, readOnlyHint=True))
@validate_id("chat_id")
async def list_inline_buttons(
    chat_id: Union[int, str], message_id: Optional[Union[int, str]] = None, limit: int = 20
) -> str:
    """
    Inspect inline buttons on a recent message to discover their indices/text/URLs.
    """
    try:
        if isinstance(message_id, str):
            if message_id.isdigit():
                message_id = int(message_id)
            else:
                return "message_id must be an integer."

        entity = await client.get_entity(chat_id)
        target_message = None

        if message_id is not None:
            target_message = await client.get_messages(entity, ids=message_id)
            if isinstance(target_message, list):
                target_message = target_message[0] if target_message else None
        else:
            recent_messages = await client.get_messages(entity, limit=limit)
            target_message = next(
                (msg for msg in recent_messages if getattr(msg, "buttons", None)), None
            )

        if not target_message:
            return "No message with inline buttons found."

        buttons_attr = getattr(target_message, "buttons", None)
        if not buttons_attr:
            return f"Message {target_message.id} does not contain inline buttons."

        buttons = [btn for row in buttons_attr for btn in row]
        if not buttons:
            return f"Message {target_message.id} does not contain inline buttons."

        lines = [
            f"Buttons for message {target_message.id} (date {target_message.date}):",
        ]
        for idx, btn in enumerate(buttons):
            raw_button = getattr(btn, "button", None)
            text = getattr(btn, "text", "") or "<no text>"
            url = getattr(raw_button, "url", None) if raw_button else None
            has_callback = bool(getattr(btn, "data", None))
            parts = [f"[{idx}] text='{text}'"]
            parts.append("callback=yes" if has_callback else "callback=no")
            if url:
                parts.append(f"url={url}")
            lines.append(", ".join(parts))

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "list_inline_buttons",
            e,
            chat_id=chat_id,
            message_id=message_id,
            limit=limit,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Press Inline Button", openWorldHint=True, destructiveHint=True)
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
    try:
        if button_text is None and button_index is None:
            return "Provide button_text or button_index to choose a button."

        if isinstance(message_id, str):
            if message_id.isdigit():
                message_id = int(message_id)
            else:
                return "message_id must be an integer."

        if isinstance(button_index, str):
            if button_index.isdigit():
                button_index = int(button_index)
            else:
                return "button_index must be an integer."

        entity = await client.get_entity(chat_id)

        target_message = None
        if message_id is not None:
            target_message = await client.get_messages(entity, ids=message_id)
            if isinstance(target_message, list):
                target_message = target_message[0] if target_message else None
        else:
            recent_messages = await client.get_messages(entity, limit=20)
            target_message = next(
                (msg for msg in recent_messages if getattr(msg, "buttons", None)), None
            )

        if not target_message:
            return "No message with inline buttons found. Specify message_id to target a specific message."

        buttons_attr = getattr(target_message, "buttons", None)
        if not buttons_attr:
            return f"Message {target_message.id} does not contain inline buttons."

        buttons = [btn for row in buttons_attr for btn in row]
        if not buttons:
            return f"Message {target_message.id} does not contain inline buttons."

        target_button = None
        if button_text:
            normalized = button_text.strip().lower()
            target_button = next(
                (
                    btn
                    for btn in buttons
                    if (getattr(btn, "text", "") or "").strip().lower() == normalized
                ),
                None,
            )

        if target_button is None and button_index is not None:
            if button_index < 0 or button_index >= len(buttons):
                return f"button_index out of range. Valid indices: 0-{len(buttons) - 1}."
            target_button = buttons[button_index]

        if not target_button:
            available = ", ".join(
                f"[{idx}] {getattr(btn, 'text', '') or '<no text>'}"
                for idx, btn in enumerate(buttons)
            )
            return f"Button not found. Available buttons: {available}"

        if not getattr(target_button, "data", None):
            raw_button = getattr(target_button, "button", None)
            url = getattr(raw_button, "url", None) if raw_button else None
            if url:
                return f"Selected button opens a URL instead of sending a callback: {url}"
            return "Selected button does not provide callback data to press."

        callback_result = await client(
            functions.messages.GetBotCallbackAnswerRequest(
                peer=entity, msg_id=target_message.id, data=target_button.data
            )
        )

        response_parts = []
        if getattr(callback_result, "message", None):
            response_parts.append(callback_result.message)
        if getattr(callback_result, "alert", None):
            response_parts.append("Telegram displayed an alert to the user.")
        if not response_parts:
            response_parts.append("Button pressed successfully.")

        return " ".join(response_parts)
    except Exception as e:
        return log_and_format_error(
            "press_inline_button",
            e,
            chat_id=chat_id,
            message_id=message_id,
            button_text=button_text,
            button_index=button_index,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Reaction", openWorldHint=True, destructiveHint=False, idempotentHint=True)
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
    try:
        from telethon.tl.types import ReactionEmoji

        peer = await client.get_input_entity(chat_id)
        await client(
            functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                big=big,
                reaction=[ReactionEmoji(emoticon=emoji)],
            )
        )
        return f"Reaction '{emoji}' sent to message {message_id} in chat {chat_id}."
    except Exception as e:
        logger.exception(
            f"send_reaction failed (chat_id={chat_id}, message_id={message_id}, emoji={emoji})"
        )
        return log_and_format_error(
            "send_reaction", e, chat_id=chat_id, message_id=message_id, emoji=emoji
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Remove Reaction", openWorldHint=True, destructiveHint=True, idempotentHint=True)
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
    try:
        peer = await client.get_input_entity(chat_id)
        await client(
            functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                reaction=[],
            )
        )
        return f"Reaction removed from message {message_id} in chat {chat_id}."
    except Exception as e:
        logger.exception(f"remove_reaction failed (chat_id={chat_id}, message_id={message_id})")
        return log_and_format_error("remove_reaction", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Message Reactions", openWorldHint=True, readOnlyHint=True, idempotentHint=True)
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
    try:
        from telethon.tl.types import ReactionEmoji, ReactionCustomEmoji

        peer = await client.get_input_entity(chat_id)

        result = await client(
            functions.messages.GetMessageReactionsListRequest(
                peer=peer,
                id=message_id,
                limit=limit,
            )
        )

        if not result.reactions:
            return f"No reactions on message {message_id} in chat {chat_id}."

        reactions_data = []
        for reaction in result.reactions:
            user_id = reaction.peer_id.user_id if hasattr(reaction.peer_id, "user_id") else None
            emoji = None
            if isinstance(reaction.reaction, ReactionEmoji):
                emoji = reaction.reaction.emoticon
            elif isinstance(reaction.reaction, ReactionCustomEmoji):
                emoji = f"custom:{reaction.reaction.document_id}"

            reactions_data.append(
                {
                    "user_id": user_id,
                    "emoji": emoji,
                    "date": reaction.date.isoformat() if reaction.date else None,
                }
            )

        return json.dumps(
            {
                "message_id": message_id,
                "chat_id": str(chat_id),
                "reactions": reactions_data,
                "count": len(reactions_data),
            },
            indent=2,
            default=json_serializer,
        )
    except Exception as e:
        logger.exception(
            f"get_message_reactions failed (chat_id={chat_id}, message_id={message_id})"
        )
        return log_and_format_error(
            "get_message_reactions", e, chat_id=chat_id, message_id=message_id
        )
