"""Application entrypoints for the Telegram MCP server."""

from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:
    raise SystemExit(str(exc)) from None

from telethon.errors import AuthKeyDuplicatedError

from telegram_mcp import runtime as _runtime
from telegram_mcp.runtime import *
from telegram_mcp.singleton import (
    DEFAULT_GRACE_SECONDS,
    SessionLock,
    SessionLockError,
    session_identity,
)
import telegram_mcp.tools  # noqa: F401 - registers MCP tools via decorators

# Populated as each account's session lock is acquired; released in _main's
# finally block so a lock is never held past this process's lifetime.
_session_locks: dict[str, SessionLock] = {}


def _lock_grace_seconds() -> float:
    raw = os.getenv("TELEGRAM_LOCK_GRACE_SECONDS")
    if not raw:
        return DEFAULT_GRACE_SECONDS
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_GRACE_SECONDS


async def _connect_authorized_client(label, client) -> None:
    # First, prevent our own duplicate-spawn case outright: an exclusive
    # per-session lock means a second instance of this server never even
    # attempts to connect while another instance already holds the same
    # session (see telegram_mcp/singleton.py for why and how).
    lock = SessionLock(label, session_identity(client))
    await asyncio.to_thread(lock.acquire, grace_seconds=_lock_grace_seconds())
    _session_locks[label] = lock

    # Once we hold the lock, still tolerate a transient AuthKeyDuplicatedError
    # from Telegram itself (e.g. the same session briefly seen from two IPs
    # during a VPN reconnect) with a bounded retry, since that's not caused by
    # a second instance of this server and a blip shouldn't take the server
    # down. Give each concurrent client its own session (TELEGRAM_SESSION_STRINGS
    # pool or TELEGRAM_SESSION_STRING_<LABEL>) to avoid the collision entirely.
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            await client.connect()
            break
        except AuthKeyDuplicatedError:
            if attempt >= max_attempts:
                raise
            delay = min(2**attempt, 15)
            print(
                f"AuthKeyDuplicatedError connecting '{label}' (attempt "
                f"{attempt}/{max_attempts}): session in use from another IP. "
                f"Retrying in {delay}s. If this persists, give each concurrent "
                "client its own session via TELEGRAM_SESSION_STRINGS or "
                "TELEGRAM_SESSION_STRING_<LABEL>.",
                file=sys.stderr,
            )
            try:
                await client.disconnect()
            except Exception:
                pass
            await asyncio.sleep(delay)

    if await client.is_user_authorized():
        return

    raise RuntimeError(
        f"Telegram client '{label}' is not authorized. Interactive phone login "
        "is disabled for the MCP server because it runs over stdio. Generate a "
        "session string with `uv run session_string_generator.py`, then set "
        "TELEGRAM_SESSION_STRING or TELEGRAM_SESSION_STRING_<LABEL> in .env. "
        "For existing file sessions, run the login outside the MCP server first."
    )


def _configure_transport_security() -> None:
    """Wire MCP_ALLOWED_HOSTS/MCP_ALLOWED_ORIGINS into FastMCP's DNS-rebinding
    protection, e.g. when the server sits behind a reverse proxy on a public
    domain instead of only being reached via 127.0.0.1/localhost.
    """
    raw_hosts = os.getenv("MCP_ALLOWED_HOSTS", "")
    allowed_hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
    if not allowed_hosts:
        return

    from mcp.server.transport_security import TransportSecuritySettings

    raw_origins = os.getenv("MCP_ALLOWED_ORIGINS", "")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


async def _serve(transport: str) -> None:
    """Run the MCP server on the selected transport.

    HTTP transports let one long-lived process hold a single shared Telegram
    connection while multiple local MCP clients connect over HTTP, instead of
    each client spawning its own Telethon session (which Telegram
    throttles/flags). "http" is streamable HTTP — the current MCP transport
    that Claude Code (`--transport http`) and Codex (`--url`) speak natively;
    "sse" is kept for clients that only support the legacy SSE transport.
    """
    if transport in ("http", "sse"):
        mcp.settings.host = os.getenv("MCP_HOST", "127.0.0.1")
        mcp.settings.port = int(os.getenv("MCP_PORT", "8765"))
        _configure_transport_security()
        if transport == "http":
            await mcp.run_streamable_http_async()
        else:
            await mcp.run_sse_async()
    else:
        # Use the asynchronous entrypoint instead of mcp.run()
        await mcp.run_stdio_async()


async def _main() -> None:
    try:
        labels = ", ".join(clients.keys())
        print(f"Starting {len(clients)} Telegram client(s) ({labels})...", file=sys.stderr)
        await asyncio.gather(
            *(_connect_authorized_client(label, cl) for label, cl in clients.items())
        )

        # Warm entity caches — StringSession has no persistent cache,
        # so fetch all dialogs once per client to populate them.
        # Runs in background: blocking startup on this (e.g. under a
        # GetDialogsRequest flood wait) makes MCP clients time out, and
        # resolve_entity() re-warms the cache on miss anyway.
        print("Warming entity caches (background)...", file=sys.stderr)

        async def _warm_caches() -> None:
            try:
                await asyncio.gather(*(cl.get_dialogs() for cl in clients.values()))
                print("Entity caches warmed.", file=sys.stderr)
            except Exception as warm_exc:
                print(f"Entity cache warm failed: {warm_exc}", file=sys.stderr)

        warm_task = asyncio.create_task(_warm_caches())

        transport = os.getenv("MCP_TRANSPORT", "stdio").lower()
        print(
            f"Telegram client(s) started ({labels}). Running MCP server ({transport})...",
            file=sys.stderr,
        )
        await _serve(transport)
    except Exception as e:
        print(f"Error starting client: {e}", file=sys.stderr)
        if isinstance(e, sqlite3.OperationalError) and "database is locked" in str(e):
            print(
                "Database lock detected. Please ensure no other instances are running.",
                file=sys.stderr,
            )
        elif isinstance(e, SessionLockError):
            print(
                "Another instance of this MCP server already holds this Telegram "
                "session (e.g. the client restarted the connector without the old "
                "process exiting yet). This instance is exiting instead of "
                "connecting a second time, which would risk Telegram invalidating "
                "the session for both. Retry once the other instance is gone.",
                file=sys.stderr,
            )
        sys.exit(1)
    finally:
        try:
            await asyncio.gather(
                *(cl.disconnect() for cl in clients.values()), return_exceptions=True
            )
        except Exception:
            pass
        for lock in _session_locks.values():
            lock.release()
        _session_locks.clear()


def main() -> None:
    _configure_allowed_roots_from_cli(sys.argv[1:])
    _runtime._apply_exposed_tools_mode()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
