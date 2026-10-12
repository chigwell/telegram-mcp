"""Application entrypoints for the Telegram MCP server."""

from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:
    raise SystemExit(str(exc)) from None

import sys
from telethon.errors import AuthKeyDuplicatedError, BotMethodInvalidError
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

# Ensure sys.stderr is reconfigured for UTF-8 on Windows where possible
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

from telegram_mcp import runtime as _runtime
from telegram_mcp import transcription as _transcription
from telegram_mcp.runtime import *
from telegram_mcp.singleton import (
    DEFAULT_GRACE_SECONDS,
    SessionLock,
    SessionLockError,
    session_identity,
)
from telegram_mcp.broker_security import (
    StaticTokenVerifier,
    load_client_tokens,
    record_audit_event,
    require_loopback,
)
from telegram_mcp.connection_guard import connection_status
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


_SESSION_LOCK_MODES = ("exclusive", "shared")


def _session_lock_shared() -> bool:
    """``TELEGRAM_SESSION_LOCK``: exclusive (default) | shared.

    ``exclusive`` refuses to start while another instance on this host holds
    the same session. ``shared`` takes the lock in shared mode instead, so any
    number of instances on this host can use one session -- safe only when
    they all reach Telegram from the same IP, since Telegram invalidates a
    session it sees from two IPs at once. Shared and exclusive instances never
    overlap, so the default keeps its guarantee.
    """
    raw = (os.getenv("TELEGRAM_SESSION_LOCK") or "exclusive").strip().lower()
    if raw not in _SESSION_LOCK_MODES:
        accepted = ", ".join(_SESSION_LOCK_MODES)
        raise SystemExit(f"Invalid TELEGRAM_SESSION_LOCK '{raw}'. Expected one of: {accepted}.")
    return raw == "shared"


def _normalize_username(value) -> str:
    return (value or "").strip().lstrip("@").casefold()


def _expected_username(label: str) -> str:
    """TELEGRAM_EXPECTED_USERNAME_<LABEL>, else TELEGRAM_EXPECTED_USERNAME; "" if unset."""
    raw = os.getenv(f"TELEGRAM_EXPECTED_USERNAME_{label.upper()}") or os.getenv(
        "TELEGRAM_EXPECTED_USERNAME"
    )
    return _normalize_username(raw)


async def _verify_expected_username(label: str, client) -> None:
    """Refuse to serve a session logged in to a different account."""
    expected = _expected_username(label)
    if not expected:
        return

    me = await client.get_me()
    usernames = {_normalize_username(getattr(me, "username", None))}
    for entry in getattr(me, "usernames", None) or []:
        if getattr(entry, "active", False):
            usernames.add(_normalize_username(getattr(entry, "username", None)))
    usernames.discard("")

    if expected in usernames:
        return

    try:
        await client.disconnect()
    except Exception:
        pass
    raise RuntimeError(
        f"Telegram client '{label}' is logged in to a different account than "
        f"TELEGRAM_EXPECTED_USERNAME_{label.upper()} / TELEGRAM_EXPECTED_USERNAME expects."
    )


async def _connect_authorized_client(label, client) -> None:
    # First, prevent our own duplicate-spawn case outright: a per-session lock
    # means a second instance of this server never even attempts to connect
    # while another instance already holds the same session (see
    # telegram_mcp/singleton.py for why and how). The lock only sees processes
    # on this host; TELEGRAM_SESSION_LOCK=shared lets those share one session
    # where they all reach Telegram from a single IP.
    lock = SessionLock(label, session_identity(client))
    await asyncio.to_thread(
        lock.acquire, grace_seconds=_lock_grace_seconds(), shared=_session_lock_shared()
    )
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
        await _verify_expected_username(label, client)
        return

    raise RuntimeError(
        f"Telegram client '{label}' is not authorized. Interactive phone login "
        "is disabled for the MCP server because it runs over stdio. Generate a "
        "session string with `uv run session_string_generator.py`, then set "
        "TELEGRAM_SESSION_STRING or TELEGRAM_SESSION_STRING_<LABEL> in .env. "
        "For existing file sessions, run the login outside the MCP server first."
    )


def _configure_transport_security(*, require_auth: bool = False) -> None:
    """Wire MCP_ALLOWED_HOSTS/MCP_ALLOWED_ORIGINS into FastMCP's DNS-rebinding
    protection, e.g. when the server sits behind a reverse proxy on a public
    domain instead of only being reached via 127.0.0.1/localhost.
    """
    host = mcp.settings.host
    if require_auth:
        require_loopback(host)
        tokens = load_client_tokens()
        if not tokens:
            raise RuntimeError(
                "Shared HTTP broker requires MCP_AUTH_TOKEN or per-client "
                "MCP_AUTH_TOKEN_<CLIENT> values."
            )
        mcp._token_verifier = StaticTokenVerifier(tokens)
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        base_url = f"http://{url_host}:{mcp.settings.port}"
        mcp.settings.auth = AuthSettings(
            issuer_url=base_url,
            resource_server_url=f"{base_url}/mcp",
            required_scopes=["telegram"],
        )

    raw_hosts = os.getenv("MCP_ALLOWED_HOSTS", "")
    allowed_hosts = [h.strip() for h in raw_hosts.split(",") if h.strip()]
    if require_auth and not allowed_hosts:
        allowed_hosts = [
            f"127.0.0.1:{mcp.settings.port}",
            f"localhost:{mcp.settings.port}",
            f"[::1]:{mcp.settings.port}",
        ]
    if not allowed_hosts:
        return

    raw_origins = os.getenv("MCP_ALLOWED_ORIGINS", "")
    allowed_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
    if require_auth and not allowed_origins:
        allowed_origins = [
            f"http://127.0.0.1:{mcp.settings.port}",
            f"http://localhost:{mcp.settings.port}",
            f"http://[::1]:{mcp.settings.port}",
        ]

    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _broker_mode_enabled() -> bool:
    return os.getenv("MCP_BROKER_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _telegram_connection_summary() -> dict[str, bool]:
    return {label: bool(client.is_connected()) for label, client in clients.items()}


async def _request_access_token(request: Request):
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    verifier = getattr(mcp, "_token_verifier", None)
    if verifier is None:
        return None
    return await verifier.verify_token(token)


@mcp.custom_route("/health", methods=["GET"])
async def broker_health(_request: Request) -> JSONResponse:
    connections = _telegram_connection_summary()
    healthy = bool(connections) and all(connections.values())
    return JSONResponse(
        {"status": "ok" if healthy else "degraded", "telegram_connected": healthy},
        status_code=200 if healthy else 503,
    )


@mcp.custom_route("/status", methods=["GET"])
async def broker_status(request: Request) -> JSONResponse:
    access_token = await _request_access_token(request)
    if access_token is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    record_audit_event(
        client_id=access_token.client_id,
        tool="broker_status",
        read_only=True,
        outcome="completed",
    )
    return JSONResponse(
        {
            "status": "ok",
            "transport": "http",
            "pid": os.getpid(),
            "client_id": access_token.client_id,
            "telegram_connections": _telegram_connection_summary(),
            "connection_owner": connection_status(),
        }
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
        broker_mode = transport == "http" and _broker_mode_enabled()
        _configure_transport_security(require_auth=broker_mode)
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
            async def _warm_client(label: str, cl: TelegramClient) -> None:
                try:
                    await cl.get_dialogs()
                except BotMethodInvalidError:
                    print(
                        f"Skipping entity cache pre-warm for bot client '{label}' (dialogs restricted for bots).",
                        file=sys.stderr,
                    )
                except Exception as exc:
                    print(f"Entity cache warm failed for '{label}': {exc}", file=sys.stderr)

            try:
                await asyncio.gather(*(_warm_client(label, cl) for label, cl in clients.items()))
                print("Entity caches warmed.", file=sys.stderr)
            except Exception as warm_exc:
                print(f"Entity cache warm failed: {warm_exc}", file=sys.stderr)

        warm_task = asyncio.create_task(_warm_caches())

        transport = os.getenv("MCP_TRANSPORT", "stdio").lower()

        # Validate transport mode
        VALID_TRANSPORTS = ("stdio", "http", "sse")
        if transport not in VALID_TRANSPORTS:
            accepted = ", ".join(VALID_TRANSPORTS)
            print(
                f"Invalid MCP_TRANSPORT '{transport}'. Expected one of: {accepted}.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Startup log with host:port for HTTP/SSE transports
        if transport in ("http", "sse"):
            host = os.getenv("MCP_HOST", "127.0.0.1")
            port = os.getenv("MCP_PORT", "8765")
            print(
                f"Telegram client(s) started ({labels}). Running MCP server ({transport}) on {host}:{port}...",
                file=sys.stderr,
            )
        else:
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
                "the session for both. Retry once the other instance is gone, or "
                "set TELEGRAM_SESSION_LOCK=shared if several instances on this "
                "host are meant to share one session (safe when they all reach "
                "Telegram from the same IP).",
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
    # Apply CLI transport/host/port overrides to environment (runtime sets globals)
    transport = _runtime._CLI_TRANSPORT or "stdio"
    host = _runtime._CLI_HOST
    port = _runtime._CLI_PORT

    if transport != "stdio":
        os.environ["MCP_TRANSPORT"] = transport
    if host:
        os.environ["MCP_HOST"] = host
    if port is not None:
        os.environ["MCP_PORT"] = str(port)
    # Before _apply_exposed_tools_mode(): that prunes non-exposed tools from
    # the tool manager, and the extension overrides validate tool names
    # against that same manager. Narrowing send_file's extensions while
    # send_file is not exposed is a valid configuration, so the name check
    # has to see the full tool set.
    _runtime._apply_file_extension_overrides()
    hidden, excluded = _runtime._apply_exposed_tools_mode()
    if hidden:
        # These names are the menu for the "+" allowlist; without this line the
        # only way to find them is reading the tool annotations in the source.
        print(
            f"TELEGRAM_EXPOSED_TOOLS hides {len(hidden)} tool(s); list any of them "
            f"after '+' to expose it: {', '.join(sorted(hidden))}",
            file=sys.stderr,
        )
    if excluded:
        print(
            f"TELEGRAM_EXPOSED_TOOLS excludes {len(excluded)} tool(s) listed "
            f"after '-': {', '.join(sorted(excluded))}",
            file=sys.stderr,
        )
    _transcription.validate_transcription_config()
    _session_lock_shared()  # fail loudly at startup on a bad toggle
    asyncio.run(_main())


if __name__ == "__main__":
    main()
