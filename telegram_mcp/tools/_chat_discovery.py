"""Private chats implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Union


async def get_chats(
    account: str = None,
    page: int = 1,
    page_size: int = 20,
    *,
    BotMethodInvalidError,
    ensure_connected,
    format_tool_result,
    get_client,
    get_marked_id,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    log_and_format_error,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        try:
            dialogs = await cl.get_dialogs()
        except BotMethodInvalidError:
            return "Listing chats/dialogs is not supported for bot accounts (Telegram API restriction: bots cannot fetch dialog lists)."
        if is_chat_allowlist_enabled():
            dialogs = [d for d in dialogs if is_chat_allowed(get_marked_id(d.entity), d.entity)]
        start = (page - 1) * page_size
        end = start + page_size
        if start >= len(dialogs):
            return "Page out of range."
        chats = dialogs[start:end]
        records = []
        for dialog in chats:
            entity = dialog.entity
            title = getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
            records.append(
                {
                    "chat_id": get_marked_id(entity),
                    "title": sanitize_name(title),
                }
            )
        return format_tool_result(records)
    except Exception as e:
        return log_and_format_error("get_chats", e)


async def list_chats(
    chat_type: str = None,
    limit: int = 20,
    unread_only: bool = False,
    unmuted_only: bool = False,
    archived: bool = None,
    with_about: bool = False,
    account: str = None,
    *,
    BotMethodInvalidError,
    Channel,
    Chat,
    User,
    datetime,
    ensure_connected,
    format_tool_result,
    functions,
    get_client,
    get_entity_filter_type,
    get_entity_type,
    get_marked_id,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    log_and_format_error,
    logger,
    sanitize_name,
    sanitize_user_content,
    time,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        try:
            dialogs = await cl.get_dialogs(limit=limit, archived=archived)
        except BotMethodInvalidError:
            return "Listing chats is not supported for bot accounts (Telegram API restriction: bots cannot fetch dialog lists)."

        records = []
        for dialog in dialogs:
            entity = dialog.entity

            # Enforce privacy allowlist
            if is_chat_allowlist_enabled() and not is_chat_allowed(get_marked_id(entity), entity):
                continue

            # Filter by type if requested
            current_type = get_entity_filter_type(entity)

            if chat_type and current_type != chat_type.lower():
                continue

            # Post-filter by archive status (Telethon may include pinned dialogs from other folders)
            if archived is not None and bool(getattr(dialog, "archived", False)) != archived:
                continue

            # Build chat record
            record = {"chat_id": get_marked_id(entity)}

            if hasattr(entity, "title"):
                record["title"] = sanitize_name(entity.title)
            elif hasattr(entity, "first_name"):
                name = f"{entity.first_name}"
                if hasattr(entity, "last_name") and entity.last_name:
                    name += f" {entity.last_name}"
                record["name"] = sanitize_name(name)

            record["type"] = get_entity_type(entity)

            if hasattr(entity, "username") and entity.username:
                record["username"] = entity.username

            # Add unread count if available
            unread_count = getattr(dialog, "unread_count", 0) or 0
            # Also check unread_mark (manual "mark as unread" flag)
            inner_dialog = getattr(dialog, "dialog", None)
            unread_mark = (
                bool(getattr(inner_dialog, "unread_mark", False)) if inner_dialog else False
            )

            # Extract mute status from notify_settings
            notify_settings = getattr(inner_dialog, "notify_settings", None)
            mute_until = getattr(notify_settings, "mute_until", None)
            if mute_until is None:
                is_muted = False
            elif isinstance(mute_until, datetime):
                is_muted = mute_until.timestamp() > time.time()
            else:
                is_muted = mute_until > time.time()

            # Filter by mute status if requested
            if unmuted_only and is_muted:
                continue

            # Filter by unread status if requested
            if unread_only and unread_count == 0 and not unread_mark:
                continue

            record["unread"] = unread_count
            if unread_mark:
                record["unread_mark"] = True
            record["muted"] = is_muted
            record["archived"] = bool(getattr(dialog, "archived", False))

            # Add unread mentions count if available
            unread_mentions = getattr(dialog, "unread_mentions_count", 0) or 0
            if unread_mentions > 0:
                record["unread_mentions"] = unread_mentions

            # Optionally fetch per-chat description/bio. Each call is guarded
            # so one failure (permissions, flood, etc.) doesn't abort the whole
            # listing.
            if with_about:
                about_text = ""
                try:
                    if isinstance(entity, Channel):
                        full = await cl(functions.channels.GetFullChannelRequest(channel=entity))
                        about_text = getattr(full.full_chat, "about", "") or ""
                    elif isinstance(entity, Chat):
                        full = await cl(functions.messages.GetFullChatRequest(chat_id=entity.id))
                        about_text = getattr(full.full_chat, "about", "") or ""
                    elif isinstance(entity, User):
                        full = await cl(functions.users.GetFullUserRequest(id=entity))
                        about_text = getattr(full.full_user, "about", "") or ""
                except Exception:
                    logger.warning("list_chats: failed to fetch one chat description")
                    about_text = "<error fetching description>"

                record["about"] = sanitize_user_content(about_text, max_length=200)

            records.append(record)

        if not records:
            return "No chats found matching the criteria."

        return format_tool_result(records)
    except Exception as e:
        return log_and_format_error(
            "list_chats",
            e,
            chat_type=chat_type,
            limit=limit,
            unread_only=unread_only,
            unmuted_only=unmuted_only,
            archived=archived,
            with_about=with_about,
            account=account,
        )


async def get_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    ChatAccessDeniedError,
    ErrorCategory,
    User,
    check_chat_access,
    format_tool_result,
    functions,
    get_client,
    get_entity_type,
    get_marked_id,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    log_and_format_error,
    logger,
    resolve_entity,
    sanitize_name,
    sanitize_user_content,
    types,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        if is_chat_allowlist_enabled() and not is_chat_allowed(chat_id, entity):
            err = check_chat_access(chat_id, entity)
            return log_and_format_error(
                "get_chat",
                ChatAccessDeniedError(err),
                prefix=ErrorCategory.PRIVACY,
                user_message=err,
                chat_id=chat_id,
            )

        record = {"id": get_marked_id(entity)}

        is_user = isinstance(entity, User)

        if hasattr(entity, "title"):
            record["title"] = sanitize_name(entity.title)
            record["type"] = get_entity_type(entity)
            if hasattr(entity, "username") and entity.username:
                record["username"] = entity.username

            # Fetch participants count reliably
            try:
                participants_count = (await cl.get_participants(entity, limit=0)).total
                record["participants"] = participants_count
            except Exception:
                record["participants"] = None

        elif is_user:
            name = f"{entity.first_name}"
            if entity.last_name:
                name += f" {entity.last_name}"
            record["name"] = sanitize_name(name)
            record["type"] = get_entity_type(entity)
            if entity.username:
                record["username"] = entity.username
            if entity.phone:
                record["phone"] = entity.phone
            record["bot"] = bool(entity.bot)
            record["verified"] = bool(entity.verified)

        # Photo presence — the entity carries ChatPhoto/ChatPhotoEmpty (chats/channels)
        # or UserProfilePhoto/UserProfilePhotoEmpty (users). Surfaced so callers can
        # detect chats that have no avatar set.
        photo = getattr(entity, "photo", None)
        record["has_photo"] = photo is not None and not isinstance(
            photo, (types.ChatPhotoEmpty, types.UserProfilePhotoEmpty)
        )
        if record["has_photo"]:
            record["current_avatar_id"] = getattr(photo, "photo_id", None)

        # Get unread count + last activity for THIS specific peer.
        #
        # NOTE: do NOT use get_dialogs(limit=1, offset_peer=entity) here. In
        # Telethon `offset_peer` is a pagination cursor, not a per-chat filter —
        # with offset_id=0 it is effectively ignored, so limit=1 returns the
        # account's top dialog and its unread/archived/last-message get wrongly
        # attributed to the requested chat. GetPeerDialogsRequest resolves the
        # dialog for exactly the requested peer instead.
        try:
            input_peer = await cl.get_input_entity(entity)
            peer_dialogs = await cl(
                functions.messages.GetPeerDialogsRequest(
                    peers=[types.InputDialogPeer(peer=input_peer)]
                )
            )
            if getattr(peer_dialogs, "dialogs", None):
                dialog = peer_dialogs.dialogs[0]
                record["unread"] = getattr(dialog, "unread_count", 0)
                # folder_id == 1 is the Archive folder (None/0 == main list)
                record["archived"] = getattr(dialog, "folder_id", 0) == 1

            last_messages = await cl.get_messages(entity, limit=1)
            if last_messages:
                last_msg = last_messages[0]
                sender_name = "Unknown"
                sender = getattr(last_msg, "sender", None)
                if sender:
                    sender_name = getattr(sender, "first_name", "") or getattr(
                        sender, "title", "Unknown"
                    )
                    if getattr(sender, "last_name", None):
                        sender_name += f" {sender.last_name}"
                sender_name = sanitize_name(sender_name.strip() or "Unknown")
                record["last_message"] = {
                    "sender": sender_name,
                    "date": last_msg.date,
                    "text": sanitize_user_content(last_msg.message),
                }
        except Exception:
            logger.warning("Could not get requested dialog metadata")

        return format_tool_result([], metadata=record)
    except Exception as e:
        return log_and_format_error("get_chat", e, chat_id=chat_id)


async def search_public_chats(
    query: str,
    limit: int = 20,
    account: str = None,
    *,
    ensure_connected,
    format_entity,
    functions,
    get_client,
    get_marked_id,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    json,
    log_and_format_error,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.SearchRequest(q=query, limit=limit))
        all_entities = result.chats + result.users
        if is_chat_allowlist_enabled():
            all_entities = [e for e in all_entities if is_chat_allowed(get_marked_id(e), e)]
        entities = [format_entity(e) for e in all_entities]
        return json.dumps(entities, indent=2)
    except Exception as e:
        return log_and_format_error("search_public_chats", e, query=query, limit=limit)


async def resolve_username(
    username: str,
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.ResolveUsernameRequest(username=username))
        return str(result)
    except Exception as e:
        return log_and_format_error("resolve_username", e, username=username)


async def get_full_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    Chat,
    ChatAccessDeniedError,
    ErrorCategory,
    InputPeerChat,
    check_chat_access,
    ensure_connected,
    functions,
    get_client,
    get_marked_id,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    json,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    sanitize_user_content,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)

        if is_chat_allowlist_enabled() and not is_chat_allowed(chat_id, entity):
            err = check_chat_access(chat_id, entity)
            return log_and_format_error(
                "get_full_chat",
                ChatAccessDeniedError(err),
                prefix=ErrorCategory.PRIVACY,
                user_message=err,
                chat_id=chat_id,
            )

        # Basic ("legacy") groups are not channels: GetFullChannelRequest cannot
        # cast an InputPeerChat and raises TypeError. They are served by
        # messages.GetFullChatRequest instead.
        if isinstance(entity, (Chat, InputPeerChat)):
            basic_id = getattr(entity, "chat_id", None) or getattr(entity, "id", None)
            full = await cl(functions.messages.GetFullChatRequest(chat_id=basic_id))
        else:
            full = await cl(functions.channels.GetFullChannelRequest(channel=entity))

        chat = full.chats[0] if full.chats else None
        full_chat = full.full_chat

        # Channels carry participants_count on the full object; basic groups only
        # carry the member list, so count that instead.
        participants_count = getattr(full_chat, "participants_count", None)
        if participants_count is None:
            members = getattr(getattr(full_chat, "participants", None), "participants", None)
            if members is not None:
                participants_count = len(members)

        result = {
            "id": get_marked_id(chat) if chat else None,
            "title": sanitize_name(getattr(chat, "title", None)) if chat else None,
            "username": getattr(chat, "username", None) if chat else None,
            "about": sanitize_user_content(
                getattr(full_chat, "about", None) or "", max_length=1024
            ),
            "participants_count": participants_count,
            "linked_chat_id": getattr(full_chat, "linked_chat_id", None),
        }

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error("get_full_chat", e, chat_id=chat_id)


async def get_common_chats(
    user_id: Union[int, str],
    limit: int = 100,
    max_id: int = 0,
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    get_entity_type,
    get_marked_id,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Telegram caps the limit at 100
        if limit > 100:
            limit = 100
        if limit < 1:
            limit = 1

        user_entity = await resolve_entity(user_id, cl)
        result = await cl(
            functions.messages.GetCommonChatsRequest(
                user_id=user_entity, max_id=max_id, limit=limit
            )
        )

        chats = getattr(result, "chats", []) or []
        if not chats:
            return f"No common chats found with user {user_id}."

        lines = []
        for chat in chats:
            line = f"Chat ID: {get_marked_id(chat)}"
            if hasattr(chat, "title") and chat.title:
                line += f", Title: {sanitize_name(chat.title)}"
            line += f", Type: {get_entity_type(chat)}"
            if hasattr(chat, "username") and chat.username:
                line += f", Username: @{chat.username}"
            lines.append(line)

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "get_common_chats", e, user_id=user_id, limit=limit, max_id=max_id
        )


async def get_message_read_by(
    chat_id: Union[int, str],
    message_id: int,
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    json,
    json_serializer,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        from telethon.errors.rpcerrorlist import (
            ChatAdminRequiredError,
            UserNotParticipantError,
            MsgTooOldError,
            PeerIdInvalidError,
        )

        entity = await resolve_entity(chat_id, cl)
        try:
            result = await cl(
                functions.messages.GetMessageReadParticipantsRequest(
                    peer=entity, msg_id=message_id
                )
            )
        except MsgTooOldError:
            return (
                f"Read receipts unavailable for message {message_id} in chat "
                f"{chat_id}: message is too old or read receipts are disabled."
            )
        except ChatAdminRequiredError:
            return (
                f"Cannot read receipts for message {message_id} in chat {chat_id}: "
                f"admin rights are required."
            )
        except UserNotParticipantError:
            return (
                f"Cannot read receipts for message {message_id} in chat {chat_id}: "
                f"you are not a participant of this chat."
            )
        except PeerIdInvalidError:
            return f"Invalid chat: {chat_id}."

        # result is a list of ReadParticipantDate objects in newer Telethon,
        # or a list of user IDs (ints) in older layers. Handle both.
        if not result:
            return f"No read receipts available for message {message_id} in chat " f"{chat_id}."

        readers = []
        for item in result:
            if hasattr(item, "user_id"):
                readers.append(
                    {
                        "user_id": item.user_id,
                        "read_at": item.date.isoformat() if getattr(item, "date", None) else None,
                    }
                )
            else:
                # Older layer: plain int
                readers.append({"user_id": item, "read_at": None})

        return json.dumps(
            {
                "chat_id": str(chat_id),
                "message_id": message_id,
                "read_by": readers,
                "count": len(readers),
            },
            indent=2,
            default=json_serializer,
        )
    except Exception as e:
        return log_and_format_error(
            "get_message_read_by", e, chat_id=chat_id, message_id=message_id
        )


async def get_message_link(
    chat_id: Union[int, str],
    message_id: int,
    thread: bool = False,
    account: str = None,
    *,
    Channel,
    ensure_connected,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        if not isinstance(entity, Channel):
            return (
                f"Cannot export message link for this entity type "
                f"({type(entity).__name__}). Message links are only available "
                f"for channels and supergroups."
            )

        result = await cl(
            functions.channels.ExportMessageLinkRequest(
                channel=entity, id=message_id, grouped=False, thread=thread
            )
        )

        link = getattr(result, "link", None)
        html = getattr(result, "html", None)
        if not link:
            return f"Could not export link for message {message_id} in chat {chat_id}."

        output = f"Link: {link}"
        if html:
            output += f"\nHTML: {html}"
        return output
    except Exception as e:
        return log_and_format_error(
            "get_message_link",
            e,
            chat_id=chat_id,
            message_id=message_id,
            thread=thread,
        )
