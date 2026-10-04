"""Private messages implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import List, Optional, Union


async def forward_message(
    from_chat_id: Union[int, str],
    message_id: Union[int, List[int]],
    to_chat_id: Union[int, str],
    account: str = None,
    expand_album: bool = True,
    topic_id: Optional[int] = None,
    send_as: Optional[Union[int, str]] = None,
    drop_author: bool = False,
    silent: bool = False,
    *,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    resolve_input_entity,
    types,
) -> str:
    try:
        if topic_id is not None and (type(topic_id) is not int or topic_id <= 0):
            return "Error: topic_id must be a positive integer."
        cl = get_client(account)
        from_entity = await resolve_entity(from_chat_id, cl)
        to_entity = await resolve_entity(to_chat_id, cl)

        ids_to_forward = message_id
        expanded_from_album = False
        if expand_album and isinstance(message_id, int):
            anchor = await cl.get_messages(from_entity, ids=message_id)
            grouped_id = getattr(anchor, "grouped_id", None) if anchor else None
            if grouped_id is not None:
                # Album ids are allocated contiguously by Telegram; a small
                # window around the anchor reliably captures all siblings.
                window = list(range(message_id - 9, message_id + 10))
                neighbors = await cl.get_messages(from_entity, ids=window)
                sibling_ids = sorted(
                    {
                        m.id
                        for m in neighbors
                        if m is not None and getattr(m, "grouped_id", None) == grouped_id
                    }
                )
                if len(sibling_ids) > 1:
                    ids_to_forward = sibling_ids
                    expanded_from_album = True

        destination_note = ""
        if topic_id is not None or send_as is not None or drop_author or silent:
            sender = await resolve_input_entity(send_as, cl) if send_as is not None else None
            request = functions.messages.ForwardMessagesRequest(
                from_peer=from_entity,
                id=ids_to_forward if isinstance(ids_to_forward, list) else [ids_to_forward],
                to_peer=to_entity,
                top_msg_id=topic_id,
                send_as=sender,
                drop_author=drop_author,
                silent=silent,
            )
            result = await cl(request)
            # Correlate only this request's random IDs, in request order; never
            # infer destination IDs from unrelated updates in the response.
            returned_ids = {
                update.random_id: update.id
                for update in getattr(result, "updates", None) or []
                if isinstance(update, types.UpdateMessageID)
            }
            destination_ids = [
                returned_ids[random_id]
                for random_id in request.random_id
                if random_id in returned_ids
            ]
            destination_note = (
                f" Destination message IDs: {destination_ids or 'not returned by Telegram'}."
            )
        else:
            await cl.forward_messages(to_entity, ids_to_forward, from_entity)
        count = len(ids_to_forward) if isinstance(ids_to_forward, list) else 1
        if count == 1:
            summary = f"Message {message_id} forwarded from {from_chat_id} to {to_chat_id}."
        elif expanded_from_album:
            summary = (
                f"Album of {count} messages forwarded from {from_chat_id} "
                f"to {to_chat_id} (auto-expanded from message {message_id})."
            )
        else:
            summary = f"{count} messages forwarded from {from_chat_id} to {to_chat_id}."
        return summary + destination_note
    except Exception as e:
        return log_and_format_error(
            "forward_message",
            e,
            from_chat_id=from_chat_id,
            message_id=message_id,
            to_chat_id=to_chat_id,
        )


async def forward_messages(
    from_chat_id: Union[int, str],
    message_ids: List[int],
    to_chat_id: Union[int, str],
    account: str = None,
    *,
    get_client,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        if not message_ids:
            return "Error: message_ids must contain at least one id."
        cl = get_client(account)
        from_entity = await resolve_entity(from_chat_id, cl)
        to_entity = await resolve_entity(to_chat_id, cl)
        await cl.forward_messages(to_entity, list(message_ids), from_entity)
        return f"{len(message_ids)} messages forwarded from " f"{from_chat_id} to {to_chat_id}."
    except Exception as e:
        return log_and_format_error(
            "forward_messages",
            e,
            from_chat_id=from_chat_id,
            message_ids=message_ids,
            to_chat_id=to_chat_id,
        )
