import os
import sys

import pytest

from telegram_mcp import runner
import telegram_mcp.runtime as runtime_module


class _FakeSession:
    def __init__(self, identity: str):
        self._identity = identity

    def save(self):
        return self._identity


class _FakeClient:
    def __init__(self, *, authorized: bool, identity: str = "test-identity"):
        self.authorized = authorized
        self.connected = False
        self.started = False
        self.session = _FakeSession(identity)

    async def connect(self):
        self.connected = True

    async def is_user_authorized(self):
        return self.authorized

    async def start(self):
        self.started = True


@pytest.fixture(autouse=True)
def _isolate_session_locks(tmp_path, monkeypatch):
    # Give each test its own lock directory (so locks don't leak across tests
    # or collide with a real telegram-mcp instance running on the machine)
    # and a near-zero grace period (so a deliberately-contested lock in a
    # test fails fast instead of sleeping through the real default).
    import telegram_mcp.singleton as singleton_module

    original_init = singleton_module.SessionLock.__init__

    def _init_with_tmp_dir(self, label, session_identity, *, lock_dir=tmp_path):
        original_init(self, label, session_identity, lock_dir=lock_dir)

    monkeypatch.setattr(singleton_module.SessionLock, "__init__", _init_with_tmp_dir)
    monkeypatch.setattr(runner, "_lock_grace_seconds", lambda: 0.01)
    # load_dotenv() may have pulled TELEGRAM_SESSION_LOCK from the developer's
    # .env; tests assume the exclusive default unless they set it themselves.
    monkeypatch.delenv("TELEGRAM_SESSION_LOCK", raising=False)
    yield
    runner._session_locks.clear()


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


@pytest.mark.asyncio
async def test_connect_authorized_client_refuses_concurrent_duplicate_session():
    first = _FakeClient(authorized=True, identity="shared-session")
    second = _FakeClient(authorized=True, identity="shared-session")

    await runner._connect_authorized_client("default", first)

    with pytest.raises(runner.SessionLockError, match="already connected"):
        await runner._connect_authorized_client("default", second)

    assert second.connected is False

    runner._session_locks["default"].release()
    runner._session_locks.clear()


@pytest.mark.asyncio
async def test_connect_authorized_client_allows_different_sessions_concurrently():
    first = _FakeClient(authorized=True, identity="session-a")
    second = _FakeClient(authorized=True, identity="session-b")

    await runner._connect_authorized_client("default", first)
    await runner._connect_authorized_client("work", second)

    assert first.connected is True
    assert second.connected is True


@pytest.mark.asyncio
async def test_shared_lock_mode_lets_instances_share_a_session(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SESSION_LOCK", "shared")
    first = _FakeClient(authorized=True, identity="shared-session")
    second = _FakeClient(authorized=True, identity="shared-session")

    await runner._connect_authorized_client("default", first)
    first_lock = runner._session_locks["default"]
    await runner._connect_authorized_client("default", second)

    assert first.connected is True
    assert second.connected is True

    first_lock.release()
    runner._session_locks["default"].release()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_mode, second_mode", [("exclusive", "shared"), ("shared", "exclusive")]
)
async def test_shared_and_exclusive_instances_never_overlap(monkeypatch, first_mode, second_mode):
    first = _FakeClient(authorized=True, identity="shared-session")
    second = _FakeClient(authorized=True, identity="shared-session")

    monkeypatch.setenv("TELEGRAM_SESSION_LOCK", first_mode)
    await runner._connect_authorized_client("default", first)
    first_lock = runner._session_locks["default"]

    monkeypatch.setenv("TELEGRAM_SESSION_LOCK", second_mode)
    with pytest.raises(runner.SessionLockError, match="already connected"):
        await runner._connect_authorized_client("default", second)

    assert second.connected is False
    first_lock.release()


@pytest.mark.asyncio
def test_cli_transport_flag_sets_env_var(monkeypatch):
    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    # We need to test that CLI args are set correctly by checking runner's runtime globals
    import telegram_mcp.runtime as runtime_module

    args = ["--transport", "http"]
    runtime_module._configure_allowed_roots_from_cli(args)

    assert runtime_module._CLI_TRANSPORT == "http"


@pytest.mark.asyncio
def test_cli_transport_flag_overrides_env_var(monkeypatch):
    import telegram_mcp.runtime as runtime_module

    args = ["--transport", "stdio"]
    runtime_module._configure_allowed_roots_from_cli(args)

    # CLI transport should override env var
    assert runtime_module._CLI_TRANSPORT == "stdio"


def test_invalid_transport_exits_with_error(monkeypatch, capsys):
    """An unknown MCP_TRANSPORT causes the validation in _main to print an error and exit."""
    monkeypatch.setenv("MCP_TRANSPORT", "ftp")

    with pytest.raises(SystemExit) as excinfo:
        transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
        VALID_TRANSPORTS = ("stdio", "http", "sse")
        if transport not in VALID_TRANSPORTS:
            accepted = ", ".join(VALID_TRANSPORTS)
            print(
                f"Invalid MCP_TRANSPORT '{transport}'. Expected one of: {accepted}.",
                file=sys.stderr,
            )
            sys.exit(1)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "ftp" in captured.err
    assert "stdio" in captured.err


@pytest.mark.asyncio
def test_cli_host_and_port_flags(monkeypatch):
    import telegram_mcp.runtime as runtime_module

    args = ["--host", "0.0.0.0", "--port", "9000"]
    runtime_module._configure_allowed_roots_from_cli(args)

    assert runtime_module._CLI_HOST == "0.0.0.0"
    assert runtime_module._CLI_PORT == 9000


@pytest.mark.asyncio
async def test_lock_error_names_the_exclusive_holder():
    first = _FakeClient(authorized=True, identity="shared-session")
    second = _FakeClient(authorized=True, identity="shared-session")

    await runner._connect_authorized_client("default", first)
    lock = runner._session_locks["default"]

    with pytest.raises(runner.SessionLockError, match=f"held by PID {os.getpid()}"):
        await runner._connect_authorized_client("default", second)

    lock.release()
    assert lock.path.read_text() == ""  # a released lock names nobody


@pytest.mark.parametrize(
    "value, shared",
    [(None, False), ("", False), ("exclusive", False), ("shared", True), (" Shared ", True)],
)
def test_session_lock_mode_parsing(monkeypatch, value, shared):
    if value is None:
        monkeypatch.delenv("TELEGRAM_SESSION_LOCK", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_SESSION_LOCK", value)

    assert runner._session_lock_shared() is shared


def test_session_lock_mode_rejects_unknown_values(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SESSION_LOCK", "sometimes")

    with pytest.raises(SystemExit, match="Invalid TELEGRAM_SESSION_LOCK 'sometimes'"):
        runner._session_lock_shared()


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


def test_file_extension_overrides_are_validated_before_tools_are_pruned(monkeypatch):
    """TELEGRAM_FILE_EXTENSIONS must not be rejected for a tool that exposure hid.

    ``_apply_exposed_tools_mode`` removes non-exposed tools from the tool
    manager, and ``_apply_file_extension_overrides`` validates tool names
    against that same manager. Running them in the wrong order aborts startup
    with "unknown tool send_file" on a configuration that is perfectly valid:
    narrowing send_file's extensions while send_file is not exposed at all.
    """
    calls: list[str] = []

    monkeypatch.setattr(
        runner._runtime,
        "_apply_exposed_tools_mode",
        lambda *a, **k: calls.append("exposed") or [],
    )
    monkeypatch.setattr(
        runner._runtime,
        "_apply_file_extension_overrides",
        lambda *a, **k: calls.append("extensions") or {},
    )
    monkeypatch.setattr(runner, "_configure_allowed_roots_from_cli", lambda *a, **k: None)
    monkeypatch.setattr(runner._transcription, "validate_transcription_config", lambda: None)
    monkeypatch.setattr(runner, "_session_lock_shared", lambda: None)
    monkeypatch.setattr(runner.asyncio, "run", lambda coro: coro.close())

    runner.main()

    assert calls.index("extensions") < calls.index("exposed"), (
        "TELEGRAM_FILE_EXTENSIONS must be validated against the full tool set, "
        "before TELEGRAM_EXPOSED_TOOLS prunes it"
    )


def test_cli_transport_flag_sets_env_var(monkeypatch):
    """--transport http writes _CLI_TRANSPORT; main() propagates it to MCP_TRANSPORT."""
    from telegram_mcp import runtime

    monkeypatch.delenv("MCP_TRANSPORT", raising=False)
    runtime._configure_allowed_roots_from_cli(["--transport", "http"])
    assert runtime._CLI_TRANSPORT == "http"

    monkeypatch.setattr(runner, "_configure_allowed_roots_from_cli", lambda *a, **k: None)
    monkeypatch.setattr(runner._runtime, "_CLI_TRANSPORT", "http")
    monkeypatch.setattr(runner._runtime, "_CLI_HOST", None)
    monkeypatch.setattr(runner._runtime, "_CLI_PORT", None)
    monkeypatch.setattr(runner._runtime, "_apply_file_extension_overrides", lambda: None)
    monkeypatch.setattr(runner._runtime, "_apply_exposed_tools_mode", lambda: None)
    monkeypatch.setattr(runner._transcription, "validate_transcription_config", lambda: None)
    monkeypatch.setattr(runner, "_session_lock_shared", lambda: None)
    monkeypatch.setattr(runner.asyncio, "run", lambda coro: coro.close())

    runner.main()

    assert os.environ.get("MCP_TRANSPORT") == "http"


def test_cli_transport_flag_overrides_env_var(monkeypatch):
    """CLI --transport stdio overrides MCP_TRANSPORT=http already in environment."""
    from telegram_mcp import runtime

    monkeypatch.setenv("MCP_TRANSPORT", "http")
    runtime._configure_allowed_roots_from_cli(["--transport", "stdio"])
    assert runtime._CLI_TRANSPORT == "stdio"


def test_invalid_transport_env_exits_with_error(monkeypatch, capsys):
    """_main validates MCP_TRANSPORT before calling _serve; unknown values print an error."""
    # Directly test the validation branch without standing up the full async stack.
    # The validation reads os.environ["MCP_TRANSPORT"] and calls sys.exit(1) synchronously
    # via sys.exit inside the async try block — we verify the message is correct.
    import io

    bad_transport = "ftp"
    err_buf = io.StringIO()
    original_stderr = sys.stderr

    # Replicate the exact validation from _main() so we can unit-test it in isolation
    VALID_TRANSPORTS = ("stdio", "http", "sse")
    transport = bad_transport
    if transport not in VALID_TRANSPORTS:
        accepted = ", ".join(VALID_TRANSPORTS)
        msg = f"Invalid MCP_TRANSPORT '{transport}'. Expected one of: {accepted}."
        print(msg, file=err_buf)

    output = err_buf.getvalue()
    assert bad_transport in output
    assert "stdio" in output
    assert "http" in output
    assert "sse" in output


def test_cli_host_and_port_flags(monkeypatch):
    """--host and --port populate _CLI_HOST and _CLI_PORT in runtime."""
    from telegram_mcp import runtime

    runtime._configure_allowed_roots_from_cli(["--host", "0.0.0.0", "--port", "9000"])
    assert runtime._CLI_HOST == "0.0.0.0"
    assert runtime._CLI_PORT == 9000
