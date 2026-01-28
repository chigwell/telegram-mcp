"""Integration tests for read-only tools.

These tests require valid Telegram credentials in .env file.
They test actual API calls to Telegram.
"""

import pytest
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

# Skip all tests if credentials are not available
pytestmark = pytest.mark.skipif(
    not os.getenv("TELEGRAM_API_ID") or os.getenv("TELEGRAM_API_ID") == "12345",
    reason="Valid Telegram credentials required"
)


@pytest.fixture(scope="module")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def setup_client():
    """Setup Telegram client for testing."""
    from telegram_mcp.app import client

    if not client.is_connected():
        await client.connect()

    yield client


class TestUserTools:
    """Test user-related read-only tools."""

    @pytest.mark.asyncio
    async def test_get_me(self, setup_client):
        from telegram_mcp.tools.users import get_me
        result = await get_me()
        assert "id" in result or "Error" not in result
        print(f"get_me result: {result[:200]}...")

    @pytest.mark.asyncio
    async def test_get_privacy_settings(self, setup_client):
        from telegram_mcp.tools.users import get_privacy_settings
        result = await get_privacy_settings()
        assert result is not None
        print(f"get_privacy_settings result: {result[:200]}...")


class TestChatTools:
    """Test chat-related read-only tools."""

    @pytest.mark.asyncio
    async def test_get_chats(self, setup_client):
        from telegram_mcp.tools.chats import get_chats
        result = await get_chats(limit=5)
        assert result is not None
        print(f"get_chats result: {result[:300]}...")

    @pytest.mark.asyncio
    async def test_list_chats(self, setup_client):
        from telegram_mcp.tools.chats import list_chats
        result = await list_chats(limit=5)
        assert result is not None
        print(f"list_chats result: {result[:300]}...")


class TestContactTools:
    """Test contact-related read-only tools."""

    @pytest.mark.asyncio
    async def test_list_contacts(self, setup_client):
        from telegram_mcp.tools.contacts import list_contacts
        result = await list_contacts()
        assert result is not None
        print(f"list_contacts result: {result[:300]}...")

    @pytest.mark.asyncio
    async def test_get_blocked_users(self, setup_client):
        from telegram_mcp.tools.contacts import get_blocked_users
        result = await get_blocked_users()
        assert result is not None
        print(f"get_blocked_users result: {result[:200]}...")


class TestFolderTools:
    """Test folder-related read-only tools."""

    @pytest.mark.asyncio
    async def test_list_folders(self, setup_client):
        from telegram_mcp.tools.folders import list_folders
        result = await list_folders()
        assert result is not None
        print(f"list_folders result: {result[:300]}...")


class TestMediaTools:
    """Test media-related read-only tools."""

    @pytest.mark.asyncio
    async def test_get_sticker_sets(self, setup_client):
        from telegram_mcp.tools.media import get_sticker_sets
        result = await get_sticker_sets()
        assert result is not None
        print(f"get_sticker_sets result: {result[:200]}...")


class TestMiscTools:
    """Test miscellaneous read-only tools."""

    @pytest.mark.asyncio
    async def test_get_drafts(self, setup_client):
        from telegram_mcp.tools.misc import get_drafts
        result = await get_drafts()
        assert result is not None
        print(f"get_drafts result: {result[:200]}...")
