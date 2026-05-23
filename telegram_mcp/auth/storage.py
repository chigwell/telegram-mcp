"""SQLite-backed storage for the OAuth provider.

The MCP SDK's OAuth provider has a handful of small key/value lookups
(clients by id, auth codes by string, access/refresh tokens by string).
For a single-user, low-traffic server SQLite is more than enough -- it
buys us persistence across container restarts so Claude Desktop's
connector doesn't have to re-run the OAuth flow every time the server
bounces.

Schema is intentionally minimal: each row stores the Pydantic model's
JSON serialization, so changes to the SDK's model shape don't require
migrations. We only index on the columns we query (primary keys and
``expires_at`` for cleanup).

All access goes through synchronous methods that are called from async
code via ``asyncio.to_thread`` (see :class:`SingleUserOAuthProvider`).
This keeps the implementation simple and avoids pulling in aiosqlite
when the workload is a few queries per OAuth round-trip.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional, Union

from mcp.server.auth.provider import AccessToken, AuthorizationCode, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull


SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    info_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code        TEXT PRIMARY KEY,
    entry_json  TEXT NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_codes_expires_idx
    ON auth_codes(expires_at);
CREATE TABLE IF NOT EXISTS access_tokens (
    token       TEXT PRIMARY KEY,
    entry_json  TEXT NOT NULL,
    expires_at  INTEGER
);
CREATE INDEX IF NOT EXISTS access_tokens_expires_idx
    ON access_tokens(expires_at);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token              TEXT PRIMARY KEY,
    entry_json         TEXT NOT NULL,
    expires_at         INTEGER,
    access_token       TEXT
);
CREATE INDEX IF NOT EXISTS refresh_tokens_expires_idx
    ON refresh_tokens(expires_at);
"""


class OAuthStore:
    """SQLite-backed key/value store for OAuth provider state."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        # ``check_same_thread=False`` so we can reuse one connection across
        # the asyncio worker threads. We still guard with a Lock because
        # SQLite's default mode serializes writes per connection.
        if db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._cursor() as cur:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.executescript(SCHEMA)

    @contextmanager
    def _cursor(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- clients ------------------------------------------------------------

    def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT info_json FROM clients WHERE client_id = ?",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["info_json"])

    def put_client(self, info: OAuthClientInformationFull) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO clients(client_id, info_json) VALUES (?, ?)",
                (info.client_id, info.model_dump_json()),
            )

    # -- authorization codes ------------------------------------------------

    def get_auth_code(self, code: str) -> Optional[AuthorizationCode]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT entry_json FROM auth_codes WHERE code = ?",
                (code,),
            ).fetchone()
        if row is None:
            return None
        return AuthorizationCode.model_validate_json(row["entry_json"])

    def put_auth_code(self, entry: AuthorizationCode) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO auth_codes(code, entry_json, expires_at) "
                "VALUES (?, ?, ?)",
                (entry.code, entry.model_dump_json(), float(entry.expires_at)),
            )

    def delete_auth_code(self, code: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM auth_codes WHERE code = ?", (code,))

    # -- access tokens ------------------------------------------------------

    def get_access_token(self, token: str) -> Optional[AccessToken]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT entry_json FROM access_tokens WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        return AccessToken.model_validate_json(row["entry_json"])

    def put_access_token(self, entry: AccessToken) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO access_tokens(token, entry_json, expires_at) "
                "VALUES (?, ?, ?)",
                (entry.token, entry.model_dump_json(), entry.expires_at),
            )

    def delete_access_token(self, token: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM access_tokens WHERE token = ?", (token,))

    # -- refresh tokens -----------------------------------------------------

    def get_refresh_token(self, token: str) -> Optional[RefreshToken]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT entry_json FROM refresh_tokens WHERE token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        return RefreshToken.model_validate_json(row["entry_json"])

    def put_refresh_token(
        self, entry: RefreshToken, access_token: Optional[str] = None
    ) -> None:
        with self._cursor() as cur:
            cur.execute(
                "INSERT OR REPLACE INTO refresh_tokens"
                "(token, entry_json, expires_at, access_token) "
                "VALUES (?, ?, ?, ?)",
                (
                    entry.token,
                    entry.model_dump_json(),
                    entry.expires_at,
                    access_token,
                ),
            )

    def get_access_token_for_refresh(self, refresh_token: str) -> Optional[str]:
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT access_token FROM refresh_tokens WHERE token = ?",
                (refresh_token,),
            ).fetchone()
        return row["access_token"] if row else None

    def delete_refresh_token(self, token: str) -> None:
        with self._cursor() as cur:
            cur.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))

    # -- generic ------------------------------------------------------------

    def revoke(self, token: str) -> None:
        """Delete a token regardless of which table it lives in."""
        with self._cursor() as cur:
            cur.execute("DELETE FROM access_tokens WHERE token = ?", (token,))
            cur.execute("DELETE FROM refresh_tokens WHERE token = ?", (token,))

    def gc(self, now: Optional[float] = None) -> None:
        """Best-effort cleanup of expired rows. Cheap to call periodically."""
        ts = now if now is not None else time.time()
        with self._cursor() as cur:
            cur.execute("DELETE FROM auth_codes WHERE expires_at < ?", (ts,))
            cur.execute(
                "DELETE FROM access_tokens "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (int(ts),),
            )
            cur.execute(
                "DELETE FROM refresh_tokens "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (int(ts),),
            )
