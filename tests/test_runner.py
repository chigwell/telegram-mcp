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


def test_main_does_not_patch_the_event_loop(monkeypatch):
    """nest_asyncio.apply() must not be re-introduced into main().

    Patching the loop stops mcp.run_stdio_async() from returning when stdin
    reaches EOF, so the server outlives the MCP client that spawned it and is
    reparented to init. Every closed client session then leaks a server that
    nothing ever reaps.
    """
    applied = []
    monkeypatch.setattr(runner.nest_asyncio, "apply", lambda *a, **k: applied.append(1))

    ran = []

    def _fake_run(coro):
        coro.close()  # we are asserting on wiring, not executing the server
        ran.append(1)

    monkeypatch.setattr(runner.asyncio, "run", _fake_run)
    monkeypatch.setattr(runner, "_configure_allowed_roots_from_cli", lambda *a, **k: None)
    monkeypatch.setattr(runner._runtime, "_apply_exposed_tools_mode", lambda *a, **k: None)

    runner.main()

    assert ran == [1], "main() should still drive the server through asyncio.run"
    assert applied == [], (
        "main() called nest_asyncio.apply(); this breaks stdin-EOF shutdown "
        "and leaks an orphaned server process per client session"
    )
