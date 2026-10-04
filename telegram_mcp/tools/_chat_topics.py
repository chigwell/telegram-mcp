"""Private chats implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import Optional, Union


async def list_topics(
    chat_id: Union[int, str],
    limit: int = 200,
    offset_topic: int = 0,
    search_query: str = None,
    account: str = None,
    *,
    Channel,
    GetForumTopicsRequest,
    format_tool_result,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_user_content,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "The specified chat is not a supergroup."

        if not getattr(entity, "forum", False):
            return "The specified supergroup does not have forum topics enabled."

        result = await cl(
            GetForumTopicsRequest(
                channel=entity,
                offset_date=0,
                offset_id=0,
                offset_topic=offset_topic,
                limit=limit,
                q=search_query or None,
            )
        )

        topics = getattr(result, "topics", None) or []
        if not topics:
            return "No topics found for this chat."

        messages_map = {}
        if getattr(result, "messages", None):
            messages_map = {message.id: message for message in result.messages}

        records = []
        for topic in topics:
            title = getattr(topic, "title", None) or "(no title)"
            record = {
                "id": topic.id,
                "title": sanitize_user_content(title, max_length=256),
            }

            total_messages = getattr(topic, "total_messages", None)
            if total_messages is not None:
                record["total_messages"] = total_messages

            unread_count = getattr(topic, "unread_count", None)
            if unread_count:
                record["unread"] = unread_count

            record["closed"] = bool(getattr(topic, "closed", False))
            record["hidden"] = bool(getattr(topic, "hidden", False))

            top_message_id = getattr(topic, "top_message", None)
            top_message = messages_map.get(top_message_id)
            if top_message and getattr(top_message, "date", None):
                record["last_activity"] = top_message.date.isoformat()

            records.append(record)

        return format_tool_result(records)
    except Exception as e:
        return log_and_format_error(
            "list_topics",
            e,
            chat_id=chat_id,
            limit=limit,
            offset_topic=offset_topic,
            search_query=search_query,
        )


async def enable_forum_topics(
    chat_id: Union[int, str],
    tabs: bool = True,
    account: str = None,
    *,
    Channel,
    functions,
    get_client,
    log_and_format_error,
    resolve_entity,
    sanitize_name,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "The specified chat is not a supergroup."

        if getattr(entity, "forum", False):
            title = sanitize_name(getattr(entity, "title", str(chat_id)))
            return f"Forum topics already enabled for {title}."

        await cl(functions.channels.ToggleForumRequest(channel=entity, enabled=True, tabs=tabs))
        # Keep the resolved entity in sync for callers/tests that reuse it.
        try:
            entity.forum = True
        except Exception:
            pass

        title = sanitize_name(getattr(entity, "title", str(chat_id)))
        return f"Forum topics enabled for {title}."
    except Exception as e:
        return log_and_format_error("enable_forum_topics", e, chat_id=chat_id, tabs=tabs)


async def create_forum_topic(
    chat_id: Union[int, str],
    title: str,
    icon_color: int = None,
    icon_emoji_id: int = None,
    account: str = None,
    *,
    Channel,
    CreateForumTopicRequest,
    _extract_created_topic_id,
    format_tool_result,
    get_client,
    get_marked_id,
    log_and_format_error,
    resolve_entity,
    sanitize_user_content,
    secrets,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "The specified chat is not a supergroup."

        if not getattr(entity, "forum", False):
            return (
                "The specified supergroup does not have forum topics enabled. "
                "Use enable_forum_topics first."
            )

        clean_title = sanitize_user_content(title, max_length=128)
        result = await cl(
            CreateForumTopicRequest(
                peer=entity,
                title=clean_title,
                random_id=secrets.randbits(63),
                icon_color=icon_color,
                icon_emoji_id=icon_emoji_id,
            )
        )

        topic_id = _extract_created_topic_id(result)
        record = {
            "chat_id": get_marked_id(entity),
            "title": clean_title,
        }
        if topic_id is not None:
            record["topic_id"] = topic_id

        return format_tool_result([record])
    except Exception as e:
        return log_and_format_error(
            "create_forum_topic",
            e,
            chat_id=chat_id,
            title=title,
            icon_color=icon_color,
            icon_emoji_id=icon_emoji_id,
        )


def _extract_created_topic_id(result) -> Optional[int]:
    updates = getattr(result, "updates", None) or []
    for update in updates:
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int):
            return message_id

        update_id = getattr(update, "id", None)
        if isinstance(update_id, int):
            return update_id

    message = getattr(result, "message", None)
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int):
        return message_id

    return None


def _forum_supergroup_error(entity, *, Channel) -> Optional[str]:
    if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
        return "The specified chat is not a supergroup."
    if not getattr(entity, "forum", False):
        return (
            "The specified supergroup does not have forum topics enabled. "
            "Use enable_forum_topics first."
        )
    return None


async def edit_forum_topic(
    chat_id: Union[int, str],
    topic_id: int,
    title: str = None,
    icon_emoji_id: int = None,
    closed: bool = None,
    hidden: bool = None,
    account: str = None,
    *,
    _forum_supergroup_error,
    format_tool_result,
    functions,
    get_client,
    get_marked_id,
    log_and_format_error,
    resolve_entity,
    sanitize_user_content,
) -> str:
    changes = {
        "title": title,
        "icon_emoji_id": icon_emoji_id,
        "closed": closed,
        "hidden": hidden,
    }
    changes = {key: value for key, value in changes.items() if value is not None}
    if not changes:
        return "Nothing to change: pass title, icon_emoji_id, closed or hidden."

    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        error = _forum_supergroup_error(entity)
        if error:
            return error

        if "title" in changes:
            changes["title"] = sanitize_user_content(changes["title"], max_length=128)

        await cl(
            functions.messages.EditForumTopicRequest(peer=entity, topic_id=topic_id, **changes)
        )

        record = {"chat_id": get_marked_id(entity), "topic_id": topic_id, **changes}
        return format_tool_result([record])
    except Exception as e:
        return log_and_format_error(
            "edit_forum_topic",
            e,
            chat_id=chat_id,
            topic_id=topic_id,
            title=title,
            icon_emoji_id=icon_emoji_id,
            closed=closed,
            hidden=hidden,
        )


async def delete_forum_topic(
    chat_id: Union[int, str],
    topic_id: int,
    account: str = None,
    *,
    _DELETE_TOPIC_MAX_BATCHES,
    _forum_supergroup_error,
    format_tool_result,
    functions,
    get_client,
    get_marked_id,
    log_and_format_error,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        error = _forum_supergroup_error(entity)
        if error:
            return error

        batches = 0
        while batches < _DELETE_TOPIC_MAX_BATCHES:
            result = await cl(
                functions.messages.DeleteTopicHistoryRequest(peer=entity, top_msg_id=topic_id)
            )
            batches += 1
            if not getattr(result, "offset", 0):
                break
        else:
            return (
                f"Topic {topic_id} is still being deleted after {batches} batches; "
                "call delete_forum_topic again to continue."
            )

        record = {
            "chat_id": get_marked_id(entity),
            "topic_id": topic_id,
            "deleted": True,
            "batches": batches,
        }
        return format_tool_result([record])
    except Exception as e:
        return log_and_format_error("delete_forum_topic", e, chat_id=chat_id, topic_id=topic_id)
