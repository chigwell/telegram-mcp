"""Client and session handling for the export CLI.

The export runs on an operator's machine while the MCP server usually runs
elsewhere against the same account. Telegram revokes an auth key used from two
places at once (``AuthKeyDuplicatedError``), which would take the server down,
so this module refuses to touch any session string it finds in the environment
and authorises as its own device instead. Extra interchangeable sessions for
the same account are what ``session_string_generator.py`` and the
``TELEGRAM_SESSION_STRINGS`` pool are for; export stays out of that pool on
purpose, because a pool slot is claimed per process on one host.
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from telethon import TelegramClient

from telegram_mcp import client_factory

from .util import log

# The server's session strings are frequently present in a shared environment
# (a .env file, or a secrets-manager `run` wrapper). Dropping them is the guard.
_FORBIDDEN_ENV = (
    "TELEGRAM_SESSION_STRING",
    "TELEGRAM_SESSION_STRINGS",
    "TELEGRAM_SESSION_STRING_PERSONAL",
)

PROXY_LABEL = "export"


def session_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
    else:
        base = Path(os.getenv("XDG_CONFIG_HOME", "~/.config")).expanduser() / "telegram-mcp"
        path = base / "export.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    return path


def _check_credentials() -> None:
    """Fail with a readable message before ``runtime`` casts the id to int."""
    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not api_id or not api_hash:
        raise SystemExit(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH are not set. Put them in .env "
            "next to the repo, export them, or run this command through your "
            "secrets manager."
        )
    if not api_id.isdigit():
        raise SystemExit(f"TELEGRAM_API_ID must be an integer, got '{api_id}'.")


def build_client(session: Optional[str] = None) -> TelegramClient:
    """A client on this tool's own session, built the way the rest of the
    package builds clients (device identity and proxy support included)."""
    for name in _FORBIDDEN_ENV:
        if os.environ.pop(name, None):
            log(f"note: ignoring {name} from the environment - export uses its own session.")
    _check_credentials()

    path = session_path(session)
    client = client_factory.build_client(str(path.with_suffix("")), PROXY_LABEL)
    # Telethon sleeps through floods under this threshold by itself; anything
    # longer is surfaced instead of stalling a long export for hours.
    client.flood_sleep_threshold = 900
    return client


@asynccontextmanager
async def connected(client: TelegramClient):
    """Connect without authorising.

    Telethon's own ``async with client`` calls ``start()``, which prompts for a
    phone number as soon as the session is not authorised - that turns every
    non-interactive command into a hang on a pipe. Connecting explicitly keeps
    the "are we logged in?" decision in :func:`ensure_authorised`.
    """
    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


async def ensure_authorised(client: TelegramClient, interactive: bool) -> None:
    if await client.is_user_authorized():
        return
    if not interactive or not sys.stdin.isatty():
        raise SystemExit(
            "Not logged in. Run 'telegram-mcp-export login' in an interactive terminal first."
        )
    log("Authorising a NEW Telegram device; existing sessions stay untouched.")
    await client.start()
