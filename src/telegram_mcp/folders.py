import os
import time
import asyncio
from typing import List, Dict, Optional, Union, Any
from pathlib import Path
from telethon.tl.types import *
from telethon.tl.functions.messages import GetForumTopicsRequest, ReadDiscussionRequest
from telethon import functions, types, utils
from mcp.server.fastmcp import Context

# Use absolute imports for our own module as it's the standard for PyPI packages
from telegram_mcp.client import client, logger
from telegram_mcp.utils import *
from telegram_mcp.security import *


async def list_folders() -> str:
    """
    Get all dialog folders (filters) with their IDs, names, and emoji.
    Returns a list of folders that can be used with other folder tools.
    """
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())

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
                    "title": title,
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
                    "title": title,
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


async def get_folder(folder_id: int) -> str:
    """
    Get detailed information about a specific folder including all included chats.

    Args:
        folder_id: The folder ID (get from list_folders)
    """
    try:
        result = await client(functions.messages.GetDialogFiltersRequest())

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
                entity = await resolve_entity(peer)
                chat_info = {
                    "id": entity.id,
                    "name": getattr(entity, "title", None)
                    or getattr(entity, "first_name", "Unknown"),
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
                entity = await resolve_entity(peer)
                chat_info = {
                    "id": entity.id,
                    "name": getattr(entity, "title", None)
                    or getattr(entity, "first_name", "Unknown"),
                    "type": get_entity_type(entity),
                }
                excluded_chats.append(chat_info)
            except Exception:
                excluded_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Resolve pinned peers
        pinned_chats = []
        for peer in getattr(target_folder, "pinned_peers", []):
            try:
                entity = await resolve_entity(peer)
                chat_info = {
                    "id": entity.id,
                    "name": getattr(entity, "title", None)
                    or getattr(entity, "first_name", "Unknown"),
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
            "title": title,
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
