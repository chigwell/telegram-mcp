import asyncio
import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolRequest, ToolAnnotations

from telegram_mcp import broker_security, runtime


def test_load_client_tokens_preserves_per_client_identity():
    tokens = broker_security.load_client_tokens(
        {
            "MCP_AUTH_TOKEN_CODEX": "codex-secret",
            "MCP_AUTH_TOKEN_CLAUDE_CODE": "claude-secret",
        }
    )
    assert tokens == {"codex": "codex-secret", "claude-code": "claude-secret"}


def test_load_client_tokens_rejects_shared_token():
    with pytest.raises(ValueError, match="distinct bearer token"):
        broker_security.load_client_tokens(
            {"MCP_AUTH_TOKEN_CODEX": "same", "MCP_AUTH_TOKEN_CLAUDE": "same"}
        )


@pytest.mark.asyncio
async def test_static_token_verifier_authenticates_and_identifies_client():
    verifier = broker_security.StaticTokenVerifier({"codex": "secret-a"})
    accepted = await verifier.verify_token("secret-a")
    rejected = await verifier.verify_token("wrong")

    assert accepted is not None
    assert accepted.client_id == "codex"
    assert rejected is None


@pytest.mark.asyncio
async def test_two_authenticated_clients_initialize_against_one_http_broker():
    verifier = broker_security.StaticTokenVerifier(
        {"codex": "codex-secret", "claude": "claude-secret"}
    )
    server = FastMCP(
        "test-broker",
        stateless_http=True,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url="http://127.0.0.1:18765",
            resource_server_url="http://127.0.0.1:18765/mcp",
            required_scopes=["telegram"],
        ),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:18765"],
            allowed_origins=[],
        ),
    )
    app = server.streamable_http_app()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    base_headers = {
        "accept": "application/json, text/event-stream",
        "content-type": "application/json",
    }
    lifespan_app = getattr(app, "app", app)
    async with lifespan_app.router.lifespan_context(lifespan_app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://127.0.0.1:18765",
        ) as client:
            unauthorized = await client.post("/mcp", headers=base_headers, json=body)
            responses = await asyncio.gather(
                client.post(
                    "/mcp",
                    headers={**base_headers, "authorization": "Bearer codex-secret"},
                    json=body,
                ),
                client.post(
                    "/mcp",
                    headers={**base_headers, "authorization": "Bearer claude-secret"},
                    json={**body, "id": 2},
                ),
            )

    assert unauthorized.status_code == 401
    assert [response.status_code for response in responses] == [200, 200]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_loopback_hosts_are_accepted(host):
    broker_security.require_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.2", "mcp.example.com"])
def test_non_loopback_hosts_are_rejected(host):
    with pytest.raises(ValueError, match="must bind to loopback"):
        broker_security.require_loopback(host)


def test_audit_file_contains_identity_and_no_arguments(tmp_path):
    path = tmp_path / "audit.jsonl"
    broker_security.record_audit_event(
        client_id="codex",
        tool="search_messages",
        read_only=True,
        outcome="completed",
        environ={"MCP_AUDIT_FILE": str(path)},
    )
    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["client_id"] == "codex"
    assert event["tool"] == "search_messages"
    assert "arguments" not in event
    assert path.stat().st_mode & 0o777 == 0o600


class _ToolManager:
    def __init__(self, read_only):
        self._read_only = read_only

    def get_tool(self, _name):
        return SimpleNamespace(annotations=ToolAnnotations(readOnlyHint=self._read_only))


class _Server:
    def __init__(self, read_only):
        self._tool_manager = _ToolManager(read_only)


@pytest.mark.asyncio
async def test_mutating_calls_are_serialized_and_audited_per_client(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_BROKER_MODE", "1")
    monkeypatch.setenv("MCP_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    active = 0
    peak = 0

    async def handler(_request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    request = CallToolRequest(params={"name": "save_draft", "arguments": {}})
    access = AccessToken(token="unused", client_id="claude", scopes=["telegram"])
    context = auth_context_var.set(AuthenticatedUser(access))
    try:
        await asyncio.gather(
            runtime._call_tool_with_audit(request, handler, server=_Server(False)),
            runtime._call_tool_with_audit(request, handler, server=_Server(False)),
        )
    finally:
        auth_context_var.reset(context)

    assert peak == 1
    events = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert {event["client_id"] for event in events} == {"claude"}


@pytest.mark.asyncio
async def test_read_only_calls_remain_concurrent(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_BROKER_MODE", "1")
    monkeypatch.setenv("MCP_AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    active = 0
    peak = 0

    async def handler(_request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "ok"

    request = CallToolRequest(params={"name": "search_messages", "arguments": {}})
    await asyncio.gather(
        runtime._call_tool_with_audit(request, handler, server=_Server(True)),
        runtime._call_tool_with_audit(request, handler, server=_Server(True)),
    )
    assert peak == 2


@pytest.mark.asyncio
async def test_legacy_mode_does_not_write_broker_audit(tmp_path, monkeypatch):
    audit_file = tmp_path / "audit.jsonl"
    monkeypatch.delenv("MCP_BROKER_MODE", raising=False)
    monkeypatch.setenv("MCP_AUDIT_FILE", str(audit_file))

    async def handler(_request):
        return "ok"

    request = CallToolRequest(params={"name": "search_messages", "arguments": {}})
    result = await runtime._call_tool_with_audit(request, handler, server=_Server(True))

    assert result == "ok"
    assert not audit_file.exists()
