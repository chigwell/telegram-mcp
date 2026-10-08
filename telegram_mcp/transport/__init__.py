"""Transport layer and MCP protocol negotiation for telegram-mcp."""

from telegram_mcp.transport.protocol import (
    DEFAULT_PROTOCOL_VERSION,
    HANDSHAKE_PROTOCOL_VERSIONS,
    LATEST_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    MODERN_PROTOCOL_VERSIONS,
    OLDEST_SUPPORTED_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    UnsupportedProtocolVersionError,
    install_protocol_compatibility,
    is_supported_protocol_version,
    negotiate_protocol_version,
)
from telegram_mcp.transport.http import (
    ProtocolNegotiationMiddleware,
    create_streamable_http_app,
    run_streamable_http_async,
)
from telegram_mcp.transport.stdio import run_stdio_async

# Ensure runtime patches are applied upon transport import
install_protocol_compatibility()

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "HANDSHAKE_PROTOCOL_VERSIONS",
    "LATEST_PROTOCOL_VERSION",
    "MCP_PROTOCOL_VERSION_HEADER",
    "MODERN_PROTOCOL_VERSIONS",
    "OLDEST_SUPPORTED_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "UnsupportedProtocolVersionError",
    "install_protocol_compatibility",
    "is_supported_protocol_version",
    "negotiate_protocol_version",
    "ProtocolNegotiationMiddleware",
    "create_streamable_http_app",
    "run_streamable_http_async",
    "run_stdio_async",
]
