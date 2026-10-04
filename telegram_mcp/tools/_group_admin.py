"""Private groups implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Union


async def promote_admin(
    group_id: Union[int, str],
    user_id: Union[int, str],
    rights: dict = None,
    account: str = None,
    *,
    ChatAdminRights,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        chat = await resolve_entity(group_id, cl)
        user = await resolve_entity(user_id, cl)

        # Set default admin rights if not provided
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
                "manage_topics": True,
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
            manage_topics=rights.get("manage_topics", True),
            other=rights.get("other", True),
        )

        try:
            result = await cl(
                functions.channels.EditAdminRequest(
                    channel=chat, user_id=user, admin_rights=admin_rights, rank="Admin"
                )
            )
            return f"Successfully promoted user {user_id} to admin in {sanitize_name(chat.title)}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot promote users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("promote_admin", e, group_id=group_id, user_id=user_id)

    except Exception as e:
        return log_and_format_error("promote_admin", e, group_id=group_id, user_id=user_id)


async def demote_admin(
    group_id: Union[int, str],
    user_id: Union[int, str],
    account: str = None,
    *,
    ChatAdminRights,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        chat = await resolve_entity(group_id, cl)
        user = await resolve_entity(user_id, cl)

        # Create empty admin rights (regular user)
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
            manage_topics=False,
            other=False,
        )

        try:
            result = await cl(
                functions.channels.EditAdminRequest(
                    channel=chat, user_id=user, admin_rights=admin_rights, rank=""
                )
            )
            return f"Successfully demoted user {user_id} from admin in {sanitize_name(chat.title)}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot modify admin status of users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("demote_admin", e, group_id=group_id, user_id=user_id)

    except Exception as e:
        return log_and_format_error("demote_admin", e, group_id=group_id, user_id=user_id)


async def ban_user(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    account: str = None,
    *,
    ChatBannedRights,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        chat = await resolve_entity(chat_id, cl)
        user = await resolve_entity(user_id, cl)

        # Create banned rights (all restrictions enabled)
        banned_rights = ChatBannedRights(
            until_date=None,  # Ban forever
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
            await cl(
                functions.channels.EditBannedRequest(
                    channel=chat, participant=user, banned_rights=banned_rights
                )
            )
            return f"User {user_id} banned from chat {sanitize_name(chat.title)} (ID: {chat_id})."
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot ban users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)


async def unban_user(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    account: str = None,
    *,
    ChatBannedRights,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        chat = await resolve_entity(chat_id, cl)
        user = await resolve_entity(user_id, cl)

        # Create unbanned rights (no restrictions)
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
            await cl(
                functions.channels.EditBannedRequest(
                    channel=chat, participant=user, banned_rights=unbanned_rights
                )
            )
            return (
                f"User {user_id} unbanned from chat {sanitize_name(chat.title)} (ID: {chat_id})."
            )
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot modify status of users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)


async def _eject_and_clear(
    cl,
    chat,
    user,
    *,
    ChatBannedRights,
    _BanNotCleared,
    _REMOVE_USER_UNBAN_DELAY,
    asyncio,
    functions,
    logger,
):
    await cl(
        functions.channels.EditBannedRequest(
            channel=chat,
            participant=user,
            banned_rights=ChatBannedRights(until_date=None, view_messages=True),
        )
    )
    await asyncio.sleep(_REMOVE_USER_UNBAN_DELAY)
    try:
        await cl(
            functions.channels.EditBannedRequest(
                channel=chat, participant=user, banned_rights=ChatBannedRights(until_date=None)
            )
        )
    except Exception as error:
        logger.warning("remove_user: member ejected but the ban could not be cleared")
        raise _BanNotCleared() from error


def _ban_not_cleared_message(error: Exception, *, _is_flood_wait) -> str:
    text = (
        "Error: The user was ejected, but clearing the ban afterwards failed, so they are "
        "currently BANNED from this chat. Call unban_user to lift the ban"
    )
    if _is_flood_wait(error):
        seconds = getattr(error, "seconds", None) or 0
        return (
            f"{text} after waiting {seconds} seconds (Telegram rate limit; "
            "do NOT retry before then)."
        )
    return f"{text}."


async def remove_user(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    account: str = None,
    *,
    Channel,
    Chat,
    _BanNotCleared,
    _ban_not_cleared_message,
    _eject_and_clear,
    asyncio,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
    types,
) -> str:
    try:
        cl = get_client(account)
        chat = await resolve_entity(chat_id, cl)
        user = await resolve_entity(user_id, cl)

        if getattr(user, "is_self", False):
            return "Error: remove_user cannot target the current account. Use leave_chat instead."

        try:
            if isinstance(chat, Channel):
                # channels.editBanned happily "removes" a non-member (that is how a
                # pre-emptive ban works), so check membership first rather than
                # report success for a no-op, or quietly unban a kicked user.
                found = await cl(
                    functions.channels.GetParticipantRequest(channel=chat, participant=user)
                )
                participant = found.participant
                if isinstance(participant, types.ChannelParticipantLeft):
                    return "Error: The user is not a member of this chat."
                if isinstance(participant, types.ChannelParticipantBanned) and participant.left:
                    return "Error: The user is already banned from this chat. Use unban_user to let them back in."
                await asyncio.shield(_eject_and_clear(cl, chat, user))
            elif isinstance(chat, Chat):
                await cl(functions.messages.DeleteChatUserRequest(chat_id=chat.id, user_id=user))
            else:
                return "Error: chat_id must be a group or channel, not a user."
            return (
                f"User {user_id} removed from chat {sanitize_name(chat.title)} "
                f"(ID: {chat_id}). No ban left in place."
            )
        except _BanNotCleared as e:
            return log_and_format_error(
                "remove_user",
                e.__cause__,
                user_message=_ban_not_cleared_message(e.__cause__),
                chat_id=chat_id,
                user_id=user_id,
            )
        except telethon.errors.rpcerrorlist.UserNotParticipantError:
            return "Error: The user is not a member of this chat."
        except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
            return "Error: admin rights required to remove members from this chat."
        except telethon.errors.rpcerrorlist.UserAdminInvalidError:
            return "Error: Cannot remove this user - they are an admin. Demote them first (demote_admin)."
        except Exception as e:
            return log_and_format_error("remove_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        return log_and_format_error("remove_user", e, chat_id=chat_id, user_id=user_id)


async def edit_admin_rights(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    rank: str = "",
    change_info: bool = False,
    post_messages: bool = False,
    edit_messages: bool = False,
    delete_messages: bool = False,
    ban_users: bool = False,
    invite_users: bool = False,
    pin_messages: bool = False,
    add_admins: bool = False,
    anonymous: bool = False,
    manage_call: bool = False,
    manage_topics: bool = False,
    other: bool = False,
    account: str = None,
    *,
    ChatAdminRights,
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
        user = await resolve_entity(user_id, cl)
        admin_rights = ChatAdminRights(
            change_info=change_info,
            post_messages=post_messages,
            edit_messages=edit_messages,
            delete_messages=delete_messages,
            ban_users=ban_users,
            invite_users=invite_users,
            pin_messages=pin_messages,
            add_admins=add_admins,
            anonymous=anonymous,
            manage_call=manage_call,
            manage_topics=manage_topics,
            other=other,
        )
        await cl(
            functions.channels.EditAdminRequest(
                channel=entity, user_id=user, admin_rights=admin_rights, rank=rank
            )
        )
        return f"Admin rights updated for user {user_id} in chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: you need admin rights (with 'add_admins') to modify admin rights."
    except telethon.errors.rpcerrorlist.UserAdminInvalidError:
        return "Error: cannot modify admin rights for this user (you may need to have promoted them originally)."
    except telethon.errors.rpcerrorlist.RightForbiddenError:
        return "Error: some of the requested rights are not allowed for your account or for this chat."
    except Exception as e:
        return log_and_format_error("edit_admin_rights", e, chat_id=chat_id, user_id=user_id)


async def get_admins(
    chat_id: Union[int, str],
    account: str = None,
    *,
    ChannelParticipantsAdmins,
    ensure_connected,
    format_tool_result,
    get_client,
    log_and_format_error,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Fix: Use the correct filter type ChannelParticipantsAdmins
        participants = await cl.get_participants(chat_id, filter=ChannelParticipantsAdmins())
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
        return format_tool_result(records) if records else "No admins found."
    except Exception as e:
        return log_and_format_error("get_admins", e, chat_id=chat_id)


def _format_admin_rights(admin_rights, *, ChatAdminRights) -> dict:
    right_names = [key for key in ChatAdminRights().to_dict() if key != "_"]
    return {name: bool(getattr(admin_rights, name, False)) for name in right_names}


def _participant_role(participant, *, types) -> str:
    if isinstance(participant, types.ChannelParticipantCreator):
        return "creator"
    if isinstance(participant, types.ChannelParticipantAdmin):
        return "admin"
    if participant is None or isinstance(participant, types.ChannelParticipantLeft):
        return "not-participant"
    if isinstance(participant, types.ChannelParticipantBanned):
        if getattr(participant.banned_rights, "view_messages", False):
            return "banned"
        return "not-participant" if participant.left else "restricted"
    return "member"


async def get_member_admin_status(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    account: str = None,
    *,
    Channel,
    _format_admin_rights,
    _participant_role,
    ensure_connected,
    format_tool_result,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
    telethon,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        chat = await resolve_entity(chat_id, cl)
        if not isinstance(chat, Channel):
            return (
                "Error: get_member_admin_status supports only supergroups and channels. "
                "Basic groups do not have per-admin rights."
            )
        user = await resolve_entity(user_id, cl)

        try:
            result = await cl(
                functions.channels.GetParticipantRequest(channel=chat, participant=user)
            )
            participant = result.participant
        except telethon.errors.rpcerrorlist.UserNotParticipantError:
            participant = None

        rank = getattr(participant, "rank", None)
        record = {
            "chat_id": chat_id,
            "user_id": user_id,
            "role": _participant_role(participant),
            "rank": sanitize_name(rank) if rank else None,
            "admin_rights": _format_admin_rights(getattr(participant, "admin_rights", None)),
        }
        return format_tool_result([record])
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: you need admin rights in this chat to inspect its members."
    except Exception as e:
        return log_and_format_error("get_member_admin_status", e, chat_id=chat_id, user_id=user_id)


async def get_banned_users(
    chat_id: Union[int, str],
    account: str = None,
    *,
    ChannelParticipantsKicked,
    ensure_connected,
    format_tool_result,
    get_client,
    log_and_format_error,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Fix: Use the correct filter type ChannelParticipantsKicked
        participants = await cl.get_participants(chat_id, filter=ChannelParticipantsKicked(q=""))
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
        return format_tool_result(records) if records else "No banned users found."
    except Exception as e:
        return log_and_format_error("get_banned_users", e, chat_id=chat_id)


async def get_recent_actions(
    chat_id: Union[int, str],
    account: str = None,
    *,
    ensure_connected,
    functions,
    get_client,
    json,
    json_serializer,
    log_and_format_error,
    sanitize_dict,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(
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

        # Sanitize all string values in the raw API response to prevent
        # prompt injection via user-controlled fields (names, messages, titles).
        return json.dumps(
            sanitize_dict([e.to_dict() for e in result.events]),
            indent=2,
            default=json_serializer,
        )
    except Exception as e:
        return log_and_format_error("get_recent_actions", e, chat_id=chat_id)
