"""Private messages implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Optional, Union


async def _send_rich(
    cl,
    entity,
    text: str,
    parse_mode: str,
    reply_to: Optional[int] = None,
    *,
    account_is_premium,
    functions,
    is_premium_rpc_error,
    json,
    make_rich_input,
    premium_required_result,
    telethon,
    types,
):
    import random

    if not await account_is_premium(cl):
        return premium_required_result("send_message")
    try:
        await cl(
            functions.messages.SendMessageRequest(
                peer=entity,
                message=text,
                random_id=random.randint(0, 2**62),
                reply_to=(
                    types.InputReplyToMessage(reply_to_msg_id=reply_to) if reply_to else None
                ),
                rich_message=make_rich_input(parse_mode, text),
            )
        )
    except telethon.errors.RPCError as e:
        # Premium can lapse between the check above and the send — same refusal.
        if is_premium_rpc_error(e):
            return premium_required_result("send_message")
        raise
    return json.dumps({"sent": True, "rich": True}, ensure_ascii=False)


async def _edit_rich(
    cl,
    entity,
    message_id: int,
    text: str,
    parse_mode: str,
    *,
    account_is_premium,
    functions,
    is_premium_rpc_error,
    json,
    make_rich_input,
    premium_required_result,
    telethon,
):
    if not await account_is_premium(cl):
        return premium_required_result("edit_message")
    try:
        await cl(
            functions.messages.EditMessageRequest(
                peer=entity,
                id=message_id,
                message=text,
                rich_message=make_rich_input(parse_mode, text),
            )
        )
    except telethon.errors.RPCError as e:
        if is_premium_rpc_error(e):
            return premium_required_result("edit_message")
        raise
    return json.dumps(
        {"sent": True, "rich": True, "edited_message_id": message_id}, ensure_ascii=False
    )


def _chip_conflict(parse_mode):
    if parse_mode:
        return "format_date needs plain-text messages (leave parse_mode unset)."
    return None


def _date_entity(message: str, format_date: str, *, datetime, types):
    idx = message.find(format_date)
    if idx < 0:
        return None, f"format_date '{format_date}' was not found in the message text."
    tokens = format_date.split()
    parts = tokens[0].split("/")
    if len(parts) not in (2, 3) or any(not p.isdigit() for p in parts):
        return (
            None,
            f"format_date '{format_date}' must look like 13/09, 13/09/2026 or 13/09 17:00.",
        )
    clock = None
    if len(tokens) == 2:
        clock = tokens[1].split(":")
        if len(clock) != 2 or any(not p.isdigit() for p in clock):
            return (
                None,
                f"format_date '{format_date}' must look like 13/09, 13/09/2026 or 13/09 17:00.",
            )
    if len(tokens) > 2:
        return (
            None,
            f"format_date '{format_date}' must look like 13/09, 13/09/2026 or 13/09 17:00.",
        )
    try:
        day, month = int(parts[0]), int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else datetime.now().year
        hour, minute = (int(clock[0]), int(clock[1])) if clock else (0, 0)
        date = datetime(year, month, day, hour, minute).astimezone()
    except ValueError:
        return None, f"format_date '{format_date}' is not a valid date."
    entity = types.MessageEntityFormattedDate(
        offset=len(message[:idx].encode("utf-16-le")) // 2,
        length=len(format_date.encode("utf-16-le")) // 2,
        date=date,
        short_date=len(parts) == 2,
        long_date=len(parts) == 3,
        short_time=clock is not None,
    )
    return entity, None


async def send_message(
    chat_id: Union[int, str],
    message: str,
    parse_mode: Optional[str] = None,
    format_date: Optional[str] = None,
    account: str = None,
    *,
    ChatAccessDeniedError,
    ErrorCategory,
    RICH_PARSE_MODES,
    _chip_conflict,
    _date_entity,
    _send_rich,
    check_chat_access,
    functions,
    get_client,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        if is_chat_allowlist_enabled() and not is_chat_allowed(chat_id, entity):
            err = check_chat_access(chat_id, entity)
            return log_and_format_error(
                "send_message",
                ChatAccessDeniedError(err),
                prefix=ErrorCategory.PRIVACY,
                user_message=err,
                chat_id=chat_id,
            )

        if parse_mode and parse_mode.lower() in RICH_PARSE_MODES:
            conflict = _chip_conflict(format_date)
            if conflict:
                return conflict
            return await _send_rich(cl, entity, message, parse_mode.lower())
        if format_date:
            conflict = _chip_conflict(parse_mode)
            if conflict:
                return conflict
            chip, chip_error = _date_entity(message, format_date)
            if chip_error:
                return chip_error
            import random

            await cl(
                functions.messages.SendMessageRequest(
                    peer=entity,
                    message=message,
                    random_id=random.randint(0, 2**62),
                    entities=[chip],
                )
            )
            return "Message sent successfully."
        await cl.send_message(entity, message, parse_mode=parse_mode)
        return "Message sent successfully."
    except Exception as e:
        return log_and_format_error("send_message", e, chat_id=chat_id)


async def send_scheduled_message(
    chat_id: Union[int, str],
    message: str,
    schedule_date: Union[str, int],
    parse_mode: Optional[str] = None,
    account: str = None,
    *,
    RICH_PARSE_MODES,
    ensure_connected,
    get_client,
    log_and_format_error,
    parse_schedule_date,
    resolve_entity,
    telethon,
) -> str:
    try:
        if parse_mode and parse_mode.lower() in RICH_PARSE_MODES:
            return (
                f"parse_mode='{parse_mode}' is not supported for scheduled messages. "
                "Use 'md', 'html' or 'plain'."
            )
        cl = get_client(account)
        await ensure_connected(cl)
        dt, schedule_error = parse_schedule_date(schedule_date)
        if schedule_error:
            return schedule_error

        entity = await resolve_entity(chat_id, cl)
        kwargs = {"schedule": dt}
        if parse_mode is not None:
            # Omitted parse_mode keeps Telethon's client default (Markdown) for
            # backward compatibility; 'plain' maps to None, which disables parsing.
            kwargs["parse_mode"] = None if parse_mode.lower() == "plain" else parse_mode
        result = await cl.send_message(entity, message, **kwargs)
        message_id = getattr(result, "id", None)
        return f"Scheduled message {message_id} for {dt.isoformat()} in chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )
    except telethon.errors.rpcerrorlist.ScheduleDateTooLateError as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )
    except telethon.errors.rpcerrorlist.ScheduleDateInvalidError as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )
    except Exception as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )


async def edit_message(
    chat_id: Union[int, str],
    message_id: int,
    new_text: str,
    parse_mode: Optional[str] = None,
    format_date: Optional[str] = None,
    account: str = None,
    *,
    RICH_PARSE_MODES,
    _chip_conflict,
    _date_entity,
    _edit_rich,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        if parse_mode and parse_mode.lower() in RICH_PARSE_MODES:
            conflict = _chip_conflict(format_date)
            if conflict:
                return conflict
            return await _edit_rich(cl, entity, message_id, new_text, parse_mode.lower())
        if format_date:
            conflict = _chip_conflict(parse_mode)
            if conflict:
                return conflict
            chip, chip_error = _date_entity(new_text, format_date)
            if chip_error:
                return chip_error
            await cl(
                functions.messages.EditMessageRequest(
                    peer=entity,
                    id=message_id,
                    message=new_text,
                    entities=[chip],
                )
            )
            return f"Message {message_id} edited."
        # Only pass parse_mode when the caller set it: Telethon treats an explicit
        # None as "disable parsing", while omitting the argument uses its default
        # parser. Passing None unconditionally would turn previously formatted
        # edits into literal text.
        extra = {"parse_mode": parse_mode} if parse_mode is not None else {}
        await cl.edit_message(entity, message_id, new_text, **extra)
        return f"Message {message_id} edited."
    except Exception as e:
        return log_and_format_error(
            "edit_message", e, chat_id=chat_id, message_id=message_id, new_text=new_text
        )


async def reply_to_message(
    chat_id: Union[int, str],
    message_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    format_date: Optional[str] = None,
    account: str = None,
    *,
    RICH_PARSE_MODES,
    _chip_conflict,
    _date_entity,
    _send_rich,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    types,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        if parse_mode and parse_mode.lower() in RICH_PARSE_MODES:
            conflict = _chip_conflict(format_date)
            if conflict:
                return conflict
            return await _send_rich(cl, entity, text, parse_mode.lower(), reply_to=message_id)
        if format_date:
            conflict = _chip_conflict(parse_mode)
            if conflict:
                return conflict
            chip, chip_error = _date_entity(text, format_date)
            if chip_error:
                return chip_error
            import random

            await cl(
                functions.messages.SendMessageRequest(
                    peer=entity,
                    message=text,
                    random_id=random.randint(0, 2**62),
                    reply_to=types.InputReplyToMessage(reply_to_msg_id=message_id),
                    entities=[chip],
                )
            )
            return f"Replied to message {message_id} in chat {chat_id}."
        await cl.send_message(entity, text, reply_to=message_id, parse_mode=parse_mode)
        return f"Replied to message {message_id} in chat {chat_id}."
    except Exception as e:
        return log_and_format_error(
            "reply_to_message", e, chat_id=chat_id, message_id=message_id, text=text
        )
