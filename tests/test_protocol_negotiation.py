"""Tests for MCP 2026-07-28 protocol negotiation, backward compatibility, and discovery."""

import pytest
from starlette.testclient import TestClient

from telegram_mcp.transport import (
    DEFAULT_PROTOCOL_VERSION,
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    UnsupportedProtocolVersionError,
    create_streamable_http_app,
    install_protocol_compatibility,
    is_supported_protocol_version,
    negotiate_protocol_version,
)
from telegram_mcp.runtime import mcp
import telegram_mcp.server as server


def test_supported_protocol_versions():
    """Verify supported protocol revisions and constants."""
    assert "2026-07-28" in SUPPORTED_PROTOCOL_VERSIONS
    assert "2025-11-25" in SUPPORTED_PROTOCOL_VERSIONS
    assert "2025-03-26" in SUPPORTED_PROTOCOL_VERSIONS
    assert "2024-11-05" in SUPPORTED_PROTOCOL_VERSIONS
    assert LATEST_PROTOCOL_VERSION == "2026-07-28"
    assert DEFAULT_PROTOCOL_VERSION == "2025-11-25"


def test_is_supported_protocol_version():
    """Verify version membership checks."""
    assert is_supported_protocol_version("2026-07-28") is True
    assert is_supported_protocol_version("2025-11-25") is True
    assert is_supported_protocol_version("2024-11-05") is True
    assert is_supported_protocol_version("invalid-version") is False
    assert is_supported_protocol_version("2099-01-01") is False


def test_negotiate_protocol_version():
    """Verify version negotiation logic."""
    assert negotiate_protocol_version(None) == DEFAULT_PROTOCOL_VERSION
    assert negotiate_protocol_version("") == DEFAULT_PROTOCOL_VERSION
    assert negotiate_protocol_version("2026-07-28") == "2026-07-28"
    assert negotiate_protocol_version("2025-11-25") == "2025-11-25"
    assert negotiate_protocol_version("2025-03-26") == "2025-03-26"
    assert negotiate_protocol_version("2024-11-05") == "2024-11-05"

    with pytest.raises(UnsupportedProtocolVersionError) as exc_info:
        negotiate_protocol_version("2099-01-01")
    assert "Unsupported protocol version: 2099-01-01" in str(exc_info.value)


def test_sdk_runtime_patch_applied():
    """Verify that install_protocol_compatibility patched SDK globals."""
    install_protocol_compatibility()

    import mcp.shared.version
    import mcp.types
    import mcp.server.streamable_http
    import mcp.server.session

    assert "2026-07-28" in mcp.shared.version.SUPPORTED_PROTOCOL_VERSIONS
    assert mcp.types.LATEST_PROTOCOL_VERSION == "2026-07-28"
    assert "2026-07-28" in mcp.server.streamable_http.SUPPORTED_PROTOCOL_VERSIONS
    assert "2026-07-28" in mcp.server.session.SUPPORTED_PROTOCOL_VERSIONS


def test_server_module_exports():
    """Verify telegram_mcp.server public module exports."""
    assert server.get_server() is mcp
    assert server.LATEST_PROTOCOL_VERSION == "2026-07-28"
    assert callable(server.get_http_app)
    assert callable(server.serve)
    assert callable(server.main)


def test_http_transport_negotiation_2026_07_28():
    """Verify clients requesting 2026-07-28 negotiate 2026-07-28 successfully over HTTP."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2026-07-28",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2026-07-28",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert response.status_code == 200
        assert response.headers.get("mcp-protocol-version") == "2026-07-28"
        assert '"protocolVersion":"2026-07-28"' in response.text


def test_http_transport_backwards_compatibility_2025_11_25():
    """Verify older clients requesting 2025-11-25 negotiate 2025-11-25 over HTTP."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2025-11-25",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
        )
        assert response.status_code == 200
        assert response.headers.get("mcp-protocol-version") == "2025-11-25"
        assert '"protocolVersion":"2025-11-25"' in response.text


def test_http_transport_backwards_compatibility_2024_11_05():
    """Verify legacy clients requesting 2024-11-05 negotiate 2024-11-05 over HTTP."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2024-11-05",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "legacy-client", "version": "0.1"},
                },
            },
        )
        assert response.status_code == 200
        assert response.headers.get("mcp-protocol-version") == "2024-11-05"
        assert '"protocolVersion":"2024-11-05"' in response.text


def test_http_transport_unsupported_protocol_rejected():
    """Verify unsupported protocol versions receive HTTP 400 with supported versions list."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2099-01-01",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert "error" in data
        assert "Unsupported protocol version: 2099-01-01" in data["error"]["message"]
        assert "2026-07-28" in data["error"]["message"]


def test_http_transport_options_preflight():
    """Verify OPTIONS preflight returns 204 with protocol and CORS headers."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.options("/mcp", headers={"MCP-Protocol-Version": "2026-07-28"})
        assert response.status_code == 204
        assert response.headers.get("mcp-protocol-version") == "2026-07-28"
        assert "OPTIONS" in response.headers.get("allow", "")
        assert response.headers.get("access-control-allow-origin") == "*"


def test_http_transport_discovery_get_probe():
    """Verify plain GET discovery probe returns JSON server metadata."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.get(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2026-07-28",
                "Accept": "application/json",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "telegram"
        assert "2026-07-28" in data["protocolVersions"]
        assert data["latestProtocolVersion"] == "2026-07-28"
        assert response.headers.get("mcp-protocol-version") == "2026-07-28"


def test_http_transport_modern_stateless_tools_list():
    """Verify modern 2026-07-28 stateless call to tools/list executes cleanly."""
    app = create_streamable_http_app(mcp)
    with TestClient(app, base_url="http://localhost:8765") as client:
        response = client.post(
            "/mcp",
            headers={
                "MCP-Protocol-Version": "2026-07-28",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/list",
                "params": {},
            },
        )
        assert response.status_code == 200
        assert response.headers.get("mcp-protocol-version") == "2026-07-28"
        assert '"tools":' in response.text


@pytest.mark.asyncio
async def test_session_handshake_protocol_negotiation():
    """Verify ServerSession handshake negotiates 2026-07-28, 2025-11-25, 2024-11-05 cleanly."""
    from unittest.mock import AsyncMock, MagicMock
    from mcp.server.session import ServerSession
    from mcp.server.models import InitializationOptions
    from mcp.shared.session import RequestResponder
    from mcp.types import (
        ClientCapabilities,
        ClientRequest,
        Implementation,
        InitializeRequest,
        InitializeRequestParams,
        ServerCapabilities,
    )

    install_protocol_compatibility()

    init_options = InitializationOptions(
        server_name="telegram",
        server_version="2.0.1",
        capabilities=ServerCapabilities(),
    )

    for version in ("2026-07-28", "2025-11-25", "2024-11-05"):
        mock_read = MagicMock()
        mock_write = AsyncMock()

        session = ServerSession(
            read_stream=mock_read,
            write_stream=mock_write,
            init_options=init_options,
        )

        req = InitializeRequest(
            method="initialize",
            params=InitializeRequestParams(
                protocolVersion=version,
                capabilities=ClientCapabilities(),
                clientInfo=Implementation(name="test", version="1.0"),
            ),
        )

        responder = RequestResponder(
            request_id="1",
            request_meta=None,
            request=ClientRequest(req),
            session=session,
            on_complete=lambda r: None,
        )

        await session._received_request(responder)
        assert mock_write.send.called
        sent_session_msg = mock_write.send.call_args[0][0]
        result_dict = sent_session_msg.message.root.result
        assert result_dict["protocolVersion"] == version
