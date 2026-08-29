"""Construction of ``TelegramClient`` instances: credentials, proxy, identity.

Split out of ``runtime`` so a client can be built without importing the MCP
server. ``runtime`` discovers accounts and exits the process when none is
configured, which is right for the server and wrong for every other caller -
the export CLI authorises its own session and has no server session at all.

Credentials are read at call time rather than at import, so importing this
module never fails and error messages surface where they can be reported.
"""

import os
from typing import Any, Optional

from telethon import TelegramClient

from telegram_mcp.client_identity import client_identity_kwargs
from telegram_mcp.errors import ValidationError

PROXY_TYPES_SOCKS_HTTP = {"socks5", "socks4", "http"}
PROXY_TYPES_ALL = PROXY_TYPES_SOCKS_HTTP | {"mtproxy"}


def api_credentials() -> tuple[int, str]:
    """``(api_id, api_hash)`` from the environment, with a readable failure."""
    api_id = (os.getenv("TELEGRAM_API_ID") or "").strip()
    api_hash = (os.getenv("TELEGRAM_API_HASH") or "").strip()
    if not api_id or not api_hash:
        raise ValidationError(
            "TELEGRAM_API_ID and TELEGRAM_API_HASH are required. Set them in .env "
            "or in the environment."
        )
    try:
        return int(api_id), api_hash
    except ValueError as exc:
        raise ValidationError(f"TELEGRAM_API_ID must be an integer, got '{api_id}'.") from exc


def get_proxy_env(name: str, label: str) -> Optional[str]:
    """Resolve a TELEGRAM_PROXY_* env var with optional ``_<LABEL>`` suffix.

    Per-account values override the unsuffixed defaults so a global proxy can
    coexist with per-label overrides.
    """
    suffixed = os.getenv(f"TELEGRAM_PROXY_{name}_{label.upper()}")
    if suffixed:
        return suffixed
    return os.getenv(f"TELEGRAM_PROXY_{name}") or None


def parse_bool_env(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_proxy_for_label(label: str) -> tuple[Optional[Any], Optional[Any]]:
    """Return ``(proxy, connection)`` kwargs for ``TelegramClient`` for a label.

    Reads ``TELEGRAM_PROXY_*`` env vars (with optional ``_<LABEL>`` suffix).
    Returns ``(None, None)`` when no proxy is configured. Raises
    :class:`ValidationError` for malformed configuration so the caller fails
    fast instead of silently bypassing the proxy.
    """
    proxy_type = get_proxy_env("TYPE", label)
    if not proxy_type:
        return None, None

    proxy_type = proxy_type.strip().lower()
    if proxy_type not in PROXY_TYPES_ALL:
        raise ValidationError(
            f"Invalid TELEGRAM_PROXY_TYPE '{proxy_type}'. "
            f"Expected one of: {', '.join(sorted(PROXY_TYPES_ALL))}."
        )

    host = get_proxy_env("HOST", label)
    port_raw = get_proxy_env("PORT", label)
    if not host or not port_raw:
        raise ValidationError(
            "TELEGRAM_PROXY_HOST and TELEGRAM_PROXY_PORT are required when "
            "TELEGRAM_PROXY_TYPE is set."
        )
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValidationError(
            f"TELEGRAM_PROXY_PORT must be an integer, got '{port_raw}'."
        ) from exc

    if proxy_type == "mtproxy":
        secret = get_proxy_env("SECRET", label)
        if not secret:
            raise ValidationError("TELEGRAM_PROXY_SECRET is required for mtproxy.")
        try:
            from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
        except ImportError as exc:  # pragma: no cover - defensive guard
            raise ValidationError(
                "Telethon MTProxy connection class is unavailable; upgrade telethon."
            ) from exc
        return (host, port, secret), ConnectionTcpMTProxyRandomizedIntermediate

    # SOCKS4/SOCKS5/HTTP via python-socks (Telethon's optional dependency).
    try:
        import python_socks  # noqa: F401
    except ImportError as exc:
        raise ValidationError(
            f"Proxy type '{proxy_type}' requires the 'python-socks' package. "
            "Install it with `pip install python-socks` or `uv sync --extra proxy`."
        ) from exc

    proxy: dict[str, Any] = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "rdns": parse_bool_env(get_proxy_env("RDNS", label), default=True),
    }
    username = get_proxy_env("USERNAME", label)
    password = get_proxy_env("PASSWORD", label)
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy, None


def build_client(session: Any, label: str) -> TelegramClient:
    """Construct a ``TelegramClient`` honoring per-label proxy configuration."""
    proxy, connection = build_proxy_for_label(label)
    kwargs: dict[str, Any] = {}
    if proxy is not None:
        kwargs["proxy"] = proxy
    if connection is not None:
        kwargs["connection"] = connection
    kwargs.update(client_identity_kwargs())
    api_id, api_hash = api_credentials()
    return TelegramClient(session, api_id, api_hash, **kwargs)
