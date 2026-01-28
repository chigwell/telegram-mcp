#!/usr/bin/env python3
"""Test script for read-only Telegram MCP tools.

Run this script to test all read-only tools against your Telegram account.
Requires valid credentials in .env file.
"""

import asyncio
import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

# Track test results
results = {"passed": [], "failed": [], "skipped": []}


async def test_tool(name: str, coro, skip_condition=None):
    """Run a single tool test."""
    if skip_condition:
        results["skipped"].append((name, skip_condition))
        print(f"  SKIP: {name} - {skip_condition}")
        return None

    try:
        result = await coro
        if "Error" in str(result) and "not found" not in str(result).lower():
            results["failed"].append((name, str(result)[:100]))
            print(f"  FAIL: {name}")
            print(f"        {str(result)[:100]}")
        else:
            results["passed"].append(name)
            # Truncate long results
            display = str(result)[:150].replace("\n", " ")
            print(f"  PASS: {name}")
            print(f"        {display}...")
        return result
    except Exception as e:
        results["failed"].append((name, str(e)[:100]))
        print(f"  FAIL: {name}")
        print(f"        Exception: {str(e)[:100]}")
        return None


async def run_tests():
    """Run all read-only tool tests."""
    print("=" * 60)
    print("Telegram MCP Read-Only Tools Test")
    print("=" * 60)

    # Import client and connect
    from telegram_mcp.app import client

    print("\n[1] Connecting to Telegram...")
    try:
        await client.connect()
        me = await client.get_me()
        print(f"    Connected as: {me.first_name} (ID: {me.id})")
    except Exception as e:
        print(f"    Failed to connect: {e}")
        return

    # === User Tools ===
    print("\n[2] Testing User Tools...")
    from telegram_mcp.tools.users import (
        get_me, get_privacy_settings, get_user_photos, get_user_status
    )

    await test_tool("get_me", get_me())
    await test_tool("get_privacy_settings", get_privacy_settings())
    await test_tool("get_user_photos", get_user_photos(me.id))
    await test_tool("get_user_status", get_user_status(me.id))

    # === Chat Tools ===
    print("\n[3] Testing Chat Tools...")
    from telegram_mcp.tools.chats import (
        get_chats, list_chats, get_chat, list_topics, search_public_chats
    )

    await test_tool("get_chats", get_chats(page=1, page_size=3))
    chats_result = await test_tool("list_chats", list_chats(limit=3))

    # Get a chat ID directly from client for further tests
    chat_id = None
    try:
        dialogs = await client.get_dialogs(limit=1)
        if dialogs:
            chat_id = dialogs[0].entity.id
            # Adjust for channels/groups
            if hasattr(dialogs[0].entity, 'broadcast') or hasattr(dialogs[0].entity, 'megagroup'):
                chat_id = -1000000000000 - dialogs[0].entity.id
            elif hasattr(dialogs[0].entity, 'title'):
                chat_id = -dialogs[0].entity.id
            print(f"    Using chat_id: {chat_id}")
    except Exception as e:
        print(f"    Could not get chat_id: {e}")

    if chat_id:
        await test_tool("get_chat", get_chat(chat_id))
        await test_tool("list_topics", list_topics(chat_id))

    await test_tool("search_public_chats", search_public_chats("python"))

    # === Contact Tools ===
    print("\n[4] Testing Contact Tools...")
    from telegram_mcp.tools.contacts import (
        list_contacts, search_contacts, get_contact_ids,
        export_contacts, get_blocked_users, resolve_username
    )

    await test_tool("list_contacts", list_contacts())
    await test_tool("search_contacts", search_contacts("test"))
    await test_tool("get_contact_ids", get_contact_ids())
    await test_tool("export_contacts", export_contacts())
    await test_tool("get_blocked_users", get_blocked_users())
    await test_tool("resolve_username", resolve_username("telegram"))

    # === Folder Tools ===
    print("\n[5] Testing Folder Tools...")
    from telegram_mcp.tools.folders import list_folders, get_folder

    folders_result = await test_tool("list_folders", list_folders())

    # Get a folder ID for further tests
    folder_id = None
    if folders_result and "Error" not in str(folders_result):
        try:
            folders_data = json.loads(folders_result)
            if folders_data.get("folders"):
                folder_id = folders_data["folders"][0].get("id")
        except:
            pass

    if folder_id:
        await test_tool("get_folder", get_folder(folder_id))

    # === Message Tools ===
    print("\n[6] Testing Message Tools...")
    from telegram_mcp.tools.messages import (
        get_messages, list_messages, get_pinned_messages, search_messages
    )

    if chat_id:
        await test_tool("get_messages", get_messages(chat_id, page=1, page_size=3))
        await test_tool("list_messages", list_messages(chat_id, limit=3))
        await test_tool("get_pinned_messages", get_pinned_messages(chat_id))
        await test_tool("search_messages", search_messages(chat_id, "test"))
    else:
        print("  SKIP: Message tools (no chat_id available)")

    # === Media Tools ===
    print("\n[7] Testing Media Tools...")
    from telegram_mcp.tools.media import (
        get_sticker_sets, get_gif_search, get_bot_info
    )

    await test_tool("get_sticker_sets", get_sticker_sets())
    await test_tool("get_gif_search", get_gif_search("hello", limit=3))
    await test_tool("get_bot_info", get_bot_info("BotFather"))

    # === Misc Tools ===
    print("\n[8] Testing Misc Tools...")
    from telegram_mcp.tools.misc import get_drafts

    await test_tool("get_drafts", get_drafts())

    # === Admin Tools ===
    print("\n[9] Testing Admin Tools...")
    from telegram_mcp.tools.admin import get_admins, get_banned_users

    if chat_id:
        await test_tool("get_admins", get_admins(chat_id))
        await test_tool("get_banned_users", get_banned_users(chat_id))
    else:
        print("  SKIP: Admin tools (no chat_id available)")

    # Print summary
    print("\n" + "=" * 60)
    print("Test Summary")
    print("=" * 60)
    print(f"  Passed:  {len(results['passed'])}")
    print(f"  Failed:  {len(results['failed'])}")
    print(f"  Skipped: {len(results['skipped'])}")

    if results["failed"]:
        print("\nFailed tests:")
        for name, error in results["failed"]:
            print(f"  - {name}: {error}")

    print("\n" + "=" * 60)

    # Disconnect
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(run_tests())
