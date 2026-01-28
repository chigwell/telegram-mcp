"""Backward-compatible entry point for telegram-mcp.

This module re-exports main from the telegram_mcp package for backward compatibility.
New code should import directly from telegram_mcp.
"""

from telegram_mcp import main

if __name__ == "__main__":
    main()
