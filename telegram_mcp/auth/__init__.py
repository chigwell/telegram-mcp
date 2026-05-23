"""OAuth 2.1 authentication for the HTTP transport."""

from telegram_mcp.auth.single_user_provider import (
    SingleUserOAuthProvider,
    build_auth_settings,
    build_oauth_provider,
)

__all__ = [
    "SingleUserOAuthProvider",
    "build_auth_settings",
    "build_oauth_provider",
]
