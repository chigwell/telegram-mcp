"""Loopback authentication and audit helpers for the shared HTTP broker."""

from __future__ import annotations

import hmac
import json
import os
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from mcp.server.auth.provider import AccessToken


TOKEN_PREFIX = "MCP_AUTH_TOKEN_"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def load_client_tokens(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = os.environ if environ is None else environ
    tokens: dict[str, str] = {}
    for key, value in env.items():
        if not key.startswith(TOKEN_PREFIX) or key == "MCP_AUTH_TOKEN" or not value:
            continue
        client_id = key[len(TOKEN_PREFIX) :].strip().lower().replace("_", "-")
        if client_id:
            tokens[client_id] = value

    raw_json = env.get("MCP_AUTH_TOKENS", "").strip()
    if raw_json:
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP_AUTH_TOKENS must be a JSON object") from exc
        if not isinstance(parsed, dict):
            raise ValueError("MCP_AUTH_TOKENS must be a JSON object")
        for client_id, token in parsed.items():
            if not isinstance(client_id, str) or not isinstance(token, str) or not token:
                raise ValueError("MCP_AUTH_TOKENS keys and values must be non-empty strings")
            tokens[client_id] = token

    single = env.get("MCP_AUTH_TOKEN", "")
    if single:
        tokens[env.get("MCP_CLIENT_ID", "local-client")] = single

    if len(tokens.values()) != len(set(tokens.values())):
        raise ValueError("Each MCP client must have a distinct bearer token")
    return tokens


class StaticTokenVerifier:
    """Verify local bearer tokens while preserving a per-client audit identity."""

    def __init__(self, tokens: Mapping[str, str]):
        if not tokens:
            raise ValueError("At least one MCP bearer token is required")
        self._tokens = dict(tokens)

    async def verify_token(self, token: str) -> AccessToken | None:
        for client_id, expected in self._tokens.items():
            if hmac.compare_digest(token, expected):
                return AccessToken(
                    token=token,
                    client_id=client_id,
                    scopes=["telegram"],
                    subject=client_id,
                )
        return None


def require_loopback(host: str) -> None:
    normalized = host.strip().strip("[]").lower()
    if normalized not in LOOPBACK_HOSTS:
        raise ValueError(
            f"Shared Telegram MCP broker must bind to loopback, got {host!r}. "
            "Use 127.0.0.1, ::1, or localhost."
        )


def audit_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    explicit = env.get("MCP_AUDIT_FILE")
    if explicit:
        return Path(explicit).expanduser()
    state_home = Path(env.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
    return state_home / "telegram-mcp" / "audit.jsonl"


def record_audit_event(
    *,
    client_id: str,
    tool: str,
    read_only: bool,
    outcome: str,
    environ: Mapping[str, str] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "client_id": client_id,
        "tool": tool,
        "read_only": read_only,
        "outcome": outcome,
    }
    path = audit_path(environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        os.write(fd, (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)
    print(json.dumps({"telegram_mcp_audit": payload}, sort_keys=True), file=sys.stderr)
