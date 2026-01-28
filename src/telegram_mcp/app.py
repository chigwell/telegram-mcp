"""Application entry point for telegram-mcp."""

import asyncio
import sqlite3
import sys

import nest_asyncio
from mcp.server.fastmcp import FastMCP
from telethon import TelegramClient
from telethon.sessions import StringSession

from .config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELEGRAM_SESSION_NAME,
    SESSION_STRING,
)

# Create MCP server instance
mcp = FastMCP("telegram")

# Create Telegram client instance
if SESSION_STRING:
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
else:
    client = TelegramClient(TELEGRAM_SESSION_NAME, TELEGRAM_API_ID, TELEGRAM_API_HASH)


async def _main() -> None:
    try:
        # Start the Telethon client non-interactively
        print("Starting Telegram client...")
        await client.start()

        print("Telegram client started. Running MCP server...")
        # Use the asynchronous entrypoint instead of mcp.run()
        await mcp.run_stdio_async()
    except Exception as e:
        print(f"Error starting client: {e}", file=sys.stderr)
        if isinstance(e, sqlite3.OperationalError) and "database is locked" in str(e):
            print(
                "Database lock detected. Please ensure no other instances are running.",
                file=sys.stderr,
            )
        sys.exit(1)


def main() -> None:
    # Import tools to register them with mcp
    from . import tools  # noqa: F401

    nest_asyncio.apply()
    asyncio.run(_main())
