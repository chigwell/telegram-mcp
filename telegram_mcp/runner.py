"""Application entrypoints for the Telegram MCP server."""

from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:
    raise SystemExit(str(exc)) from None

from telegram_mcp.runtime import *
import telegram_mcp.tools  # noqa: F401 - registers MCP tools via decorators
from telegram_mcp.mcpl.channels import enumerate_channels
from telegram_mcp.mcpl.dispatcher import McplDispatcher
from telegram_mcp.mcpl.events import attach_event_handlers
from telegram_mcp.mcpl.handlers import (
    make_close_handler,
    make_list_handler,
    make_open_handler,
    make_publish_handler,
    make_typing_handler,
)
from telegram_mcp.mcpl.transport import McplTransport, run_stdio_with_mcpl


def _build_dispatcher() -> McplDispatcher:
    """Construct the MCPL dispatcher with all server-side handlers wired in."""
    dispatcher = McplDispatcher()
    dispatcher.register(
        "channels/publish",
        make_publish_handler(
            clients,
            resolve_entity_fn=resolve_entity,
            ensure_connected_fn=ensure_connected,
        ),
    )
    dispatcher.register("channels/list", make_list_handler(clients))
    dispatcher.register("channels/open", make_open_handler())
    dispatcher.register("channels/close", make_close_handler())
    dispatcher.register(
        "channels/typing",
        make_typing_handler(
            clients,
            resolve_entity_fn=resolve_entity,
            ensure_connected_fn=ensure_connected,
        ),
    )
    return dispatcher


async def _build_on_ready_hook():
    """After the host signals it's initialized: enumerate Telegram dialogs
    across all accounts, register them as MCPL channels, and attach the
    Telethon event handlers that translate NewMessage events into
    `channels/incoming` pushes.
    """

    async def on_ready(transport: McplTransport) -> None:
        all_channels = []
        for label, cl in clients.items():
            try:
                channels = await enumerate_channels(cl, label)
                all_channels.extend(channels)
            except Exception as exc:  # noqa: BLE001 — never block the agent on enumeration
                print(
                    f"Failed to enumerate channels for account '{label}': {exc}",
                    file=sys.stderr,
                )
        await transport.send_notification(
            "channels/register", {"channels": all_channels}
        )
        print(
            f"Registered {len(all_channels)} MCPL channels with host",
            file=sys.stderr,
        )

        for label, cl in clients.items():
            try:
                await attach_event_handlers(
                    cl, account_label=label, transport=transport
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"Failed to attach event handlers for account '{label}': {exc}",
                    file=sys.stderr,
                )

    return on_ready


async def _main() -> None:
    try:
        labels = ", ".join(clients.keys())
        print(f"Starting {len(clients)} Telegram client(s) ({labels})...", file=sys.stderr)
        await asyncio.gather(*(cl.start() for cl in clients.values()))

        # Warm entity caches — StringSession has no persistent cache,
        # so fetch all dialogs once per client to populate them
        print("Warming entity caches...", file=sys.stderr)
        await asyncio.gather(*(cl.get_dialogs() for cl in clients.values()))

        print(f"Telegram client(s) started ({labels}). Running MCP server...", file=sys.stderr)
        # MCPL-aware stdio runner — advertises experimental.mcpl in the
        # initialize handshake, registers Telegram dialogs as MCPL channels
        # once the host signals ready, and exposes channels/publish so the
        # host can send messages through us.
        on_ready = await _build_on_ready_hook()
        dispatcher = _build_dispatcher()
        await run_stdio_with_mcpl(mcp, dispatcher=dispatcher, on_ready=on_ready)
    except Exception as e:
        print(f"Error starting client: {e}", file=sys.stderr)
        if isinstance(e, sqlite3.OperationalError) and "database is locked" in str(e):
            print(
                "Database lock detected. Please ensure no other instances are running.",
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


def main() -> None:
    _configure_allowed_roots_from_cli(sys.argv[1:])
    nest_asyncio.apply()
    asyncio.run(_main())


if __name__ == "__main__":
    main()
