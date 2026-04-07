import asyncio
import sys
import json
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, "./src")
from telegram_mcp.mcp_server import mcp


async def main():
    try:
        # FastMCP internals for testing tool calls simulate an MCP call
        print("Calling create_folder with payload object...")
        # Since 'create_folder' uses Telethon, it might crash without a real session,
        # but what we care about is if the payload binding works or if it throws a TypeError/ValidationError.

        args = {"payload": {"title": "TestFolderSchema", "include_contacts": True}}

        # FastMCP tools are stored in mcp._tools or similar. We can also just use the tool call handler.
        result = await mcp.call_tool("create_folder", args)
        print(f"Tool executed successfully. Result: {result}")

    except Exception as e:
        print(f"Failed: {type(e).__name__} - {e}")


if __name__ == "__main__":
    asyncio.run(main())
