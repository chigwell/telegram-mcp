import pytest

from telegram_mcp import runner


class _FakeClient:
    def __init__(self, *, authorized: bool):
        self.authorized = authorized
        self.connected = False
        self.started = False

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def start(self):
        self.started = True


@pytest.mark.asyncio
async def test_connect_authorized_client_uses_existing_session_without_interactive_start():
    client = _FakeClient(authorized=True)

    await runner._connect_authorized_client("default", client)

    assert client.connected is True
    assert client.started is False


@pytest.mark.asyncio
async def test_connect_authorized_client_rejects_unauthorized_session():
    client = _FakeClient(authorized=False)

    with pytest.raises(RuntimeError, match="Interactive phone login is disabled"):
        await runner._connect_authorized_client("default", client)

    assert client.connected is True
    assert client.started is False


class _FakeSettings:
    def __init__(self):
        self.host = None
        self.port = None
        self.transport_security = None


class _FakeMcp:
    def __init__(self):
        self.settings = _FakeSettings()
        self.ran = None

    async def run_stdio_async(self):
        self.ran = "stdio"

    async def run_sse_async(self):
        self.ran = "sse"

    async def run_streamable_http_async(self):
        self.ran = "http"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["stdio", "unknown"])
async def test_serve_defaults_to_stdio(monkeypatch, transport):
    fake = _FakeMcp()
    monkeypatch.setattr(runner, "mcp", fake)

    await runner._serve(transport)

    assert fake.ran == "stdio"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "sse"])
async def test_serve_http_transports_bind_host_and_port(monkeypatch, transport):
    fake = _FakeMcp()
    monkeypatch.setattr(runner, "mcp", fake)
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("MCP_PORT", "9000")

    await runner._serve(transport)

    assert fake.ran == transport
    assert fake.settings.host == "0.0.0.0"
    assert fake.settings.port == 9000


@pytest.mark.asyncio
async def test_serve_http_uses_default_host_and_port(monkeypatch):
    fake = _FakeMcp()
    monkeypatch.setattr(runner, "mcp", fake)
    monkeypatch.delenv("MCP_HOST", raising=False)
    monkeypatch.delenv("MCP_PORT", raising=False)

    await runner._serve("http")

    assert fake.ran == "http"
    assert fake.settings.host == "127.0.0.1"
    assert fake.settings.port == 8765


@pytest.mark.asyncio
async def test_serve_http_leaves_transport_security_unset_by_default(monkeypatch):
    fake = _FakeMcp()
    monkeypatch.setattr(runner, "mcp", fake)
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("MCP_ALLOWED_ORIGINS", raising=False)

    await runner._serve("http")

    assert fake.settings.transport_security is None


@pytest.mark.asyncio
async def test_serve_http_configures_allowed_hosts(monkeypatch):
    fake = _FakeMcp()
    monkeypatch.setattr(runner, "mcp", fake)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.com, localhost:8765")
    monkeypatch.setenv("MCP_ALLOWED_ORIGINS", "https://mcp.example.com")

    await runner._serve("http")

    security = fake.settings.transport_security
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["mcp.example.com", "localhost:8765"]
    assert security.allowed_origins == ["https://mcp.example.com"]
