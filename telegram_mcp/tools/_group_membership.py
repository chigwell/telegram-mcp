"""Private groups implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import List, Union


async def create_group(
    title: str,
    user_ids: List[Union[int, str]],
    account: str = None,
    *,
    BotMethodInvalidError,
    asyncio,
    functions,
    get_client,
    get_marked_id,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        # Convert user IDs to entities
        users = []
        for user_id in user_ids:
            try:
                user = await resolve_entity(user_id, cl)
                users.append(user)
            except Exception:
                return "Error: Could not find a requested user."

        if not users:
            return "Error: No valid users provided"

        # Create the group with the users
        try:
            # Create a new chat with selected users
            result = await cl(functions.messages.CreateChatRequest(users=users, title=title))

            # Check what type of response we got
            if hasattr(result, "chats") and result.chats:
                created_chat = result.chats[0]
                return f"Group created with ID: {get_marked_id(created_chat)}"
            elif hasattr(result, "chat") and result.chat:
                return f"Group created with ID: {get_marked_id(result.chat)}"
            elif hasattr(result, "chat_id"):
                return f"Group created with ID: {result.chat_id}"
            else:
                # If we can't determine the chat ID directly from the result
                # Try to find it in recent dialogs
                await asyncio.sleep(1)  # Give Telegram a moment to register the new group
                try:
                    dialogs = await cl.get_dialogs(limit=5)  # Get recent dialogs
                except BotMethodInvalidError:
                    dialogs = []
                for dialog in dialogs:
                    if dialog.title == title:
                        return f"Group created with ID: {get_marked_id(dialog.entity)}"

                # If we still can't find it, at least return success
                return f"Group created successfully. Please check your recent chats for '{sanitize_name(title)}'."

        except Exception as create_err:
            if "PEER_FLOOD" in str(create_err):
                return "Error: Cannot create group due to Telegram limits. Try again later."
            else:
                raise  # Let the outer exception handler catch it
    except Exception as e:
        return log_and_format_error("create_group", e, title=title, user_ids=user_ids)


async def invite_to_group(
    group_id: Union[int, str],
    user_ids: List[Union[int, str]],
    account: str = None,
    *,
    Channel,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(group_id, cl)
        users_to_add = []

        for user_id in user_ids:
            try:
                user = await resolve_entity(user_id, cl)
                users_to_add.append(user)
            except ValueError:
                return "Error: A requested user could not be found."

        try:
            if isinstance(entity, Channel):
                # Supergroup or broadcast channel
                result = await cl(
                    functions.channels.InviteToChannelRequest(channel=entity, users=users_to_add)
                )

                invited_count = 0
                if hasattr(result, "users") and result.users:
                    invited_count = len(result.users)
                elif hasattr(result, "count"):
                    invited_count = result.count

                return (
                    f"Successfully invited {invited_count} users to {sanitize_name(entity.title)}"
                )
            else:
                # Basic group (telethon Chat): channels.InviteToChannel cannot be used
                # (it casts to InputChannel and fails). Add each user individually via
                # messages.AddChatUser instead.
                invited_count = 0
                already = 0
                failures = []
                for user in users_to_add:
                    try:
                        await cl(
                            functions.messages.AddChatUserRequest(
                                chat_id=entity.id, user_id=user, fwd_limit=100
                            )
                        )
                        invited_count += 1
                    except telethon.errors.rpcerrorlist.UserAlreadyParticipantError:
                        already += 1
                    except (
                        telethon.errors.rpcerrorlist.UserNotMutualContactError,
                        telethon.errors.rpcerrorlist.UserPrivacyRestrictedError,
                    ) as ue:
                        failures.append(f"{getattr(user, 'id', user)}: {type(ue).__name__}")

                msg = (
                    f"Successfully invited {invited_count} users to {sanitize_name(entity.title)}"
                )
                if already:
                    msg += f" ({already} already a participant)"
                if failures:
                    msg += f" (failed: {'; '.join(failures)})"
                return msg
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot invite users who are not mutual contacts. Please ensure the users are in your contacts and have added you back."
        except telethon.errors.rpcerrorlist.UserPrivacyRestrictedError:
            return (
                "Error: One or more users have privacy settings that prevent you from adding them."
            )
        except Exception as e:
            return log_and_format_error("invite_to_group", e, group_id=group_id, user_ids=user_ids)

    except Exception as e:
        return log_and_format_error("invite_to_group", e, group_id=group_id, user_ids=user_ids)


async def leave_chat(
    chat_id: Union[int, str],
    account: str = None,
    *,
    Channel,
    Chat,
    functions,
    get_client,
    log_and_format_error,
    logger,
    resolve_entity,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Check the entity type carefully
        if isinstance(entity, Channel):
            # Handle both channels and supergroups (which are also channels in Telegram)
            try:
                await cl(functions.channels.LeaveChannelRequest(channel=entity))
                chat_name = sanitize_name(getattr(entity, "title", str(chat_id)))
                return f"Left channel/supergroup {chat_name} (ID: {chat_id})."
            except Exception as chan_err:
                return log_and_format_error("leave_chat", chan_err, chat_id=chat_id)

        elif isinstance(entity, Chat):
            # Traditional basic groups (not supergroups)
            try:
                # First try with InputPeerUser
                me = await cl.get_me(input_peer=True)
                await cl(
                    functions.messages.DeleteChatUserRequest(
                        chat_id=entity.id,
                        user_id=me,  # Use the entity ID directly
                    )
                )
                chat_name = sanitize_name(getattr(entity, "title", str(chat_id)))
                return f"Left basic group {chat_name} (ID: {chat_id})."
            except Exception:
                # If the above fails, try the second approach
                logger.warning("First leave attempt failed; trying alternative method")

                try:
                    # Alternative approach - sometimes this works better
                    me_full = await cl.get_me()
                    await cl(
                        functions.messages.DeleteChatUserRequest(
                            chat_id=entity.id, user_id=me_full.id
                        )
                    )
                    chat_name = sanitize_name(getattr(entity, "title", str(chat_id)))
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


async def get_participants(
    chat_id: Union[int, str],
    page: int = 1,
    page_size: int = 200,
    account: str = None,
    *,
    ensure_connected,
    format_tool_result,
    get_client,
    log_and_format_error,
    sanitize_name,
) -> str:
    try:
        # Enforce safety limit per issue #14
        if page_size > 1000:
            return "Error: page_size cannot exceed 1000 participants per request."

        cl = get_client(account)
        await ensure_connected(cl)

        # iter_participants takes no `offset`, and its `limit` is not honoured
        # for basic groups. Fetch through the page, then slice it out.
        offset = (page - 1) * page_size
        participants = []
        async for participant in cl.iter_participants(chat_id, limit=offset + page_size):
            participants.append(participant)
        participants = participants[offset : offset + page_size]

        if not participants:
            return format_tool_result([])

        records = []
        for p in participants:
            rec = {
                "id": p.id,
                "name": sanitize_name(
                    f"{getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
                ),
            }
            uname = getattr(p, "username", None)
            if uname:
                rec["username"] = sanitize_name(uname)
            records.append(rec)
        result = format_tool_result(records)

        # Append pagination metadata; has_more indicates whether a next page likely exists
        has_more = len(participants) == page_size
        result += f"\n\nPage {page} (showing {len(participants)} participants)"
        if has_more:
            result += f" — more results available on page {page + 1}"

        return result
    except Exception as e:
        return log_and_format_error(
            "get_participants", e, chat_id=chat_id, page=page, page_size=page_size
        )


async def create_channel(
    title: str,
    about: str = "",
    megagroup: bool = False,
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
        result = await cl(
            functions.channels.CreateChannelRequest(title=title, about=about, megagroup=megagroup)
        )
        return f"Channel '{sanitize_name(title)}' created with ID: {result.chats[0].id}"
    except Exception as e:
        return log_and_format_error(
            "create_channel", e, title=title, about=about, megagroup=megagroup
        )
