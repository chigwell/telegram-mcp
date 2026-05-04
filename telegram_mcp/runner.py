"""Application entrypoints for the Telegram MCP server."""

from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution

try:
    assert_safe_distribution()
except UnsafeInstallationError as exc:
    raise SystemExit(str(exc)) from None

from telegram_mcp.runtime import *
import telegram_mcp.tools  # noqa: F401 - registers MCP tools via decorators


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
        # Use the asynchronous entrypoint instead of mcp.run()
        await mcp.run_stdio_async()
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
