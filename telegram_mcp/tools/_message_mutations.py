"""Private messages implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import List, Optional, Union


async def delete_scheduled_message(
    chat_id: Union[int, str],
    message_ids: List[int],
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
        if not message_ids:
            return "message_ids must be a non-empty list."
        entity = await resolve_entity(chat_id, cl)
        await cl(functions.messages.DeleteScheduledMessagesRequest(peer=entity, id=message_ids))
        return f"Deleted {len(message_ids)} scheduled message(s) from chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError as e:
        return log_and_format_error(
            "delete_scheduled_message", e, chat_id=chat_id, message_ids=message_ids
        )
    except Exception as e:
        return log_and_format_error(
            "delete_scheduled_message", e, chat_id=chat_id, message_ids=message_ids
        )


async def delete_message(
    chat_id: Union[int, str],
    message_id: int,
    account: str = None,
    *,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.delete_messages(entity, message_id)
        return f"Message {message_id} deleted."
    except Exception as e:
        return log_and_format_error("delete_message", e, chat_id=chat_id, message_id=message_id)


async def delete_chat_history(
    chat_id: Union[int, str],
    max_id: int = 0,
    revoke: bool = False,
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
        result = await cl(
            functions.messages.DeleteHistoryRequest(peer=entity, max_id=max_id, revoke=revoke)
        )
        pts_count = getattr(result, "pts_count", 0)
        offset = getattr(result, "offset", 0)
        scope = "for both parties" if revoke else "for you"
        return (
            f"Chat {chat_id} history cleared {scope}: "
            f"{pts_count} messages deleted (offset={offset})."
        )
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Cannot delete chat history: admin privileges are required."
    except Exception as e:
        return log_and_format_error(
            "delete_chat_history",
            e,
            chat_id=chat_id,
            max_id=max_id,
            revoke=revoke,
        )


async def delete_messages_bulk(
    chat_id: Union[int, str],
    message_ids: List[int],
    revoke: bool = True,
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
        if isinstance(entity, Channel):
            result = await cl(
                functions.channels.DeleteMessagesRequest(channel=entity, id=message_ids)
            )
        else:
            result = await cl(
                functions.messages.DeleteMessagesRequest(id=message_ids, revoke=revoke)
            )
        pts_count = getattr(result, "pts_count", 0)
        return f"Deleted {pts_count} of {len(message_ids)} messages from chat {chat_id}."
    except telethon.errors.rpcerrorlist.MessageIdInvalidError:
        return "Cannot delete messages: one or more message IDs are invalid."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Cannot delete messages: admin privileges are required."
    except Exception as e:
        return log_and_format_error(
            "delete_messages_bulk",
            e,
            chat_id=chat_id,
            message_ids=message_ids,
            revoke=revoke,
        )


async def pin_message(
    chat_id: Union[int, str],
    message_id: int,
    account: str = None,
    *,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.pin_message(entity, message_id)
        return f"Message {message_id} pinned in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("pin_message", e, chat_id=chat_id, message_id=message_id)


async def unpin_message(
    chat_id: Union[int, str],
    message_id: int,
    account: str = None,
    *,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.unpin_message(entity, message_id)
        return f"Message {message_id} unpinned in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("unpin_message", e, chat_id=chat_id, message_id=message_id)


async def unpin_all_messages(
    chat_id: Union[int, str],
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
        await cl(functions.messages.UnpinAllMessagesRequest(peer=entity))
        return f"All messages unpinned in chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Cannot unpin messages: admin privileges are required."
    except Exception as e:
        return log_and_format_error("unpin_all_messages", e, chat_id=chat_id)


async def mark_as_read(
    chat_id: Union[int, str],
    account: str = None,
    *,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.send_read_acknowledge(entity)
        return f"Marked all messages as read in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("mark_as_read", e, chat_id=chat_id)


async def save_draft(
    chat_id: Union[int, str],
    message: str,
    reply_to_msg_id: Optional[int] = None,
    no_webpage: bool = False,
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_input_entity,
) -> str:
    try:
        cl = get_client(account)
        peer = await resolve_input_entity(chat_id, cl)

        # Build reply_to parameter if provided
        reply_to = None
        if reply_to_msg_id:
            from telethon.tl.types import InputReplyToMessage

            reply_to = InputReplyToMessage(reply_to_msg_id=reply_to_msg_id)

        await cl(
            functions.messages.SaveDraftRequest(
                peer=peer,
                message=message,
                no_webpage=no_webpage,
                reply_to=reply_to,
            )
        )

        return f"Draft saved to chat {chat_id}. Open the chat in Telegram to see and send it."
    except Exception as e:
        return log_and_format_error("save_draft", e, chat_id=chat_id)


async def clear_draft(
    chat_id: Union[int, str],
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_input_entity,
) -> str:
    try:
        cl = get_client(account)
        peer = await resolve_input_entity(chat_id, cl)

        # Saving an empty message clears the draft
        await cl(
            functions.messages.SaveDraftRequest(
                peer=peer,
                message="",
            )
        )

        return f"Draft cleared from chat {chat_id}."
    except Exception as e:
        return log_and_format_error("clear_draft", e, chat_id=chat_id)
