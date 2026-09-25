#!/usr/bin/env python3
"""Migrate a Telegram StringSession to a persistent SQLite session."""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import SQLiteSession, StringSession

from telegram_mcp.client_identity import client_identity_kwargs
from telegram_mcp.install_guard import UnsafeInstallationError, assert_safe_distribution


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate TELEGRAM_SESSION_STRING to a persistent SQLite session. "
            "Stop any running telegram-mcp process before migrating."
        )
    )
    parser.add_argument(
        "--account",
        metavar="LABEL",
        help=(
            "Migrate TELEGRAM_SESSION_STRING_<LABEL> instead of the default account "
            "(for example: --account work)."
        ),
    )
    parser.add_argument(
        "--target",
        metavar="PATH",
        help=(
            "Destination session name or path, without the .session suffix. "
            "Defaults to TELEGRAM_SESSION_NAME[_<LABEL>] or telegram_mcp_session[_<label>]."
        ),
    )
    return parser.parse_args()


def _check_installation() -> None:
    try:
        assert_safe_distribution()
    except UnsafeInstallationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


def _env_names(account: Optional[str]) -> tuple[str, str, str]:
    if not account:
        return (
            "TELEGRAM_SESSION_STRING",
            "TELEGRAM_SESSION_NAME",
            "telegram_mcp_session",
        )

    label = account.strip()
    if not label or not label.replace("-", "").replace("_", "").isalnum():
        raise ValueError("account label may contain only letters, numbers, '-' and '_'")

    suffix = label.upper().replace("-", "_")
    filename_label = label.lower().replace("-", "_")
    return (
        f"TELEGRAM_SESSION_STRING_{suffix}",
        f"TELEGRAM_SESSION_NAME_{suffix}",
        f"telegram_mcp_session_{filename_label}",
    )


def _target_paths(target: str) -> tuple[str, Path]:
    """Return the Telethon session name and its resulting SQLite filename."""
    expanded = Path(target).expanduser()
    if expanded.name.endswith(".session"):
        expanded = expanded.with_name(expanded.name[: -len(".session")])
    session_name = str(expanded)
    return session_name, Path(f"{session_name}.session").resolve()


async def migrate(
    session_string: str,
    api_id: int,
    api_hash: str,
    target: str,
) -> tuple[Path, object]:
    """Copy auth material into a new SQLite session and verify it with Telegram."""
    session_name, session_file = _target_paths(target)
    if session_file.exists():
        raise FileExistsError(
            f"destination already exists: {session_file}. Choose another --target."
        )

    sqlite_session = None
    client = None
    try:
        source = StringSession(session_string)
        sqlite_session = SQLiteSession(session_name)
        sqlite_session.set_dc(source.dc_id, source.server_address, source.port)
        sqlite_session.auth_key = source.auth_key
        sqlite_session.save()

        client = TelegramClient(
            sqlite_session,
            api_id,
            api_hash,
            **client_identity_kwargs(),
        )
        await client.connect()
        if not await client.is_user_authorized():
            raise RuntimeError("the source session is not authorized")
        user = await client.get_me()
    except Exception:
        if client is not None:
            await client.disconnect()
        elif sqlite_session is not None:
            sqlite_session.close()
        session_file.unlink(missing_ok=True)
        raise
    else:
        await client.disconnect()

    try:
        session_file.chmod(0o600)
    except OSError:
        # Some filesystems do not implement POSIX permissions.
        pass
    return session_file, user


async def _run(args: argparse.Namespace) -> int:
    try:
        string_env, name_env, default_target = _env_names(args.account)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    api_id_raw = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    session_string = os.getenv(string_env)

    if not api_id_raw or not api_hash:
        print("Error: TELEGRAM_API_ID and TELEGRAM_API_HASH must be set", file=sys.stderr)
        return 1
    try:
        api_id = int(api_id_raw)
    except ValueError:
        print("Error: TELEGRAM_API_ID must be an integer", file=sys.stderr)
        return 1
    if not session_string:
        print(f"Error: {string_env} must be set", file=sys.stderr)
        return 1

    target = args.target or os.getenv(name_env) or default_target
    try:
        session_file, user = await migrate(session_string, api_id, api_hash, target)
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    display_name = getattr(user, "first_name", None) or "Telegram user"
    user_id = getattr(user, "id", "unknown")
    session_name = str(session_file.with_suffix(""))
    print(f"Migration successful: {display_name} (id={user_id})")
    print(f"Created {session_file}")
    print("\nUpdate your environment:")
    print(f"  {name_env}={session_name}")
    print(f"  remove or comment out {string_env}")
    print("\nKeep the .session file private and do not commit it.")
    return 0


def main() -> None:
    load_dotenv()
    _check_installation()
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
