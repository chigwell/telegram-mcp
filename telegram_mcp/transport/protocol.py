"""MCP protocol version registry and negotiation for Telegram MCP.

Supports MCP protocol versions:
- 2024-11-05 (legacy initial protocol)
- 2025-03-26 (first protocol revision)
- 2025-06-18 (oauth/auth revision)
- 2025-11-25 (standard v1 streamable HTTP & stdio)
- 2026-07-28 (stateless per-request envelope & modern discovery)
"""

from __future__ import annotations

import logging
from typing import Final, Sequence

logger = logging.getLogger(__name__)

SUPPORTED_PROTOCOL_VERSIONS: Final[tuple[str, ...]] = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
    "2026-07-28",
)

HANDSHAKE_PROTOCOL_VERSIONS: Final[tuple[str, ...]] = (
    "2024-11-05",
    "2025-03-26",
    "2025-06-18",
    "2025-11-25",
)

MODERN_PROTOCOL_VERSIONS: Final[tuple[str, ...]] = ("2026-07-28",)

DEFAULT_PROTOCOL_VERSION: Final[str] = "2025-11-25"
LATEST_PROTOCOL_VERSION: Final[str] = "2026-07-28"
OLDEST_SUPPORTED_VERSION: Final[str] = "2024-11-05"
MCP_PROTOCOL_VERSION_HEADER: Final[str] = "mcp-protocol-version"


class UnsupportedProtocolVersionError(ValueError):
    """Raised when an incoming client request specifies an unsupported MCP protocol version."""

    def __init__(self, requested: str, supported: Sequence[str] = SUPPORTED_PROTOCOL_VERSIONS):
        self.requested = requested
        self.supported = tuple(supported)
        msg = (
            f"Unsupported protocol version: {requested}. "
            f"Supported versions: {', '.join(supported)}"
        )
        super().__init__(msg)


def is_supported_protocol_version(version: str) -> bool:
    """Return True if the version string is recognized by this server."""
    return version in SUPPORTED_PROTOCOL_VERSIONS


def negotiate_protocol_version(requested_version: str | None) -> str:
    """Negotiate the protocol version between client offer and server support.

    If requested_version is None or empty, falls back to DEFAULT_PROTOCOL_VERSION.
    If requested_version is in SUPPORTED_PROTOCOL_VERSIONS, echoes back the requested version.
    Otherwise raises UnsupportedProtocolVersionError.
    """
    if not requested_version:
        return DEFAULT_PROTOCOL_VERSION

    clean_version = requested_version.strip()
    if clean_version in SUPPORTED_PROTOCOL_VERSIONS:
        return clean_version

    raise UnsupportedProtocolVersionError(clean_version)


_COMPATIBILITY_INSTALLED = False


def install_protocol_compatibility() -> None:
    """Patch the runtime MCP SDK to recognize MCP 2026-07-28.

    This ensures both stdio and HTTP transports validate and negotiate 2026-07-28
    requests while preserving full backward compatibility for older clients.
    """
    global _COMPATIBILITY_INSTALLED
    if _COMPATIBILITY_INSTALLED:
        return

    try:
        import mcp.shared.version

        if "2026-07-28" not in mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS:
            mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS.append("2026-07-28")
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not patch mcp.shared.version: %s", exc)

    try:
        import mcp.types

        mcp.types.LATEST_PROTOCOL_VERSION = LATEST_PROTOCOL_VERSION
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not patch mcp.types: %s", exc)

    try:
        import mcp.server.streamable_http

        if hasattr(mcp.server.streamable_http, "SUPPORTED_PROTOCOL_VERSIONS"):
            current = mcp.server.streamable_http.SUPPORTED_PROTOCOL_VERSIONS
            if "2026-07-28" not in current:
                if isinstance(current, list):
                    current.append("2026-07-28")
                else:
                    mcp.server.streamable_http.SUPPORTED_PROTOCOL_VERSIONS = list(
                        SUPPORTED_PROTOCOL_VERSIONS
                    )
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not patch mcp.server.streamable_http: %s", exc)

    try:
        import mcp.server.session

        if hasattr(mcp.server.session, "SUPPORTED_PROTOCOL_VERSIONS"):
            current = mcp.server.session.SUPPORTED_PROTOCOL_VERSIONS
            if "2026-07-28" not in current:
                if isinstance(current, list):
                    current.append("2026-07-28")
                else:
                    mcp.server.session.SUPPORTED_PROTOCOL_VERSIONS = list(
                        SUPPORTED_PROTOCOL_VERSIONS
                    )
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not patch mcp.server.session: %s", exc)

    try:
        import mcp.client.session

        if hasattr(mcp.client.session, "SUPPORTED_PROTOCOL_VERSIONS"):
            current = mcp.client.session.SUPPORTED_PROTOCOL_VERSIONS
            if "2026-07-28" not in current:
                if isinstance(current, list):
                    current.append("2026-07-28")
                else:
                    mcp.client.session.SUPPORTED_PROTOCOL_VERSIONS = list(
                        SUPPORTED_PROTOCOL_VERSIONS
                    )
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not patch mcp.client.session: %s", exc)

    try:
        from telegram_mcp.transport.http import patch_fastmcp_streamable_http

        patch_fastmcp_streamable_http()
    except Exception as exc:
        logger.debug("Could not patch FastMCP streamable_http: %s", exc)

    try:
        _install_silent_request_cancel()
    except (ImportError, AttributeError) as exc:
        logger.debug("Could not patch RequestResponder.cancel: %s", exc)

    _COMPATIBILITY_INSTALLED = True
    logger.info("MCP 2026-07-28 protocol compatibility installed successfully.")


def _install_silent_request_cancel() -> None:
    """Stop the SDK from answering requests the client cancelled.

    mcp 1.x replies to a cancelled request with an error (code 0), but the spec
    says the receiver should not respond (MUST NOT as of 2026-07-28). Strict
    clients treat that reply as a protocol violation. mcp 2.x no longer sends
    it, so this is only applied to 1.x.
    """
    from importlib.metadata import version

    from mcp.shared.session import RequestResponder

    if int(version("mcp").split(".")[0]) >= 2:
        return

    async def cancel(self) -> None:
        if not self._entered:
            raise RuntimeError("RequestResponder must be used as a context manager")
        if not self._cancel_scope:
            raise RuntimeError("No active cancel scope")

        self._cancel_scope.cancel()
        self._completed = True

    RequestResponder.cancel = cancel
