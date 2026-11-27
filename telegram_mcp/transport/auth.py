"""Optional static HTTP bearer authentication using the MCP SDK's middleware."""

import hmac
import os

from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import AccessToken
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware

STATIC_PRINCIPAL = "telegram-mcp-owner"


class StaticTokenVerifier:
    def __init__(self, token):
        if len(token) < 32 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ValueError(
                "TELEGRAM_MCP_HTTP_TOKEN must contain at least 32 printable ASCII characters"
            )
        self.token = token

    async def verify_token(self, token):
        if not hmac.compare_digest(token.encode(), self.token.encode()):
            return None
        return AccessToken(
            token=token, client_id=STATIC_PRINCIPAL, subject=STATIC_PRINCIPAL, scopes=[]
        )


def principal(request):
    user = request.scope.get("user") if request else None
    if not isinstance(user, AuthenticatedUser) or not getattr(
        request.app.state, "telegram_static_auth", False
    ):
        return None
    return STATIC_PRINCIPAL


def with_http_auth(app, server, paths):
    token = os.getenv("TELEGRAM_MCP_HTTP_TOKEN")
    if token is None:
        return app
    if getattr(server, "_token_verifier", None) is not None:
        raise ValueError(
            "Configure either static HTTP authentication or SDK authentication, not both"
        )
    verifier = StaticTokenVerifier(token)
    app.state.telegram_static_auth = True
    app.user_middleware.insert(
        0, Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier))
    )
    for route in app.routes:
        if route.path in paths:
            route.app = RequireAuthMiddleware(route.app, required_scopes=[])
    return app
