"""Folders MCP tools."""

import copy

from telegram_mcp.runtime import *


class _FolderValidationError(ValidationError):
    """Validation messages authored here contain no caller or provider payload."""


_FOLDER_FLAGS = (
    "contacts",
    "non_contacts",
    "groups",
    "broadcasts",
    "bots",
    "exclude_muted",
    "exclude_read",
    "exclude_archived",
    "title_noanimate",
)
_PEER_FIELDS = {
    "include_chat_ids": "include_peers",
    "pinned_chat_ids": "pinned_peers",
    "exclude_chat_ids": "exclude_peers",
}
_TITLE_LIMIT_UTF16 = 12  # Telegram's official client limit, not an app-config key.


def _positive_config_number(value):
    if not isinstance(value, types.JsonNumber):
        return None
    number = value.value
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return None
    try:
        integer = int(number)
        return integer if integer > 0 and integer == number else None
    except (ValueError, OverflowError):
        return None


async def _read_folder_limits(cl):
    premium = None
    try:
        me = await cl.get_me()
        if me is not None and hasattr(me, "premium"):
            premium = bool(me.premium)
    except Exception:
        pass
    values = {}
    available = False
    try:
        result = await cl(functions.help.GetAppConfigRequest(hash=0))
        if isinstance(result, types.help.AppConfig) and isinstance(
            result.config, types.JsonObject
        ):
            available = True
            values = {
                entry.key: _positive_config_number(entry.value) for entry in result.config.value
            }
    except Exception:
        pass
    keys = {
        "folders": "dialog_filters_limit",
        "chats_per_folder": "dialog_filters_chats_limit",
        "pinned_per_folder": "dialogs_folder_pinned_limit",
    }
    tier = None if premium is None else "premium" if premium else "default"
    return {
        "premium": premium,
        "config_available": available,
        "limits": {
            name: values.get(f"{key}_{tier}") if tier else None for name, key in keys.items()
        },
        "config_keys": {name: f"{key}_{tier}" if tier else None for name, key in keys.items()},
        "title_limit_utf16": _TITLE_LIMIT_UTF16,
        "unknown_limits": "Unknown limits defer to Telegram's server validation.",
    }


async def _configured_folder_limit(cl) -> Optional[tuple[int, bool]]:
    settings = await _read_folder_limits(cl)
    limit = settings["limits"]["folders"]
    return (limit, settings["premium"]) if limit is not None else None


def _stable_peer_id(peer, self_id=None):
    if isinstance(peer, types.InputPeerSelf):
        if self_id is None:
            raise _FolderValidationError(
                "Cannot resolve Saved Messages identity for a complete snapshot."
            )
        return self_id
    return utils.get_peer_id(peer)


async def _snapshot_self_id(cl, filters):
    if any(
        isinstance(peer, types.InputPeerSelf)
        for f in filters
        for field in _PEER_FIELDS.values()
        for peer in getattr(f, field, [])
    ):
        me = await cl.get_me()
        if me is None:
            raise _FolderValidationError(
                "Account identity is unavailable; snapshot would be incomplete."
            )
        return me.id
    return None


def _folder_state(f, self_id=None):
    if isinstance(f, DialogFilterDefault):
        return {"id": 0, "type": "system", "editable": False}
    shared = isinstance(f, DialogFilterChatlist)
    definition = {
        "title": f.title.text if isinstance(f.title, TextWithEntities) else f.title,
        "title_entities": [entity.to_dict() for entity in getattr(f.title, "entities", [])],
        "emoticon": getattr(f, "emoticon", None),
        "color": getattr(f, "color", None),
        "title_noanimate": getattr(f, "title_noanimate", None),
    }
    for name, field in _PEER_FIELDS.items():
        if hasattr(f, field):
            definition[name] = [_stable_peer_id(peer, self_id) for peer in getattr(f, field)]
    if not shared:
        definition.update({name: getattr(f, name, None) for name in _FOLDER_FLAGS})
    state = {
        "id": f.id,
        "type": "shared" if shared else "private",
        "editable": not shared,
        "definition": definition,
    }
    if shared:
        state["has_my_invites"] = getattr(f, "has_my_invites", None)
    # include/exclude ordering is normalized by Telegram; pin and entity order matter.
    canonical = dict(definition)
    for field in ("include_chat_ids", "exclude_chat_ids"):
        if field in canonical:
            canonical[field] = sorted(canonical[field])
    state["revision"] = hashlib.sha256(
        json.dumps(
            {"id": f.id, "type": state["type"], "definition": canonical},
            sort_keys=True,
            ensure_ascii=True,
        ).encode()
    ).hexdigest()
    return state


@mcp.tool(
    annotations=ToolAnnotations(title="Get Folder Limits", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_folder_limits(account: str = None) -> str:
    """Read effective Premium-aware folder, explicit-chat and pin limits from app config.

    Missing/unusable values are null, never invented defaults. Title limit uses UTF-16 units.
    """
    try:
        return json.dumps(await _read_folder_limits(get_client(account)), indent=2)
    except Exception as e:
        return log_and_format_error("get_folder_limits", e, ErrorCategory.FOLDER)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Folder Snapshot", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_folder_snapshot(account: str = None) -> str:
    """Read complete folder definitions and order without chat content or access hashes.

    Private definition objects can be passed as update_folder patches to restore state.
    Includes title entities, display fields, rules and ordered pins. Title text is untrusted
    data and is kept verbatim for restoration. Shared definitions are read-only metadata,
    not a backup of exported invites or an account-wide undo. No chat resolution is needed.
    """
    try:
        if is_chat_allowlist_enabled():
            return "Error: complete folder snapshots are unavailable with a chat allowlist."
        cl = get_client(account)
        result = await cl(functions.messages.GetDialogFiltersRequest())
        self_id = await _snapshot_self_id(cl, result.filters)
        states = [_folder_state(f, self_id) for f in result.filters]
        return json.dumps(
            {
                "schema_version": 1,
                "folders": states,
                "folder_order": [f["id"] for f in states],
                "tags_enabled": getattr(result, "tags_enabled", None),
                "scope": "folder definitions only; shared invite state and chat state are not restorable",
            },
            indent=2,
        )
    except Exception as e:
        return log_and_format_error("get_folder_snapshot", e, ErrorCategory.FOLDER)


def _validate_folder_patch(folder_id, patch):
    if isinstance(folder_id, bool) or not isinstance(folder_id, int) or not 2 <= folder_id < 2**31:
        raise _FolderValidationError(
            "Error: folder_id must be an existing private folder ID (2..2147483647)."
        )
    allowed = (
        set(_FOLDER_FLAGS) | set(_PEER_FIELDS) | {"title", "title_entities", "emoticon", "color"}
    )
    if not isinstance(patch, dict) or set(patch) - allowed:
        raise _FolderValidationError("Error: patch contains unsupported folder fields.")
    if "title" in patch:
        title = patch["title"]
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title.encode("utf-16-le")) // 2 > _TITLE_LIMIT_UTF16
        ):
            raise _FolderValidationError(
                "Error: title must be nonempty and at most 12 UTF-16 units."
            )
    for name in _FOLDER_FLAGS:
        if name in patch and patch[name] is not None and not isinstance(patch[name], bool):
            raise _FolderValidationError("Error: folder rules must be boolean or null.")
    if "color" in patch and patch["color"] is not None:
        value = patch["color"]
        if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 6:
            raise _FolderValidationError("Error: folder color must be -1..6 or null.")
    if (
        "emoticon" in patch
        and patch["emoticon"] is not None
        and not isinstance(patch["emoticon"], str)
    ):
        raise _FolderValidationError("Error: emoticon must be text or null.")
    for name in _PEER_FIELDS:
        if name not in patch:
            continue
        ids = patch[name]
        if (
            not isinstance(ids, list)
            or any(
                isinstance(i, bool)
                or not isinstance(i, int)
                or i == 0
                or not -(2**63) <= i < 2**63
                for i in ids
            )
            or len(set(ids)) != len(ids)
        ):
            raise _FolderValidationError(
                "Error: peer lists require unique, nonzero marked integer chat IDs."
            )


def _title_entities(data, text):
    if not isinstance(data, list):
        raise _FolderValidationError("Error: title_entities must be a list.")
    result = []
    units = len(text.encode("utf-16-le")) // 2
    for item in data:
        if not isinstance(item, dict):
            raise _FolderValidationError("Error: invalid title entity.")
        name = item.get("_")
        cls = (
            getattr(types, name, None)
            if isinstance(name, str) and name.startswith("MessageEntity")
            else None
        )
        values = {k: v for k, v in item.items() if k != "_"}
        if cls is None or any(isinstance(v, (dict, list)) for v in values.values()):
            raise _FolderValidationError("Error: unsupported title entity.")
        offset, length = values.get("offset"), values.get("length")
        if (
            any(isinstance(v, bool) or not isinstance(v, int) for v in (offset, length))
            or offset < 0
            or length <= 0
            or offset + length > units
        ):
            raise _FolderValidationError("Error: title entity is outside the UTF-16 text range.")
        try:
            result.append(cls(**values))
        except (TypeError, ValueError):
            raise _FolderValidationError("Error: invalid title entity fields.")
    return result


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def update_folder(
    folder_id: int,
    patch: Dict[str, Any],
    expected_revision: Optional[str] = None,
    account: str = None,
) -> str:
    """Patch an existing PRIVATE folder by ID, preserving every omitted field.

    patch fields: title, title_entities (TL entity dictionaries), emoticon, color,
    title_noanimate, contacts, non_contacts, groups, broadcasts, bots, exclude_muted,
    exclude_read, exclude_archived, include_chat_ids, pinned_chat_ids, exclude_chat_ids.
    Peer lists REPLACE that list; [] clears it. Use exact marked integer peer IDs.
    null clears optional display/flag fields. exclude_archived=false includes archived
    dialogs by folder rules; it does not unarchive any chat. title changes clear entities
    unless title_entities is supplied. Shared/system folders are refused. No chat joins,
    leaves, messages, deletions, archive, mute, read-state or global pin mutations occur.
    Save get_folder_snapshot first; its private definition is a reversible patch. Optional
    expected_revision rejects a stale snapshot. A second read detects intervening changes,
    but Telegram offers no atomic compare-and-swap; avoid concurrent folder editors.
    """
    try:
        _validate_folder_patch(folder_id, patch)
        if "title_entities" in patch:
            # Validate structure before any peer lookup. Text bounds checked below.
            if not isinstance(patch["title_entities"], list):
                raise _FolderValidationError("Error: title_entities must be a list.")
        if is_chat_allowlist_enabled():
            return "Error: complete folder updates are unavailable with a chat allowlist."
        cl = get_client(account)
        result = await cl(functions.messages.GetDialogFiltersRequest())
        target = next((f for f in result.filters if getattr(f, "id", None) == folder_id), None)
        if target is None:
            return f"Error: folder {folder_id} not found."
        if not isinstance(target, DialogFilter):
            return "Error: shared folders cannot be edited by update_folder."
        self_id = await _snapshot_self_id(cl, [target])
        before = _folder_state(target, self_id)
        if expected_revision is not None and expected_revision != before["revision"]:
            return "Error: folder changed since the snapshot; read a fresh snapshot first."
        updated = copy.deepcopy(target)
        if "title" in patch:
            updated.title = TextWithEntities(patch["title"], [])
        if "title_entities" in patch:
            updated.title = TextWithEntities(
                updated.title.text, _title_entities(patch["title_entities"], updated.title.text)
            )
        for name in set(_FOLDER_FLAGS) | {"emoticon", "color"}:
            if name in patch:
                setattr(updated, name, patch[name])
        known = {
            _stable_peer_id(peer, self_id): peer
            for field in _PEER_FIELDS.values()
            for peer in getattr(target, field)
        }
        for name, field in _PEER_FIELDS.items():
            if name not in patch:
                continue
            peers = []
            for peer_id in patch[name]:
                peer = known.get(peer_id)
                if peer is None:
                    peer = await resolve_input_entity(peer_id, cl)
                if _stable_peer_id(peer, self_id) != peer_id:
                    raise _FolderValidationError(
                        "Error: resolved peer identity differs from requested ID."
                    )
                peers.append(peer)
            setattr(updated, field, peers)
        if not (
            updated.include_peers
            or updated.pinned_peers
            or any(
                getattr(updated, name)
                for name in ("contacts", "non_contacts", "groups", "broadcasts", "bots")
            )
        ):
            return "Error: folder needs at least one included peer or inclusion rule."
        limits = (await _read_folder_limits(cl))["limits"]
        included = {
            _stable_peer_id(p, self_id) for p in updated.include_peers + updated.pinned_peers
        }
        excluded = {_stable_peer_id(p, self_id) for p in updated.exclude_peers}
        if included & excluded:
            return "Error: included/pinned and excluded peer lists must not overlap."
        chats_limit = limits["chats_per_folder"]
        if chats_limit is not None and (
            len(included) > chats_limit or len(excluded) > chats_limit
        ):
            return f"Error: explicit peer count exceeds the configured per-folder limit ({chats_limit})."
        pin_limit = limits["pinned_per_folder"]
        if pin_limit is not None and len(updated.pinned_peers) > pin_limit:
            return (
                f"Error: pinned peer count exceeds the configured per-folder limit ({pin_limit})."
            )
        after = _folder_state(updated, self_id)
        if before["revision"] == after["revision"]:
            return json.dumps(
                {
                    "success": True,
                    "folder_id": folder_id,
                    "changed": False,
                    "revision": before["revision"],
                }
            )
        latest = await cl(functions.messages.GetDialogFiltersRequest())
        current = next((f for f in latest.filters if getattr(f, "id", None) == folder_id), None)
        if (
            not isinstance(current, DialogFilter)
            or _folder_state(current, self_id)["revision"] != before["revision"]
        ):
            return "Error: folder changed while preparing the update; read a fresh snapshot first."
        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=updated))
        return json.dumps(
            {
                "success": True,
                "folder_id": folder_id,
                "changed": True,
                "revision": after["revision"],
            }
        )
    except _FolderValidationError as e:
        return log_and_format_error("update_folder", e, ErrorCategory.FOLDER, user_message=str(e))
    except Exception as e:
        return log_and_format_error("update_folder", e, ErrorCategory.FOLDER, folder_id=folder_id)


@mcp.tool(annotations=ToolAnnotations(title="List Folders", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def list_folders(account: str = None) -> str:
    """
    Get all dialog folders (filters) with their IDs, names, and emoji.
    Returns a list of folders that can be used with other folder tools.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.messages.GetDialogFiltersRequest())

        folders = []
        for f in result.filters:
            # Skip system default folder
            if isinstance(f, DialogFilterDefault):
                continue

            if isinstance(f, DialogFilter):
                # Handle title which can be str or TextWithEntities
                title = f.title
                if isinstance(title, TextWithEntities):
                    title = title.text
                folder_data = {
                    "id": f.id,
                    "title": sanitize_name(title),
                    "emoticon": getattr(f, "emoticon", None),
                    "contacts": getattr(f, "contacts", False),
                    "non_contacts": getattr(f, "non_contacts", False),
                    "groups": getattr(f, "groups", False),
                    "broadcasts": getattr(f, "broadcasts", False),
                    "bots": getattr(f, "bots", False),
                    "exclude_muted": getattr(f, "exclude_muted", False),
                    "exclude_read": getattr(f, "exclude_read", False),
                    "exclude_archived": getattr(f, "exclude_archived", False),
                    "included_peers_count": len(getattr(f, "include_peers", [])),
                    "excluded_peers_count": len(getattr(f, "exclude_peers", [])),
                    "pinned_peers_count": len(getattr(f, "pinned_peers", [])),
                }
                folders.append(folder_data)

            elif isinstance(f, DialogFilterChatlist):
                # Shared folders use DialogFilterChatlist type
                title = f.title
                if isinstance(title, TextWithEntities):
                    title = title.text
                folder_data = {
                    "id": f.id,
                    "title": sanitize_name(title),
                    "emoticon": getattr(f, "emoticon", None),
                    "type": "shared",
                    "included_peers_count": len(getattr(f, "include_peers", [])),
                    "pinned_peers_count": len(getattr(f, "pinned_peers", [])),
                }
                folders.append(folder_data)

        if not folders:
            return "No folders found. Create one with create_folder tool."

        return json.dumps(
            {"folders": folders, "count": len(folders)}, indent=2, default=json_serializer
        )
    except Exception as e:
        return log_and_format_error("list_folders", e, ErrorCategory.FOLDER)


@mcp.tool(annotations=ToolAnnotations(title="Get Folder", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def get_folder(folder_id: int, account: str = None) -> str:
    """
    Get detailed information about a specific folder including all included chats.

    Args:
        folder_id: The folder ID (get from list_folders)
    """
    try:
        cl = get_client(account)
        result = await cl(functions.messages.GetDialogFiltersRequest())

        target_folder = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                target_folder = f
                break

        if not target_folder:
            return (
                f"Folder with ID {folder_id} not found. Use list_folders to see available folders."
            )

        # Resolve included peers to readable names
        included_chats = []
        for peer in getattr(target_folder, "include_peers", []):
            try:
                entity = await resolve_entity(peer, cl)
                chat_info = {
                    "id": get_marked_id(entity),
                    "name": sanitize_name(
                        getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
                    ),
                    "type": get_entity_type(entity),
                }
                if hasattr(entity, "username") and entity.username:
                    chat_info["username"] = entity.username
                included_chats.append(chat_info)
            except Exception:
                included_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Resolve excluded peers
        excluded_chats = []
        for peer in getattr(target_folder, "exclude_peers", []):
            try:
                entity = await resolve_entity(peer, cl)
                chat_info = {
                    "id": get_marked_id(entity),
                    "name": sanitize_name(
                        getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
                    ),
                    "type": get_entity_type(entity),
                }
                excluded_chats.append(chat_info)
            except Exception:
                excluded_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Resolve pinned peers
        pinned_chats = []
        for peer in getattr(target_folder, "pinned_peers", []):
            try:
                entity = await resolve_entity(peer, cl)
                chat_info = {
                    "id": get_marked_id(entity),
                    "name": sanitize_name(
                        getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
                    ),
                    "type": get_entity_type(entity),
                }
                pinned_chats.append(chat_info)
            except Exception:
                pinned_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Handle title which can be str or TextWithEntities
        title = target_folder.title
        if isinstance(title, TextWithEntities):
            title = title.text

        folder_data = {
            "id": target_folder.id,
            "title": sanitize_name(title),
            "emoticon": getattr(target_folder, "emoticon", None),
            "included_chats": included_chats,
            "excluded_chats": excluded_chats,
            "pinned_chats": pinned_chats,
        }

        if isinstance(target_folder, DialogFilterChatlist):
            folder_data["type"] = "shared"
        else:
            folder_data["filters"] = {
                "contacts": getattr(target_folder, "contacts", False),
                "non_contacts": getattr(target_folder, "non_contacts", False),
                "groups": getattr(target_folder, "groups", False),
                "broadcasts": getattr(target_folder, "broadcasts", False),
                "bots": getattr(target_folder, "bots", False),
                "exclude_muted": getattr(target_folder, "exclude_muted", False),
                "exclude_read": getattr(target_folder, "exclude_read", False),
                "exclude_archived": getattr(target_folder, "exclude_archived", False),
            }

        return json.dumps(folder_data, indent=2, default=json_serializer)
    except Exception as e:
        return log_and_format_error("get_folder", e, ErrorCategory.FOLDER, folder_id=folder_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Folder", openWorldHint=True, destructiveHint=True, idempotentHint=False
    )
)
@with_account(readonly=False)
async def create_folder(
    title: str,
    emoticon: Optional[str] = None,
    chat_ids: Optional[List[Union[int, str]]] = None,
    contacts: bool = False,
    non_contacts: bool = False,
    groups: bool = False,
    broadcasts: bool = False,
    bots: bool = False,
    exclude_muted: bool = False,
    exclude_read: bool = False,
    exclude_archived: bool = True,
    account: str = None,
) -> str:
    """
    Create a new dialog folder.

    Args:
        title: Folder name (required)
        emoticon: Folder emoji (optional, e.g., "📁", "🏠", "💼")
        chat_ids: List of chat IDs or usernames to include (optional)
        contacts: Include all contacts
        non_contacts: Include all non-contacts
        groups: Include all groups
        broadcasts: Include all channels
        bots: Include all bots
        exclude_muted: Exclude muted chats
        exclude_read: Exclude read chats
        exclude_archived: Exclude archived chats (default True)
    """
    try:
        cl = get_client(account)
        # Get existing folders to find the next available ID
        result = await cl(functions.messages.GetDialogFiltersRequest())

        existing_ids = set()
        folder_count = 0
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)):
                existing_ids.add(f.id)
                folder_count += 1

        configured_limit = await _configured_folder_limit(cl)
        if configured_limit is not None:
            limit, premium = configured_limit
            if folder_count >= limit:
                tier = "Premium" if premium else "regular"
                return (
                    f"Cannot create folder: you've reached Telegram's folder limit "
                    f"of {limit} for your {tier} account ({folder_count} folders). "
                    "Delete a folder first."
                )

        # Find next available ID (IDs 0 and 1 are reserved for system)
        new_id = 2
        while new_id in existing_ids:
            new_id += 1

        # Resolve chat_ids to input peers
        include_peers = []
        if chat_ids:
            for chat_id in chat_ids:
                try:
                    peer = await resolve_input_entity(chat_id, cl)
                    include_peers.append(peer)
                except Exception:
                    return "Failed to resolve a requested chat."

        # Create the folder (title must be TextWithEntities)
        title_obj = TextWithEntities(text=title, entities=[])
        new_filter = DialogFilter(
            id=new_id,
            title=title_obj,
            emoticon=emoticon,
            pinned_peers=[],
            include_peers=include_peers,
            exclude_peers=[],
            contacts=contacts,
            non_contacts=non_contacts,
            groups=groups,
            broadcasts=broadcasts,
            bots=bots,
            exclude_muted=exclude_muted,
            exclude_read=exclude_read,
            exclude_archived=exclude_archived,
        )

        # Retain server rejection handling for races and unavailable app config.
        try:
            await cl(functions.messages.UpdateDialogFilterRequest(id=new_id, filter=new_filter))
        except telethon.errors.rpcerrorlist.BadRequestError as e:
            if "DIALOG_FILTERS_TOO_MUCH" in (getattr(e, "message", None) or str(e)):
                return (
                    "Cannot create folder: you've reached Telegram's folder limit "
                    "for your account. "
                    "Delete a folder first."
                )
            raise

        return json.dumps(
            {
                "success": True,
                "folder_id": new_id,
                "title": title,
                "emoticon": emoticon,
                "included_chats_count": len(include_peers),
            },
            indent=2,
        )
    except Exception as e:
        return log_and_format_error("create_folder", e, ErrorCategory.FOLDER, title=title)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add Chat to Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def add_chat_to_folder(
    folder_id: int,
    chat_id: Union[int, str],
    pinned: bool = False,
    account: str = None,
) -> str:
    """
    Add a chat to an existing folder.

    Args:
        folder_id: The folder ID (get from list_folders)
        chat_id: Chat ID or username to add
        pinned: Pin the chat in this folder (default False)
    """
    try:
        cl = get_client(account)
        # Get the folder
        result = await cl(functions.messages.GetDialogFiltersRequest())

        target_folder = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                target_folder = f
                break

        if not target_folder:
            return (
                f"Folder with ID {folder_id} not found. Use list_folders to see available folders."
            )

        # Resolve chat to input peer
        try:
            peer = await resolve_input_entity(chat_id, cl)
        except Exception:
            return "Failed to resolve a requested chat."

        # Check if already included (idempotent)
        include_peers = list(getattr(target_folder, "include_peers", []))
        pinned_peers = list(getattr(target_folder, "pinned_peers", []))

        # Get peer ID for comparison
        peer_id = utils.get_peer_id(peer)
        already_included = any(utils.get_peer_id(p) == peer_id for p in include_peers)
        already_pinned = any(utils.get_peer_id(p) == peer_id for p in pinned_peers)

        if already_included and (not pinned or already_pinned):
            return f"Chat {chat_id} is already in folder {folder_id}."

        # Add to appropriate list
        if not already_included:
            include_peers.append(peer)
        if pinned and not already_pinned:
            pinned_peers.append(peer)

        # Update the folder (keep all original attributes)
        if isinstance(target_folder, DialogFilterChatlist):
            updated_filter = DialogFilterChatlist(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )
        else:
            updated_filter = DialogFilter(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                exclude_peers=list(getattr(target_folder, "exclude_peers", [])),
                contacts=getattr(target_folder, "contacts", False),
                non_contacts=getattr(target_folder, "non_contacts", False),
                groups=getattr(target_folder, "groups", False),
                broadcasts=getattr(target_folder, "broadcasts", False),
                bots=getattr(target_folder, "bots", False),
                exclude_muted=getattr(target_folder, "exclude_muted", False),
                exclude_read=getattr(target_folder, "exclude_read", False),
                exclude_archived=getattr(target_folder, "exclude_archived", False),
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )

        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=updated_filter))

        return (
            f"Chat {chat_id} added to folder {folder_id}" + (" (pinned)" if pinned else "") + "."
        )
    except Exception as e:
        return log_and_format_error(
            "add_chat_to_folder", e, ErrorCategory.FOLDER, folder_id=folder_id, chat_id=chat_id
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Remove Chat from Folder",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def remove_chat_from_folder(
    folder_id: int, chat_id: Union[int, str], account: str = None
) -> str:
    """
    Remove a chat from a folder.

    Args:
        folder_id: The folder ID (get from list_folders)
        chat_id: Chat ID or username to remove
    """
    try:
        cl = get_client(account)
        # Get the folder
        result = await cl(functions.messages.GetDialogFiltersRequest())

        target_folder = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                target_folder = f
                break

        if not target_folder:
            return (
                f"Folder with ID {folder_id} not found. Use list_folders to see available folders."
            )

        # Resolve chat to get peer ID
        try:
            peer = await resolve_input_entity(chat_id, cl)
            peer_id = utils.get_peer_id(peer)
        except Exception:
            return "Failed to resolve a requested chat."

        # Filter out the peer from both include and pinned lists
        include_peers = [
            p
            for p in getattr(target_folder, "include_peers", [])
            if utils.get_peer_id(p) != peer_id
        ]
        pinned_peers = [
            p
            for p in getattr(target_folder, "pinned_peers", [])
            if utils.get_peer_id(p) != peer_id
        ]

        original_include_count = len(getattr(target_folder, "include_peers", []))
        original_pinned_count = len(getattr(target_folder, "pinned_peers", []))

        # Check if anything was removed (idempotent)
        if (
            len(include_peers) == original_include_count
            and len(pinned_peers) == original_pinned_count
        ):
            return f"Chat {chat_id} was not in folder {folder_id}."

        # Update the folder (keep all original attributes)
        if isinstance(target_folder, DialogFilterChatlist):
            updated_filter = DialogFilterChatlist(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )
        else:
            updated_filter = DialogFilter(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                exclude_peers=list(getattr(target_folder, "exclude_peers", [])),
                contacts=getattr(target_folder, "contacts", False),
                non_contacts=getattr(target_folder, "non_contacts", False),
                groups=getattr(target_folder, "groups", False),
                broadcasts=getattr(target_folder, "broadcasts", False),
                bots=getattr(target_folder, "bots", False),
                exclude_muted=getattr(target_folder, "exclude_muted", False),
                exclude_read=getattr(target_folder, "exclude_read", False),
                exclude_archived=getattr(target_folder, "exclude_archived", False),
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )

        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=updated_filter))

        return f"Chat {chat_id} removed from folder {folder_id}."
    except Exception as e:
        return log_and_format_error(
            "remove_chat_from_folder",
            e,
            ErrorCategory.FOLDER,
            folder_id=folder_id,
            chat_id=chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def delete_folder(folder_id: int, account: str = None) -> str:
    """
    Delete a folder. Chats in the folder are preserved, only the folder is removed.

    Args:
        folder_id: The folder ID to delete (get from list_folders)
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # System folders (id < 2) cannot be deleted
        if folder_id < 2:
            return f"Cannot delete system folder (ID {folder_id}). Only custom folders can be deleted."

        # Check if folder exists
        result = await cl(functions.messages.GetDialogFiltersRequest())

        folder_exists = False
        folder_title = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                folder_exists = True
                # Handle title which can be str or TextWithEntities
                title = f.title
                if isinstance(title, TextWithEntities):
                    title = title.text
                folder_title = title
                break

        if not folder_exists:
            return f"Folder with ID {folder_id} not found (may already be deleted)."

        # Delete by passing None as filter
        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=None))

        return f"Folder '{sanitize_name(folder_title)}' (ID {folder_id}) deleted. Chats are preserved."
    except Exception as e:
        return log_and_format_error("delete_folder", e, ErrorCategory.FOLDER, folder_id=folder_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Reorder Folders", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def reorder_folders(folder_ids: List[int], account: str = None) -> str:
    """
    Change the order of folders in the folder list.

    Args:
        folder_ids: List of folder IDs in the desired order
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Get existing folders to validate
        result = await cl(functions.messages.GetDialogFiltersRequest())

        existing_ids = set()
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)):
                existing_ids.add(f.id)

        # Validate all provided IDs exist
        for fid in folder_ids:
            if fid not in existing_ids:
                return f"Folder ID {fid} not found. Use list_folders to see available folders."

        # Validate all existing folders are included
        if set(folder_ids) != existing_ids:
            missing = existing_ids - set(folder_ids)
            return f"All folder IDs must be included. Missing: {missing}"

        # Reorder
        await cl(functions.messages.UpdateDialogFiltersOrderRequest(order=folder_ids))

        return f"Folders reordered: {folder_ids}"
    except Exception as e:
        return log_and_format_error(
            "reorder_folders", e, ErrorCategory.FOLDER, folder_ids=folder_ids
        )


__all__ = [
    "get_folder_limits",
    "get_folder_snapshot",
    "update_folder",
    "list_folders",
    "get_folder",
    "create_folder",
    "add_chat_to_folder",
    "remove_chat_from_folder",
    "delete_folder",
    "reorder_folders",
]
