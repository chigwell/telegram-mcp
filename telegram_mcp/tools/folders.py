"""Folders MCP tools."""

from telegram_mcp.runtime import *


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
        logger.exception("list_folders failed")
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
                    "id": entity.id,
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
                    "id": entity.id,
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
                    "id": entity.id,
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
        logger.exception(f"get_folder failed (folder_id={folder_id})")
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
        # Get existing folders to check count and find next ID
        result = await cl(functions.messages.GetDialogFiltersRequest())

        existing_ids = set()
        folder_count = 0
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)):
                existing_ids.add(f.id)
                folder_count += 1

        # Telegram limit: max 10 custom folders
        if folder_count >= 10:
            return "Cannot create folder: Telegram limit is 10 folders. Delete one first."

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
                except Exception as e:
                    return f"Failed to resolve chat '{chat_id}': {str(e)}"

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

        await cl(functions.messages.UpdateDialogFilterRequest(id=new_id, filter=new_filter))

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
        logger.exception(f"create_folder failed (title={title})")
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
        except Exception as e:
            return f"Failed to resolve chat '{chat_id}': {str(e)}"

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
        logger.exception(f"add_chat_to_folder failed (folder_id={folder_id}, chat_id={chat_id})")
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
        except Exception as e:
            return f"Failed to resolve chat '{chat_id}': {str(e)}"

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
        logger.exception(
            f"remove_chat_from_folder failed (folder_id={folder_id}, chat_id={chat_id})"
        )
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
        logger.exception(f"delete_folder failed (folder_id={folder_id})")
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
        logger.exception(f"reorder_folders failed (folder_ids={folder_ids})")
        return log_and_format_error(
            "reorder_folders", e, ErrorCategory.FOLDER, folder_ids=folder_ids
        )


__all__ = [
    "list_folders",
    "get_folder",
    "create_folder",
    "add_chat_to_folder",
    "remove_chat_from_folder",
    "delete_folder",
    "reorder_folders",
]
