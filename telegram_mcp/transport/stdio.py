"""Stdio transport runner with MCP 2026-07-28 protocol negotiation support."""

from __future__ import annotations

import logging
from typing import Any

from telegram_mcp.transport.protocol import install_protocol_compatibility

logger = logging.getLogger(__name__)


async def run_stdio_async(mcp_instance: Any) -> None:
    """Run FastMCP over standard I/O with MCP 2026-07-28 protocol compatibility."""
    install_protocol_compatibility()
    await mcp_instance.run_stdio_async()
