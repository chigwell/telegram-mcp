"""Private messages implementations; bindings are supplied by public adapters."""

from __future__ import annotations

from typing import List, Union


async def export_unread_messages(
    chat_ids: List[Union[int, str]],
    output_path: str,
    resume: bool = True,
    include_media_metadata: bool = True,
    account: str = None,
    *,
    Path,
    check_chat_access,
    get_client,
    get_marked_id,
    get_media_label,
    is_chat_allowed,
    is_chat_allowlist_enabled,
    json,
    json_serializer,
    log_and_format_error,
    message_to_dict,
    resolve_entity,
) -> str:
    try:
        cl = get_client(account)

        # Resolve output path and load prior state for resume support
        out = Path(output_path).expanduser()
        prior: dict = {}
        if resume and out.exists():
            try:
                with open(out, "r", encoding="utf-8") as fh:
                    prior = json.load(fh)
            except (json.JSONDecodeError, OSError):
                prior = {}

        result: dict = dict(prior)
        result.setdefault("chats", {})

        stats = {"chats_processed": 0, "chats_skipped": 0, "messages_exported": 0}

        for raw_id in chat_ids:
            # Allowlist check
            if is_chat_allowlist_enabled():
                entity_check = await resolve_entity(raw_id, cl)
                if not is_chat_allowed(raw_id, entity_check):
                    err = check_chat_access(raw_id, entity_check)
                    result["chats"][str(raw_id)] = {"error": err}
                    continue

            entity = await resolve_entity(raw_id, cl)
            numeric_id = str(get_marked_id(entity))

            # Resume: skip already-exported chats
            if resume and numeric_id in result["chats"]:
                stats["chats_skipped"] += 1
                continue

            # Fetch dialog state to get unread_count for this chat
            try:
                unread_count = 0
                for dlg in await cl.get_dialogs(limit=500):
                    if get_marked_id(dlg.entity) == int(numeric_id):
                        unread_count = getattr(dlg, "unread_count", 0) or 0
                        break
            except Exception:
                unread_count = 0

            # Retrieve all unread messages in pages of 100
            exported_msgs: list = []
            collected = 0
            offset_id = 0  # 0 means newest first; we paginate backwards

            while True:
                batch = await cl.get_messages(
                    entity,
                    limit=min(100, max(unread_count - collected, 1) if unread_count else 100),
                    add_offset=collected,
                )
                if not batch:
                    break

                for msg in batch:
                    record = message_to_dict(msg, int(numeric_id))
                    if include_media_metadata:
                        label = get_media_label(msg)
                        if label:
                            record.setdefault("media", label)
                    exported_msgs.append(record)

                collected += len(batch)

                # Stop when we've covered the unread range (or hit the end)
                if len(batch) < 100 or (unread_count and collected >= unread_count):
                    break

            result["chats"][numeric_id] = {
                "chat_id": int(numeric_id),
                "unread_count_at_export": unread_count,
                "messages_exported": len(exported_msgs),
                "messages": exported_msgs,
            }
            stats["chats_processed"] += 1
            stats["messages_exported"] += len(exported_msgs)

        # Persist to output file
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=json_serializer, ensure_ascii=False)

        return json.dumps(
            {
                "status": "ok",
                "output_path": str(out.resolve()),
                "chats_processed": stats["chats_processed"],
                "chats_skipped": stats["chats_skipped"],
                "messages_exported": stats["messages_exported"],
            },
            indent=2,
        )
    except Exception as e:
        return log_and_format_error(
            "export_unread_messages",
            e,
            chat_ids=chat_ids,
            output_path=output_path,
        )
