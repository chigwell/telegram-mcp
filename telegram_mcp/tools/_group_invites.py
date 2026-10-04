"""Private groups implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Union


async def get_invite_link(
    chat_id: Union[int, str],
    account: str = None,
    *,
    Channel,
    Chat,
    get_client,
    log_and_format_error,
    logger,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Try using ExportChatInviteRequest first
        try:
            from telethon.tl import functions

            result = await cl(functions.messages.ExportChatInviteRequest(peer=entity))
            return result.link
        except AttributeError:
            # If the function doesn't exist in the current Telethon version
            logger.warning("ExportChatInviteRequest not available, using alternative method")
        except Exception:
            # If that fails, log and try alternative approach
            logger.warning("ExportChatInviteRequest failed; trying alternative method")

        # Alternative approach using cl.export_chat_invite_link
        try:
            invite_link = await cl.export_chat_invite_link(entity)
            return invite_link
        except Exception:
            logger.warning("export_chat_invite_link failed; trying final method")

        # Last resort: Try directly fetching chat info
        try:
            if isinstance(entity, (Chat, Channel)):
                full_chat = await cl(functions.messages.GetFullChatRequest(chat_id=entity.id))
                if hasattr(full_chat, "full_chat") and hasattr(full_chat.full_chat, "invite_link"):
                    return full_chat.full_chat.invite_link or "No invite link available."
        except Exception:
            logger.warning("GetFullChatRequest failed")

        return "Could not retrieve invite link for this chat."
    except Exception as e:
        return log_and_format_error("get_invite_link", e, chat_id=chat_id)


async def join_chat_by_link(
    link: str,
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Extract the hash from the invite link
        if "/" in link:
            hash_part = link.split("/")[-1]
            if hash_part.startswith("+"):
                hash_part = hash_part[1:]  # Remove the '+' if present
        else:
            hash_part = link

        # Try checking the invite before joining
        try:
            # Try to check invite info first (will often fail if not a member)
            invite_info = await cl(functions.messages.CheckChatInviteRequest(hash=hash_part))
            if hasattr(invite_info, "chat") and invite_info.chat:
                # If we got chat info, we're already a member
                chat_title = sanitize_name(getattr(invite_info.chat, "title", "Unknown Chat"))
                return f"You are already a member of this chat: {chat_title}"
        except Exception:
            # This often fails if not a member - just continue
            pass

        # Join the chat using the hash
        result = await cl(functions.messages.ImportChatInviteRequest(hash=hash_part))
        if result and hasattr(result, "chats") and result.chats:
            chat_title = sanitize_name(getattr(result.chats[0], "title", "Unknown Chat"))
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
        return log_and_format_error("join_chat_by_link", e, link=link)


async def export_chat_invite(
    chat_id: Union[int, str],
    account: str = None,
    *,
    get_client,
    log_and_format_error,
    logger,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Try using ExportChatInviteRequest first
        try:
            from telethon.tl import functions

            result = await cl(functions.messages.ExportChatInviteRequest(peer=entity))
            return result.link
        except AttributeError:
            # If the function doesn't exist in the current Telethon version
            logger.warning("ExportChatInviteRequest not available, using alternative method")
        except Exception:
            # If that fails, log and try alternative approach
            logger.warning("ExportChatInviteRequest failed; trying alternative method")

        # Alternative approach using cl.export_chat_invite_link
        try:
            invite_link = await cl.export_chat_invite_link(entity)
            return invite_link
        except Exception as e2:
            logger.warning("export_chat_invite_link failed; trying final method")
            return log_and_format_error("export_chat_invite", e2, chat_id=chat_id)

    except Exception as e:
        return log_and_format_error("export_chat_invite", e, chat_id=chat_id)


async def import_chat_invite(
    hash: str,
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Remove any prefixes like '+' if present
        if hash.startswith("+"):
            hash = hash[1:]

        # Try checking the invite before joining
        try:
            from telethon.errors import (
                InviteHashExpiredError,
                InviteHashInvalidError,
                UserAlreadyParticipantError,
                ChatAdminRequiredError,
                UsersTooMuchError,
            )

            # Try to check invite info first (will often fail if not a member)
            invite_info = await cl(functions.messages.CheckChatInviteRequest(hash=hash))
            if hasattr(invite_info, "chat") and invite_info.chat:
                # If we got chat info, we're already a member
                chat_title = sanitize_name(getattr(invite_info.chat, "title", "Unknown Chat"))
                return f"You are already a member of this chat: {chat_title}"
        except Exception as check_err:
            # This often fails if not a member - just continue
            pass

        # Join the chat using the hash
        try:
            result = await cl(functions.messages.ImportChatInviteRequest(hash=hash))
            if result and hasattr(result, "chats") and result.chats:
                chat_title = sanitize_name(getattr(result.chats[0], "title", "Unknown Chat"))
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
                raise  # Re-raise to be caught by the outer exception handler

    except Exception as e:
        return log_and_format_error("import_chat_invite", e, hash=hash)
