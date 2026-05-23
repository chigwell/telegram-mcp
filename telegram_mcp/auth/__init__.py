"""OAuth 2.1 authentication for the HTTP transport."""

from telegram_mcp.auth.single_user_provider import (
    SingleUserOAuthProvider,
    build_auth_settings,
    build_oauth_provider,
)
from telegram_mcp.auth.storage import OAuthStore

__all__ = [
    "OAuthStore",
    "SingleUserOAuthProvider",
    "build_auth_settings",
    "build_oauth_provider",
]
