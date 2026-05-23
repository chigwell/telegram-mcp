"""Single-user OAuth 2.1 + PKCE provider for the HTTP transport.

This implements the MCP SDK's :class:`OAuthAuthorizationServerProvider` and
:class:`TokenVerifier` interfaces with a SQLite-backed storage layer
(see :mod:`telegram_mcp.auth.storage`). It is intended for a personal,
single-user deployment fronted by a Cloudflare Tunnel, Tailscale Funnel,
or any HTTPS reverse proxy.

Persistence lets Claude Desktop's connector survive container restarts
without forcing a re-login: registered clients, issued access tokens,
and refresh tokens all live on disk. Short-lived in-flight state (the
login session cookie between ``/authorize`` and the form POST) stays in
memory because it's tied to one browser window.

Flow:

1. Claude Desktop performs Dynamic Client Registration against ``/register``
   (handled automatically by the MCP SDK; ``register_client`` below stores
   the metadata).
2. Claude Desktop sends the user's browser to ``/authorize`` with PKCE
   parameters. The SDK calls :meth:`SingleUserOAuthProvider.authorize`,
   which stashes the request in memory and returns a URL to a local login
   form.
3. The user submits username + password (compared with ``hmac.compare_digest``
   against env-var values). On success we issue an authorization code and
   redirect the browser back to Claude Desktop's ``redirect_uri``.
4. Claude Desktop exchanges the code (with PKCE ``code_verifier``) at
   ``/token`` for an access token + refresh token. The SDK's ``TokenHandler``
   handles PKCE verification; we only mint tokens.
5. Every MCP request carries ``Authorization: Bearer <token>``; the SDK's
   ``BearerAuthBackend`` calls :meth:`verify_token` to validate it.
"""

from __future__ import annotations

import asyncio
import hmac
import os
import secrets
import time
from typing import Optional

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenVerifier,
    construct_redirect_uri,
)
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from telegram_mcp.auth.storage import OAuthStore


# Token / code lifetimes -----------------------------------------------------
AUTH_CODE_TTL = 600  # 10 minutes
LOGIN_SESSION_TTL = 600  # 10 minutes -- user must finish login within this window
ACCESS_TOKEN_TTL = 24 * 60 * 60  # 24 hours
REFRESH_TOKEN_TTL = 30 * 24 * 60 * 60  # 30 days


class SingleUserOAuthProvider(OAuthAuthorizationServerProvider, TokenVerifier):
    """OAuth 2.1 + PKCE provider for one user, backed by SQLite.

    State that needs to survive a container restart (registered clients,
    auth codes, access/refresh tokens) lives in the SQLite store. The
    short-lived "user has hit /authorize and not yet submitted the login
    form" mapping stays in process memory because it's bound to a single
    browser window and naturally vanishes if the user gives up.
    """

    def __init__(
        self,
        *,
        username: str,
        password: str,
        public_url: str,
        store: OAuthStore,
    ) -> None:
        self._username = username
        self._password = password
        self._public_url = public_url.rstrip("/")
        self._store = store
        # session_id -> {"client_id", "params", "expires_at"}
        self._login_sessions: dict[str, dict] = {}

    # -- OAuthAuthorizationServerProvider ----------------------------------

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        return await asyncio.to_thread(self._store.get_client, client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Dynamic Client Registration: trust whatever Claude Desktop sends.
        # The SDK has already validated the metadata shape before calling us.
        await asyncio.to_thread(self._store.put_client, client_info)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        # Stash the request in memory; return the absolute URL of our login
        # form. The SDK 302-redirects the user's browser to whatever string
        # we return.
        session_id = secrets.token_urlsafe(24)
        self._login_sessions[session_id] = {
            "client_id": client.client_id,
            "params": params,
            "expires_at": time.time() + LOGIN_SESSION_TTL,
        }
        self._gc_login_sessions()
        await asyncio.to_thread(self._store.gc)
        return f"{self._public_url}/login?session={session_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> Optional[AuthorizationCode]:
        entry = await asyncio.to_thread(self._store.get_auth_code, authorization_code)
        if entry is None or entry.client_id != client.client_id:
            return None
        if entry.expires_at < time.time():
            await asyncio.to_thread(self._store.delete_auth_code, authorization_code)
            return None
        return entry

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # Authorization codes are single-use.
        await asyncio.to_thread(
            self._store.delete_auth_code, authorization_code.code
        )
        access = self._make_access_token(
            client.client_id,
            authorization_code.scopes,
            resource=authorization_code.resource,
        )
        refresh = self._make_refresh_token(
            client.client_id, authorization_code.scopes
        )
        await asyncio.to_thread(self._store.put_access_token, access)
        await asyncio.to_thread(
            self._store.put_refresh_token, refresh, access.token
        )
        return self._build_oauth_token(access, refresh)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> Optional[RefreshToken]:
        entry = await asyncio.to_thread(self._store.get_refresh_token, refresh_token)
        if entry is None or entry.client_id != client.client_id:
            return None
        if entry.expires_at and entry.expires_at < time.time():
            await asyncio.to_thread(self._store.delete_refresh_token, refresh_token)
            return None
        return entry

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Rotate both access and refresh tokens.
        old_access = await asyncio.to_thread(
            self._store.get_access_token_for_refresh, refresh_token.token
        )
        if old_access:
            await asyncio.to_thread(self._store.delete_access_token, old_access)
        await asyncio.to_thread(
            self._store.delete_refresh_token, refresh_token.token
        )

        new_scopes = scopes or refresh_token.scopes
        access = self._make_access_token(client.client_id, new_scopes)
        new_refresh = self._make_refresh_token(client.client_id, new_scopes)
        await asyncio.to_thread(self._store.put_access_token, access)
        await asyncio.to_thread(
            self._store.put_refresh_token, new_refresh, access.token
        )
        return self._build_oauth_token(access, new_refresh)

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        entry = await asyncio.to_thread(self._store.get_access_token, token)
        if entry is None:
            return None
        if entry.expires_at and entry.expires_at < time.time():
            await asyncio.to_thread(self._store.delete_access_token, token)
            return None
        return entry

    async def revoke_token(self, token) -> None:  # AccessToken | RefreshToken
        raw = getattr(token, "token", None)
        if not raw:
            return
        await asyncio.to_thread(self._store.revoke, raw)

    # -- TokenVerifier ------------------------------------------------------

    async def verify_token(self, token: str) -> Optional[AccessToken]:
        return await self.load_access_token(token)

    # -- Login form (Starlette routes) -------------------------------------

    async def login_form(self, request: Request) -> Response:
        session_id = request.query_params.get("session", "")
        if not self._valid_session(session_id):
            return HTMLResponse(
                self._render_message("Invalid or expired login session."),
                status_code=400,
            )
        return HTMLResponse(self._render_form(session_id))

    async def login_submit(self, request: Request) -> Response:
        form = await request.form()
        session_id = str(form.get("session", ""))
        username = str(form.get("username", ""))
        password = str(form.get("password", ""))

        session = self._login_sessions.get(session_id)
        if session is None or session["expires_at"] < time.time():
            return HTMLResponse(
                self._render_message("Invalid or expired login session."),
                status_code=400,
            )

        # Constant-time comparison.
        ok_user = hmac.compare_digest(username, self._username)
        ok_pass = hmac.compare_digest(password, self._password)
        if not (ok_user and ok_pass):
            return HTMLResponse(
                self._render_form(session_id, error="Invalid username or password."),
                status_code=401,
            )

        # Success: consume the session, mint an auth code, redirect back.
        self._login_sessions.pop(session_id, None)
        params: AuthorizationParams = session["params"]
        client_id: str = session["client_id"]
        code_str = secrets.token_urlsafe(48)
        entry = AuthorizationCode(
            code=code_str,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        await asyncio.to_thread(self._store.put_auth_code, entry)
        redirect = construct_redirect_uri(
            str(params.redirect_uri), code=code_str, state=params.state
        )
        return RedirectResponse(redirect, status_code=302)

    def routes(self) -> list[Route]:
        return [
            Route("/login", endpoint=self.login_form, methods=["GET"]),
            Route("/login", endpoint=self.login_submit, methods=["POST"]),
        ]

    # -- Helpers ------------------------------------------------------------

    def _make_access_token(
        self,
        client_id: str,
        scopes: list[str],
        resource: Optional[str] = None,
    ) -> AccessToken:
        return AccessToken(
            token=secrets.token_urlsafe(48),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + ACCESS_TOKEN_TTL),
            resource=resource,
        )

    def _make_refresh_token(
        self, client_id: str, scopes: list[str]
    ) -> RefreshToken:
        return RefreshToken(
            token=secrets.token_urlsafe(48),
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + REFRESH_TOKEN_TTL),
        )

    def _build_oauth_token(
        self, access: AccessToken, refresh: RefreshToken
    ) -> OAuthToken:
        return OAuthToken(
            access_token=access.token,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(access.scopes) if access.scopes else None,
            refresh_token=refresh.token,
        )

    def _valid_session(self, session_id: str) -> bool:
        session = self._login_sessions.get(session_id)
        return session is not None and session["expires_at"] >= time.time()

    def _gc_login_sessions(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, value in self._login_sessions.items()
            if value["expires_at"] < now
        ]
        for sid in expired:
            self._login_sessions.pop(sid, None)

    @staticmethod
    def _render_message(text: str) -> str:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>telegram-mcp</title></head>"
            f"<body style='font-family:system-ui;padding:24px;'><h1>{text}</h1>"
            "</body></html>"
        )

    @staticmethod
    def _render_form(session_id: str, error: Optional[str] = None) -> str:
        error_html = (
            f"<p style='color:#c0392b;margin:0 0 8px;'>{error}</p>" if error else ""
        )
        return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>telegram-mcp login</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; background: #f5f5f7;
         display: flex; align-items: center; justify-content: center;
         min-height: 100vh; margin: 0; }}
  form {{ background: #fff; padding: 28px 32px; border-radius: 12px;
         box-shadow: 0 6px 24px rgba(0,0,0,.08); width: 320px; }}
  h1 {{ margin: 0 0 16px; font-size: 18px; color: #111; }}
  label {{ display: block; margin-top: 12px; font-size: 13px; color: #444; }}
  input {{ width: 100%; padding: 10px; margin-top: 4px; border: 1px solid #ddd;
          border-radius: 8px; font-size: 14px; box-sizing: border-box; }}
  button {{ margin-top: 18px; width: 100%; padding: 10px; background: #1f6feb;
           color: #fff; border: 0; border-radius: 8px; font-size: 14px;
           cursor: pointer; }}
  button:hover {{ background: #1a5fd0; }}
</style></head>
<body>
<form method="post" action="/login">
  <h1>telegram-mcp sign in</h1>
  {error_html}
  <input type="hidden" name="session" value="{session_id}">
  <label>Username
    <input name="username" autocomplete="username" autofocus>
  </label>
  <label>Password
    <input type="password" name="password" autocomplete="current-password">
  </label>
  <button type="submit">Sign in</button>
</form></body></html>"""


# -- Factory helpers ---------------------------------------------------------


def build_oauth_provider() -> SingleUserOAuthProvider:
    """Build a provider from ``TELEGRAM_MCP_AUTH_*`` env vars.

    Reads:
      * ``TELEGRAM_MCP_AUTH_USERNAME`` (default ``admin``)
      * ``TELEGRAM_MCP_AUTH_PASSWORD`` (required)
      * ``TELEGRAM_MCP_PUBLIC_URL``    (required)
      * ``TELEGRAM_MCP_OAUTH_DB``      (default ``:memory:``)

    Setting ``TELEGRAM_MCP_OAUTH_DB`` to a file path enables persistence
    across restarts. In Docker the recommended layout is a named volume
    mounted at ``/data`` with the env var set to ``/data/oauth.db``.

    Raises :class:`SystemExit` (caught by ``runner.main()``) when required
    environment variables are missing.
    """
    username = os.getenv("TELEGRAM_MCP_AUTH_USERNAME", "admin")
    password = os.getenv("TELEGRAM_MCP_AUTH_PASSWORD")
    public_url = os.getenv("TELEGRAM_MCP_PUBLIC_URL")
    db_path = os.getenv("TELEGRAM_MCP_OAUTH_DB", ":memory:")
    if not password:
        raise SystemExit(
            "TELEGRAM_MCP_AUTH_PASSWORD must be set when TELEGRAM_MCP_TRANSPORT=http"
        )
    if not public_url:
        raise SystemExit(
            "TELEGRAM_MCP_PUBLIC_URL must be set when TELEGRAM_MCP_TRANSPORT=http "
            "(the externally reachable HTTPS base URL, e.g. https://tg-mcp.example.com)"
        )
    store = OAuthStore(db_path)
    return SingleUserOAuthProvider(
        username=username,
        password=password,
        public_url=public_url,
        store=store,
    )


def build_auth_settings(public_url: str) -> AuthSettings:
    """Build ``AuthSettings`` pinned to the externally reachable base URL."""
    base = public_url.rstrip("/")
    return AuthSettings(
        issuer_url=AnyHttpUrl(base),
        resource_server_url=AnyHttpUrl(f"{base}/mcp"),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=None,
            default_scopes=None,
        ),
        revocation_options=RevocationOptions(enabled=True),
    )
