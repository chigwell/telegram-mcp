"""Private messages implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union


async def list_inline_buttons(
    chat_id: Union[int, str],
    message_id: Optional[Union[int, str]] = None,
    limit: int = 20,
    account: str = None,
    *,
    ensure_connected,
    format_tool_result,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_user_content,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        if isinstance(message_id, str):
            if message_id.isdigit():
                message_id = int(message_id)
            else:
                return "message_id must be an integer."

        entity = await resolve_entity(chat_id, cl)

        def _has_inline(msg):
            if getattr(msg, "buttons", None):
                return True
            rm = getattr(msg, "reply_markup", None)
            return bool(rm and hasattr(rm, "rows"))

        def _flat_buttons(msg):
            btns = getattr(msg, "buttons", None)
            if btns:
                return [btn for row in btns for btn in row]
            rm = getattr(msg, "reply_markup", None)
            if rm and hasattr(rm, "rows"):
                return [btn for row in rm.rows for btn in row.buttons]
            return []

        target_message = None

        if message_id is not None:
            target_message = await cl.get_messages(entity, ids=message_id)
            if isinstance(target_message, list):
                target_message = target_message[0] if target_message else None
        else:
            recent_messages = await cl.get_messages(entity, limit=limit)
            target_message = next((msg for msg in recent_messages if _has_inline(msg)), None)

        if not target_message:
            return "No message with inline buttons found."

        buttons = _flat_buttons(target_message)
        if not buttons:
            return f"Message {target_message.id} does not contain inline buttons."

        records = []
        for idx, btn in enumerate(buttons):
            text = getattr(btn, "text", "") or "<no text>"
            url = getattr(btn, "url", None)
            has_callback = bool(getattr(btn, "data", None))
            record = {
                "index": idx,
                "text": sanitize_user_content(text, max_length=256),
                "has_callback": has_callback,
            }
            if url:
                record["url"] = url
            records.append(record)

        return format_tool_result(
            records,
            metadata={
                "message_id": target_message.id,
                "date": target_message.date,
            },
        )
    except Exception as e:
        return log_and_format_error(
            "list_inline_buttons",
            e,
            chat_id=chat_id,
            message_id=message_id,
            limit=limit,
        )


async def press_inline_button(
    chat_id: Union[int, str],
    message_id: Optional[Union[int, str]] = None,
    button_text: Optional[str] = None,
    button_index: Optional[int] = None,
    account: str = None,
    *,
    ensure_connected,
    format_tool_result,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_user_content,
) -> str:
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        if button_text is None and button_index is None:
            return "Provide button_text or button_index to choose a button."

        # Normalize message_id if provided as a string
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

        entity = await resolve_entity(chat_id, cl)

        def _has_inline_buttons(msg):
            """Check if a message has inline buttons via buttons property or reply_markup."""
            if getattr(msg, "buttons", None):
                return True
            rm = getattr(msg, "reply_markup", None)
            return bool(rm and hasattr(rm, "rows"))

        def _extract_buttons(msg):
            """Extract flat list of buttons from buttons property or reply_markup fallback."""
            btns = getattr(msg, "buttons", None)
            if btns:
                return [btn for row in btns for btn in row]
            rm = getattr(msg, "reply_markup", None)
            if rm and hasattr(rm, "rows"):
                return [btn for row in rm.rows for btn in row.buttons]
            return []

        target_message = None
        if message_id is not None:
            # Fetch by ID first, then fall back to recent-message search if
            # reply_markup is missing (Telethon sometimes omits it for ID fetches).
            target_message = await cl.get_messages(entity, ids=message_id)
            if isinstance(target_message, list):
                target_message = target_message[0] if target_message else None
            if target_message and not _has_inline_buttons(target_message):
                # Fallback: search recent messages for the same ID with markup
                recent = await cl.get_messages(entity, limit=30)
                fallback = next(
                    (m for m in recent if m.id == target_message.id and _has_inline_buttons(m)),
                    None,
                )
                if fallback:
                    target_message = fallback
        else:
            recent_messages = await cl.get_messages(entity, limit=20)
            target_message = next(
                (msg for msg in recent_messages if _has_inline_buttons(msg)), None
            )

        if not target_message:
            return "No message with inline buttons found. Specify message_id to target a specific message."

        buttons = _extract_buttons(target_message)
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
                f"[{idx}] {sanitize_user_content(getattr(btn, 'text', '') or '<no text>', max_length=64)}"
                for idx, btn in enumerate(buttons)
            )
            return f"Button not found. Available buttons: {available}"

        btn_data = getattr(target_button, "data", None)
        if not btn_data:
            url = getattr(target_button, "url", None)
            if url:
                return f"Selected button opens a URL instead of sending a callback: {url}"
            return "Selected button does not provide callback data to press."

        callback_result = await cl(
            functions.messages.GetBotCallbackAnswerRequest(
                peer=entity, msg_id=target_message.id, data=btn_data
            )
        )

        response_parts = []
        if getattr(callback_result, "message", None):
            response_parts.append(sanitize_user_content(callback_result.message, max_length=1024))
        if getattr(callback_result, "alert", None):
            response_parts.append("Telegram displayed an alert to the user.")
        if not response_parts:
            response_parts.append("Button pressed successfully.")

        return format_tool_result([], metadata={"response": " ".join(response_parts)})
    except Exception as e:
        return log_and_format_error(
            "press_inline_button",
            e,
            chat_id=chat_id,
            message_id=message_id,
            button_text=button_text,
            button_index=button_index,
        )


async def create_poll(
    chat_id: Union[int, str],
    question: str,
    options: Union[List[str], List[Dict[str, Any]], str],
    multiple_choice: bool = False,
    quiz_mode: bool = False,
    public_votes: bool = True,
    close_date: Optional[str] = None,
    account: Optional[str] = None,
    *,
    ChatAccessDeniedError,
    ErrorCategory,
    List,
    check_chat_access,
    datetime,
    ensure_connected,
    functions,
    get_client,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    json,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        # Validate question
        if not question or not str(question).strip():
            return "Error: Poll question cannot be empty."
        question_text = str(question).strip()
        if len(question_text) > 300:
            return "Error: Poll question cannot exceed 300 characters."

        # Parse and normalize options
        if isinstance(options, str):
            options_str = options.strip()
            if options_str.startswith("[") and options_str.endswith("]"):
                try:
                    parsed = json.loads(options_str)
                    if isinstance(parsed, list):
                        options = parsed
                except Exception:
                    pass
            if isinstance(options, str):
                sep = "\n" if "\n" in options_str else ","
                options = [opt.strip() for opt in options_str.split(sep) if opt.strip()]

        if not isinstance(options, (list, tuple)):
            return "Error: Poll options must be a list of strings."

        raw_options = options
        normalized_options: List[str] = []
        for opt in raw_options:
            if isinstance(opt, dict):
                # Try common keys used by LLMs: "option", "text", "value", "title", "label"
                val = None
                for key in ("option", "text", "value", "title", "label"):
                    if key in opt and opt[key] is not None:
                        val = str(opt[key]).strip()
                        break
                if val is None:
                    # Pick the first non-empty value in the dict
                    for v in opt.values():
                        if v is not None and str(v).strip():
                            val = str(v).strip()
                            break
                opt_str = val if val is not None else ""
            else:
                opt_str = str(opt).strip()

            if not opt_str:
                return "Error: Poll options cannot be empty."
            if len(opt_str) > 100:
                return "Error: Each poll option cannot exceed 100 characters."
            normalized_options.append(opt_str)

        if len(normalized_options) < 2:
            return "Error: Poll must have at least 2 options."
        if len(normalized_options) > 10:
            return "Error: Poll can have at most 10 options."

        if len(set(normalized_options)) != len(normalized_options):
            return "Error: Poll options must be unique."

        # Parse close date if provided
        close_date_obj = None
        if close_date:
            try:
                close_date_obj = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            except ValueError:
                return "Invalid close_date format. Use YYYY-MM-DD HH:MM:SS format."

        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)

        if is_chat_allowlist_enabled() and not is_chat_allowed(chat_id, entity):
            err = check_chat_access(chat_id, entity)
            return log_and_format_error(
                "create_poll",
                ChatAccessDeniedError(err),
                prefix=ErrorCategory.PRIVACY,
                user_message=err,
                chat_id=chat_id,
            )

        # Create the poll using InputMediaPoll with SendMediaRequest
        from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities
        import random

        poll = Poll(
            id=random.randint(0, 2**63 - 1),
            question=TextWithEntities(text=question_text, entities=[]),
            answers=[
                PollAnswer(text=TextWithEntities(text=option, entities=[]), option=bytes([i]))
                for i, option in enumerate(normalized_options)
            ],
            # Telethon 1.44 made `hash` a required argument on Poll. It caches
            # server-side results, so a poll being created sends 0.
            hash=0,
            multiple_choice=multiple_choice,
            quiz=quiz_mode,
            public_voters=public_votes,
            close_date=close_date_obj,
        )

        await cl(
            functions.messages.SendMediaRequest(
                peer=entity,
                media=InputMediaPoll(poll=poll),
                message="",
                random_id=random.randint(0, 2**63 - 1),
            )
        )

        return f"Poll created successfully in chat {chat_id}."
    except Exception as e:
        return log_and_format_error(
            "create_poll", e, chat_id=chat_id, question=question, options=options
        )


async def send_reaction(
    chat_id: Union[int, str],
    message_id: int,
    emoji: str,
    big: bool = False,
    account: str = None,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_input_entity,
) -> str:
    try:
        cl = get_client(account)
        from telethon.tl.types import ReactionCustomEmoji, ReactionEmoji

        if emoji.startswith("custom:"):
            document_id = emoji.removeprefix("custom:")
            if not document_id.isascii() or not document_id.isdigit() or int(document_id) <= 0:
                return "Invalid custom reaction. Use custom:<positive document ID>."
            reaction = ReactionCustomEmoji(document_id=int(document_id))
        else:
            reaction = ReactionEmoji(emoticon=emoji)

        peer = await resolve_input_entity(chat_id, cl)
        await cl(
            functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                big=big,
                reaction=[reaction],
            )
        )
        return f"Reaction '{emoji}' sent to message {message_id} in chat {chat_id}."
    except Exception as e:
        return log_and_format_error(
            "send_reaction", e, chat_id=chat_id, message_id=message_id, emoji=emoji
        )


async def remove_reaction(
    chat_id: Union[int, str],
    message_id: int,
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
        await cl(
            functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                reaction=[],  # Empty list removes reaction
            )
        )
        return f"Reaction removed from message {message_id} in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("remove_reaction", e, chat_id=chat_id, message_id=message_id)


async def get_message_reactions(
    chat_id: Union[int, str],
    message_id: int,
    limit: int = 50,
    account: str = None,
    *,
    functions,
    get_client,
    json,
    json_serializer,
    log_and_format_error,
    resolve_input_entity,
) -> str:
    try:
        cl = get_client(account)
        from telethon.tl.types import ReactionEmoji, ReactionCustomEmoji

        peer = await resolve_input_entity(chat_id, cl)
        message = await cl.get_messages(peer, ids=message_id)
        if message is None:
            return f"Message {message_id} not found in chat {chat_id}."

        if not getattr(getattr(message, "reactions", None), "results", None):
            return json.dumps(
                {"message_id": message_id, "chat_id": str(chat_id), "reactions": [], "count": 0},
                indent=2,
            )

        result = await cl(
            functions.messages.GetMessageReactionsListRequest(
                peer=peer,
                id=message_id,
                limit=limit,
            )
        )

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
        return log_and_format_error(
            "get_message_reactions", e, chat_id=chat_id, message_id=message_id
        )
