"""Compatibility alias module for stickers and custom emoji tools."""

from telegram_mcp.tools.custom_emoji import (
    extract_pack_short_name,
    get_custom_emoji_pack,
    get_custom_emoji_packs,
    preview_custom_emoji,
)

__all__ = [
    "extract_pack_short_name",
    "get_custom_emoji_packs",
    "get_custom_emoji_pack",
    "preview_custom_emoji",
]
