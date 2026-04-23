import argparse
import os
import sys
import json
import time
import asyncio
import sqlite3
import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Dict, Optional, Union, Any
from pathlib import Path
from urllib.parse import unquote, urlparse

# Third-party libraries
import nest_asyncio
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations
from mcp.shared.exceptions import McpError
from pythonjsonlogger import jsonlogger
from telethon import TelegramClient, functions, types, utils
from telethon.sessions import StringSession
from telethon.tl.types import (
    User,
    Chat,
    Channel,
    ChatAdminRights,
    ChatBannedRights,
    ChannelParticipantsKicked,
    ChannelParticipantsAdmins,
    InputChatPhoto,
    InputChatUploadedPhoto,
    InputChatPhotoEmpty,
    InputPeerUser,
    InputPeerChat,
    InputPeerChannel,
    DialogFilter,
    DialogFilterChatlist,
    DialogFilterDefault,
    TextWithEntities,
)
import re
from functools import wraps
import telethon.errors.rpcerrorlist


class ValidationError(Exception):
    """Custom exception for validation errors."""

    pass


def json_serializer(obj):
    """Helper function to convert non-serializable objects for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    # Add other non-serializable types as needed
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def get_entity_type(entity: Any) -> str:
    """Return a normalized, human-readable chat/entity type."""
    if isinstance(entity, User):
        return "User"
    if isinstance(entity, Chat):
        return "Group (Basic)"
    if isinstance(entity, Channel):
        if getattr(entity, "megagroup", False):
            return "Supergroup"
        return "Channel" if getattr(entity, "broadcast", False) else "Group"
    return type(entity).__name__


def get_entity_filter_type(entity: Any) -> Optional[str]:
    """Return list_chats-compatible filter type: user/group/channel."""
    entity_type = get_entity_type(entity)
    if entity_type == "User":
        return "user"
    if entity_type in ("Group (Basic)", "Group", "Supergroup"):
        return "group"
    if entity_type == "Channel":
        return "channel"
    return None


load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

mcp = FastMCP("telegram")


# ---------------------------------------------------------------------------
# Multi-account configuration
# ---------------------------------------------------------------------------


def _discover_accounts() -> dict[str, TelegramClient]:
    """Scan env vars to build account label -> TelegramClient mapping.

    Detection rules:
    - TELEGRAM_SESSION_STRING_<LABEL> / TELEGRAM_SESSION_NAME_<LABEL> -> multi-mode
    - Unsuffixed TELEGRAM_SESSION_STRING / TELEGRAM_SESSION_NAME -> label "default"
    - If both suffixed and unsuffixed exist -> unsuffixed becomes "default"
    """
    accounts: dict[str, TelegramClient] = {}

    prefix_str = "TELEGRAM_SESSION_STRING_"
    prefix_name = "TELEGRAM_SESSION_NAME_"

    for key, value in os.environ.items():
        if key.startswith(prefix_str) and value:
            label = key[len(prefix_str) :].lower()
            accounts[label] = TelegramClient(
                StringSession(value), TELEGRAM_API_ID, TELEGRAM_API_HASH
            )
        elif key.startswith(prefix_name) and value:
            label = key[len(prefix_name) :].lower()
            accounts[label] = TelegramClient(value, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    # Backward-compatible unsuffixed variables
    session_string = os.getenv("TELEGRAM_SESSION_STRING")
    session_name = os.getenv("TELEGRAM_SESSION_NAME")

    if session_string and "default" not in accounts:
        accounts["default"] = TelegramClient(
            StringSession(session_string), TELEGRAM_API_ID, TELEGRAM_API_HASH
        )
    elif session_name and "default" not in accounts:
        accounts["default"] = TelegramClient(session_name, TELEGRAM_API_ID, TELEGRAM_API_HASH)

    if not accounts:
        print(
            "Error: No Telegram session configured. "
            "Set TELEGRAM_SESSION_STRING or TELEGRAM_SESSION_STRING_<LABEL> in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    return accounts


clients: dict[str, TelegramClient] = _discover_accounts()


def get_client(account: str = None) -> TelegramClient:
    """Resolve account label to TelegramClient."""
    if account is None:
        if len(clients) == 1:
            return next(iter(clients.values()))
        raise ValueError(f"Account is required. Available accounts: {', '.join(clients.keys())}")
    label = account.lower()
    if label not in clients:
        raise ValueError(
            f"Unknown account '{account}'. Available accounts: {', '.join(clients.keys())}"
        )
    return clients[label]


def is_multi_mode() -> bool:
    """Return True when more than one account is configured."""
    return len(clients) > 1


def with_account(readonly=False):
    """Decorator that adds multi-account support to MCP tools.

    - In single-mode: always uses the sole client, no output tagging.
    - In multi-mode with explicit account: uses that account's client.
    - In multi-mode without account + readonly: fans out to all accounts
      concurrently, prefixes each result with [label], concatenates.
    - In multi-mode without account + NOT readonly: returns an error.

    The wrapped function must accept ``account: str = None`` and use
    ``get_client(account)`` internally to obtain the TelegramClient.
    """

    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            account = kwargs.get("account")

            # Explicit account OR single-mode -> call once
            if account is not None or not is_multi_mode():
                return await fn(*args, **kwargs)

            # account is None AND multi-mode
            if not readonly:
                labels = ", ".join(clients.keys())
                return f"Error: 'account' is required. Available accounts: {labels}"

            # Read-only fan-out to all accounts concurrently
            async def _call_for(label):
                kw = dict(kwargs)
                kw["account"] = label
                return label, await fn(*args, **kw)

            results = await asyncio.gather(*(_call_for(label) for label in clients))
            return "\n\n".join(f"[{label}]\n{result}" for label, result in results)

        return wrapper

    return decorator


_last_conn_verified: dict[int, float] = {}
_CONN_VERIFY_INTERVAL: float = 30.0  # seconds between live pings


async def _force_reconnect(cl: TelegramClient):
    """Force disconnect + reconnect regardless of is_connected() state."""
    reconnect_logger = logging.getLogger("telegram_mcp")
    reconnect_logger.warning("Forcing reconnect...")
    try:
        await cl.disconnect()
    except Exception:
        pass
    await cl.connect()
    if not await cl.is_user_authorized():
        reconnect_logger.warning("Client not authorized after reconnect, calling start()...")
        await cl.start()
    _last_conn_verified[id(cl)] = time.time()
    reconnect_logger.warning("Forced reconnect successful")


async def ensure_connected(cl: TelegramClient = None):
    """Verify Telegram connection is alive, reconnect if needed.

    is_connected() can return True when the underlying TCP socket is dead.
    We periodically send a lightweight request to verify the connection
    actually works, and force-reconnect on any failure.

    Accepts an explicit client; falls back to the default single-account
    client when called without one.
    """
    if cl is None:
        cl = get_client()

    key = id(cl)

    if not cl.is_connected():
        await _force_reconnect(cl)
        return

    # Skip verification if recently confirmed alive
    now = time.time()
    if now - _last_conn_verified.get(key, 0.0) < _CONN_VERIFY_INTERVAL:
        return

    # Verify with a lightweight Telegram API call
    try:
        await asyncio.wait_for(
            cl(functions.help.GetNearestDcRequest()),
            timeout=5.0,
        )
        _last_conn_verified[key] = now
    except (ConnectionError, OSError, asyncio.TimeoutError, Exception):
        await _force_reconnect(cl)


# Setup robust logging with both file and console output
logger = logging.getLogger("telegram_mcp")
logger.setLevel(logging.ERROR)  # Set to ERROR for production, INFO for debugging

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)  # Set to ERROR for production, INFO for debugging

# Create file handler with absolute path
script_dir = os.path.dirname(os.path.abspath(__file__))
log_file_path = os.path.join(script_dir, "mcp_errors.log")

try:
    file_handler = logging.FileHandler(log_file_path, mode="a")  # Append mode
    file_handler.setLevel(logging.ERROR)

    # Create formatters
    # Console formatter remains in the old format
    console_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
    console_handler.setFormatter(console_formatter)

    # File formatter is now JSON
    json_formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    file_handler.setFormatter(json_formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.info(f"Logging initialized to {log_file_path}")
except Exception as log_error:
    print(f"WARNING: Error setting up log file: {log_error}", file=sys.stderr)
    # Fallback to console-only logging
    logger.addHandler(console_handler)
    logger.error(f"Failed to set up log file handler: {log_error}")


# File-path tool security configuration
SERVER_ALLOWED_ROOTS: list[Path] = []
DEFAULT_DOWNLOAD_SUBDIR = "downloads"
DISALLOWED_PATH_PATTERNS = ("*", "?", "[", "]", "{", "}", "~", "\x00")
EXTENSION_ALLOWLISTS: dict[str, set[str]] = {
    "send_voice": {".ogg", ".opus"},
    "send_sticker": {".webp"},
    "set_profile_photo": {".jpg", ".jpeg", ".png", ".webp"},
    "edit_chat_photo": {".jpg", ".jpeg", ".png", ".webp"},
}
MAX_FILE_BYTES: dict[str, int] = {
    "send_file": 200 * 1024 * 1024,  # 200 MB
    "upload_file": 200 * 1024 * 1024,
    "send_voice": 100 * 1024 * 1024,
    "send_sticker": 10 * 1024 * 1024,
    "set_profile_photo": 50 * 1024 * 1024,
    "edit_chat_photo": 50 * 1024 * 1024,
}
ROOTS_UNSUPPORTED_ERROR_CODES = {-32601}
ROOTS_STATUS_READY = "ready"
ROOTS_STATUS_NOT_CONFIGURED = "not_configured"
ROOTS_STATUS_UNSUPPORTED_FALLBACK = "unsupported_fallback"
ROOTS_STATUS_CLIENT_DENY_ALL = "client_deny_all"
ROOTS_STATUS_ERROR = "error"


# Error code prefix mapping for better error tracing
class ErrorCategory(str, Enum):
    CHAT = "CHAT"
    MSG = "MSG"
    CONTACT = "CONTACT"
    GROUP = "GROUP"
    MEDIA = "MEDIA"
    PROFILE = "PROFILE"
    AUTH = "AUTH"
    ADMIN = "ADMIN"
    FOLDER = "FOLDER"


def log_and_format_error(
    function_name: str,
    error: Exception,
    prefix: Optional[Union[ErrorCategory, str]] = None,
    user_message: str = None,
    **kwargs,
) -> str:
    """
    Centralized error handling function.

    Logs an error and returns a formatted, user-friendly message.

    Args:
        function_name: Name of the function where the error occurred.
        error: The exception that was raised.
        prefix: Error code prefix (e.g., ErrorCategory.CHAT, "VALIDATION-001").
            If None, it will be derived from the function_name.
        user_message: A custom user-facing message to return. If None, a generic one is created.
        **kwargs: Additional context parameters to include in the log.

    Returns:
        A user-friendly error message with an error code.
    """
    # Generate a consistent error code
    if isinstance(prefix, str) and prefix == "VALIDATION-001":
        # Special case for validation errors
        error_code = prefix
    else:
        if prefix is None:
            # Try to derive prefix from function name
            for category in ErrorCategory:
                if category.name.lower() in function_name.lower():
                    prefix = category
                    break

        prefix_str = prefix.value if isinstance(prefix, ErrorCategory) else (prefix or "GEN")
        error_code = f"{prefix_str}-ERR-{abs(hash(function_name)) % 1000:03d}"

    # Format the additional context parameters
    context = ", ".join(f"{k}={v}" for k, v in kwargs.items())

    # Log the full technical error
    logger.error(f"Error in {function_name} ({context}) - Code: {error_code}", exc_info=True)

    # Return a user-friendly message
    if user_message:
        return user_message

    return f"An error occurred (code: {error_code}). Check mcp_errors.log for details."


def validate_id(*param_names_to_validate):
    """
    Decorator to validate chat_id and user_id parameters, including lists of IDs.
    It checks for valid integer ranges, string representations of integers,
    and username formats.
    """

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for param_name in param_names_to_validate:
                if param_name not in kwargs or kwargs[param_name] is None:
                    continue

                param_value = kwargs[param_name]

                def validate_single_id(value, p_name):
                    # Handle integer IDs
                    if isinstance(value, int):
                        if not (-(2**63) <= value <= 2**63 - 1):
                            return (
                                None,
                                f"Invalid {p_name}: {value}. ID is out of the valid integer range.",
                            )
                        return value, None

                    # Handle string IDs
                    if isinstance(value, str):
                        try:
                            int_value = int(value)
                            if not (-(2**63) <= int_value <= 2**63 - 1):
                                return (
                                    None,
                                    f"Invalid {p_name}: {value}. ID is out of the valid integer range.",
                                )
                            return int_value, None
                        except ValueError:
                            if re.match(r"^@?[a-zA-Z0-9_]{5,}$", value):
                                return value, None
                            else:
                                return (
                                    None,
                                    f"Invalid {p_name}: '{value}'. Must be a valid integer ID, or a username string.",
                                )

                    # Handle other invalid types
                    return (
                        None,
                        f"Invalid {p_name}: {value}. Type must be an integer or a string.",
                    )

                if isinstance(param_value, list):
                    validated_list = []
                    for item in param_value:
                        validated_item, error_msg = validate_single_id(item, param_name)
                        if error_msg:
                            return log_and_format_error(
                                func.__name__,
                                ValidationError(error_msg),
                                prefix="VALIDATION-001",
                                user_message=error_msg,
                                **{param_name: param_value},
                            )
                        validated_list.append(validated_item)
                    kwargs[param_name] = validated_list
                else:
                    validated_value, error_msg = validate_single_id(param_value, param_name)
                    if error_msg:
                        return log_and_format_error(
                            func.__name__,
                            ValidationError(error_msg),
                            prefix="VALIDATION-001",
                            user_message=error_msg,
                            **{param_name: param_value},
                        )
                    kwargs[param_name] = validated_value

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def format_entity(entity) -> Dict[str, Any]:
    """Helper function to format entity information consistently."""
    result = {"id": entity.id}

    if hasattr(entity, "title"):
        result["name"] = entity.title
        result["type"] = "group" if isinstance(entity, Chat) else "channel"
    elif hasattr(entity, "first_name"):
        name_parts = []
        if entity.first_name:
            name_parts.append(entity.first_name)
        if hasattr(entity, "last_name") and entity.last_name:
            name_parts.append(entity.last_name)
        result["name"] = " ".join(name_parts)
        result["type"] = "user"
        if hasattr(entity, "username") and entity.username:
            result["username"] = entity.username
        if hasattr(entity, "phone") and entity.phone:
            result["phone"] = entity.phone

    return result


async def resolve_entity(identifier: Union[int, str], client=None) -> Any:
    """Resolve entity with automatic cache warming and auto-reconnect.

    StringSession has no persistent entity cache. If get_entity() fails
    because the cache is cold (ValueError on PeerUser lookup for group IDs),
    warm the cache via get_dialogs() and retry.

    On ConnectionError, reconnects and retries once.
    """
    if client is None:
        client = get_client()
    await ensure_connected(client)
    try:
        try:
            return await client.get_entity(identifier)
        except ValueError:
            await client.get_dialogs()
            return await client.get_entity(identifier)
    except ConnectionError:
        await ensure_connected(client)
        try:
            return await client.get_entity(identifier)
        except ValueError:
            await client.get_dialogs()
            return await client.get_entity(identifier)


async def resolve_input_entity(identifier: Union[int, str], client=None) -> Any:
    """Like resolve_entity() but returns an InputPeer.

    On ConnectionError, reconnects and retries once.
    """
    if client is None:
        client = get_client()
    await ensure_connected(client)
    try:
        try:
            return await client.get_input_entity(identifier)
        except ValueError:
            await client.get_dialogs()
            return await client.get_input_entity(identifier)
    except ConnectionError:
        await ensure_connected(client)
        try:
            return await client.get_input_entity(identifier)
        except ValueError:
            await client.get_dialogs()
            return await client.get_input_entity(identifier)


def format_message(message) -> Dict[str, Any]:
    """Helper function to format message information consistently."""
    result = {
        "id": message.id,
        "date": message.date.isoformat(),
        "text": message.message or "",
    }

    if message.from_id:
        result["from_id"] = utils.get_peer_id(message.from_id)

    if message.media:
        result["has_media"] = True
        result["media_type"] = type(message.media).__name__

    return result


def get_sender_name(message) -> str:
    """Helper function to get sender name from a message."""
    if not message.sender:
        return "Unknown"

    # Check for group/channel title first
    if hasattr(message.sender, "title") and message.sender.title:
        return message.sender.title
    elif hasattr(message.sender, "first_name"):
        # User sender
        first_name = getattr(message.sender, "first_name", "") or ""
        last_name = getattr(message.sender, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip()
        return full_name if full_name else "Unknown"
    else:
        return "Unknown"


def get_engagement_info(message) -> str:
    """Helper function to get engagement metrics (views, forwards, reactions) from a message."""
    engagement_parts = []
    views = getattr(message, "views", None)
    if views is not None:
        engagement_parts.append(f"views:{views}")
    forwards = getattr(message, "forwards", None)
    if forwards is not None:
        engagement_parts.append(f"forwards:{forwards}")
    reactions = getattr(message, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        total_reactions = sum(getattr(r, "count", 0) or 0 for r in results) if results else 0
        engagement_parts.append(f"reactions:{total_reactions}")
    return f" | {', '.join(engagement_parts)}" if engagement_parts else ""


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    seen: set[str] = set()
    result: List[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _contains_forbidden_path_patterns(raw_path: str) -> Optional[str]:
    value = raw_path.strip()
    if not value:
        return "Path must not be empty."
    if any(token in value for token in DISALLOWED_PATH_PATTERNS):
        return "Path contains disallowed wildcard/shell patterns."
    if ".." in Path(value).parts:
        return "Path traversal is not allowed."
    return None


def _coerce_root_uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported root URI scheme: {parsed.scheme}")

    decoded_path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        decoded_path = f"//{parsed.netloc}{decoded_path}"
    if os.name == "nt" and decoded_path.startswith("/") and len(decoded_path) > 2:
        # file:///C:/tmp -> C:/tmp on Windows
        if decoded_path[2] == ":":
            decoded_path = decoded_path[1:]
    return Path(decoded_path).resolve(strict=True)


def _path_is_within_root(candidate: Path, root: Path) -> bool:
    root = root.resolve()
    if root.is_file():
        return candidate == root
    return candidate == root or root in candidate.parents


def _path_is_within_any_root(candidate: Path, roots: List[Path]) -> bool:
    return any(_path_is_within_root(candidate, root) for root in roots)


def _first_resolution_root(roots: List[Path]) -> Path:
    first = roots[0]
    return first if first.is_dir() else first.parent


def _ensure_extension_allowed(tool_name: str, candidate: Path) -> Optional[str]:
    allowlist = EXTENSION_ALLOWLISTS.get(tool_name)
    if not allowlist:
        return None
    if candidate.suffix.lower() not in allowlist:
        allowed = ", ".join(sorted(allowlist))
        return f"File extension is not allowed for {tool_name}. Allowed: {allowed}."
    return None


def _ensure_size_within_limit(tool_name: str, candidate: Path) -> Optional[str]:
    max_bytes = MAX_FILE_BYTES.get(tool_name)
    if not max_bytes:
        return None
    size = candidate.stat().st_size
    if size > max_bytes:
        return f"File is too large for {tool_name}: {size} bytes " f"(limit: {max_bytes} bytes)."
    return None


async def _get_effective_allowed_roots(ctx: Optional[Context]) -> List[Path]:
    roots, _status = await _get_effective_allowed_roots_with_status(ctx)
    return roots


def _is_roots_unsupported_error(error: Exception) -> bool:
    if isinstance(error, McpError):
        error_code = getattr(getattr(error, "error", None), "code", None)
        error_message = (
            getattr(getattr(error, "error", None), "message", None) or str(error)
        ).lower()
        if error_code in ROOTS_UNSUPPORTED_ERROR_CODES:
            return True
        return "method not found" in error_message or "not implemented" in error_message

    if isinstance(error, NotImplementedError):
        return True
    if isinstance(error, AttributeError):
        return "list_roots" in str(error)
    return False


async def _get_effective_allowed_roots_with_status(
    ctx: Optional[Context],
) -> tuple[List[Path], str]:
    fallback_roots = list(SERVER_ALLOWED_ROOTS)
    if ctx is None:
        if fallback_roots:
            return fallback_roots, ROOTS_STATUS_READY
        return [], ROOTS_STATUS_NOT_CONFIGURED

    try:
        list_roots_result = await ctx.session.list_roots()
    except Exception as error:
        if _is_roots_unsupported_error(error):
            if fallback_roots:
                return fallback_roots, ROOTS_STATUS_UNSUPPORTED_FALLBACK
            return [], ROOTS_STATUS_NOT_CONFIGURED
        logger.error(
            "MCP roots request failed; disabling file-path tools for safety.", exc_info=True
        )
        return [], ROOTS_STATUS_ERROR

    client_roots: List[Path] = []
    for root in list_roots_result.roots:
        try:
            client_roots.append(_coerce_root_uri_to_path(str(root.uri)))
        except Exception:
            # Ignore invalid root entries supplied by a client.
            continue

    if client_roots:
        return _dedupe_paths(client_roots), ROOTS_STATUS_READY

    # Roots API succeeded; an empty roots list is treated as explicit deny-all.
    return [], ROOTS_STATUS_CLIENT_DENY_ALL


async def _ensure_allowed_roots(
    ctx: Optional[Context], tool_name: str
) -> tuple[List[Path], Optional[str]]:
    roots, status = await _get_effective_allowed_roots_with_status(ctx)
    if not roots:
        if status == ROOTS_STATUS_CLIENT_DENY_ALL:
            return (
                [],
                (
                    f"{tool_name} is disabled because the client provided an empty "
                    "MCP Roots list (deny-all)."
                ),
            )
        if status == ROOTS_STATUS_ERROR:
            return (
                [],
                (
                    f"{tool_name} is disabled because MCP Roots could not be verified safely. "
                    "Check MCP client/server logs."
                ),
            )
        return (
            [],
            (
                f"{tool_name} is disabled until allowed roots are configured. "
                "Provide server CLI roots and/or client MCP Roots."
            ),
        )
    return roots, None


async def _resolve_readable_file_path(
    *,
    raw_path: str,
    ctx: Optional[Context],
    tool_name: str,
) -> tuple[Optional[Path], Optional[str]]:
    roots, error = await _ensure_allowed_roots(ctx, tool_name)
    if error:
        return None, error

    pattern_error = _contains_forbidden_path_patterns(raw_path)
    if pattern_error:
        return None, pattern_error

    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = _first_resolution_root(roots) / candidate

    try:
        candidate = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None, f"File not found: {raw_path}"

    if not _path_is_within_any_root(candidate, roots):
        return None, "Path is outside allowed roots."
    if not candidate.is_file():
        return None, f"Path is not a file: {candidate}"
    if not os.access(candidate, os.R_OK):
        return None, f"File is not readable: {candidate}"

    extension_error = _ensure_extension_allowed(tool_name, candidate)
    if extension_error:
        return None, extension_error

    size_error = _ensure_size_within_limit(tool_name, candidate)
    if size_error:
        return None, size_error

    return candidate, None


async def _resolve_writable_file_path(
    *,
    raw_path: Optional[str],
    default_filename: str,
    ctx: Optional[Context],
    tool_name: str,
) -> tuple[Optional[Path], Optional[str]]:
    roots, error = await _ensure_allowed_roots(ctx, tool_name)
    if error:
        return None, error

    if raw_path and raw_path.strip():
        pattern_error = _contains_forbidden_path_patterns(raw_path)
        if pattern_error:
            return None, pattern_error
        candidate = Path(raw_path.strip())
        if not candidate.is_absolute():
            candidate = _first_resolution_root(roots) / candidate
    else:
        safe_name = Path(default_filename).name
        candidate = _first_resolution_root(roots) / DEFAULT_DOWNLOAD_SUBDIR / safe_name

    candidate = candidate.resolve(strict=False)
    parent = candidate.parent.resolve(strict=False)
    if not _path_is_within_any_root(candidate, roots) or not _path_is_within_any_root(
        parent, roots
    ):
        return None, "Path is outside allowed roots."

    extension_error = _ensure_extension_allowed(tool_name, candidate)
    if extension_error:
        return None, extension_error

    parent.mkdir(parents=True, exist_ok=True)
    if not os.access(parent, os.W_OK):
        return None, f"Directory not writable: {parent}"

    return candidate, None


def _configure_allowed_roots_from_cli(argv: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        prog="telegram-mcp",
        add_help=False,
        description=(
            "Optional positional arguments define server-side allowed roots "
            "for file-path tools."
        ),
    )
    parser.add_argument("allowed_roots", nargs="*")
    parsed, _unknown = parser.parse_known_args(argv or [])

    resolved_roots: List[Path] = []
    for raw_root in parsed.allowed_roots:
        root = Path(raw_root).expanduser()
        if not root.exists():
            raise SystemExit(f"Allowed root does not exist: {root}")
        resolved = root.resolve(strict=True)
        resolved_roots.append(resolved)

    global SERVER_ALLOWED_ROOTS
    SERVER_ALLOWED_ROOTS = _dedupe_paths(resolved_roots)


@mcp.tool(annotations=ToolAnnotations(title="List Accounts", readOnlyHint=True))
async def list_accounts() -> str:
    """List all configured Telegram accounts with profile info."""
    lines = []
    for label, cl in clients.items():
        try:
            me = await cl.get_me()
            name = f"{me.first_name or ''} {me.last_name or ''}".strip() or "Unknown"
            phone = me.phone or "N/A"
            status = getattr(me, "status", None)
            if status:
                status_str = type(status).__name__.replace("UserStatus", "").lower()
            else:
                status_str = "unknown"
            lines.append(f"{label}: {name} (+{phone}) — {status_str}")
        except Exception:
            lines.append(f"{label}: (unable to fetch profile)")
    return "\n".join(lines)


@mcp.tool(annotations=ToolAnnotations(title="Get Chats", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def get_chats(account: str = None, page: int = 1, page_size: int = 20) -> str:
    """
    Get a paginated list of chats.
    Args:
        page: Page number (1-indexed).
        page_size: Number of chats per page.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        dialogs = await cl.get_dialogs()
        start = (page - 1) * page_size
        end = start + page_size
        if start >= len(dialogs):
            return "Page out of range."
        chats = dialogs[start:end]
        lines = []
        for dialog in chats:
            entity = dialog.entity
            chat_id = entity.id
            title = getattr(entity, "title", None) or getattr(entity, "first_name", "Unknown")
            lines.append(f"Chat ID: {chat_id}, Title: {title}")
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_chats", e)


@mcp.tool(annotations=ToolAnnotations(title="Get Messages", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
@validate_id("chat_id")
async def get_messages(
    chat_id: Union[int, str], page: int = 1, page_size: int = 20, account: str = None
) -> str:
    """
    Get paginated messages from a specific chat.
    Args:
        chat_id: The ID or username of the chat.
        page: Page number (1-indexed).
        page_size: Number of messages per page.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        offset = (page - 1) * page_size
        messages = await cl.get_messages(entity, limit=page_size, add_offset=offset)
        if not messages:
            return "No messages found for this page."
        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"

            engagement_info = get_engagement_info(msg)
            safe_text = (msg.message or "").replace("\n", "\\n")

            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info}{engagement_info} | Message: {safe_text}"
            )
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "get_messages", e, chat_id=chat_id, page=page, page_size=page_size
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Send Message", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def send_message(
    chat_id: Union[int, str],
    message: str,
    parse_mode: Optional[str] = None,
    account: str = None,
) -> str:
    """
    Send a message to a specific chat.
    Args:
        chat_id: The ID or username of the chat.
        message: The message content to send.
        parse_mode: Optional formatting mode. Use 'html' for HTML tags (<b>, <i>, <code>, <pre>,
            <a href="...">), 'md' or 'markdown' for Markdown (**bold**, __italic__, `code`,
            ```pre```), or omit for plain text (no formatting).
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.send_message(entity, message, parse_mode=parse_mode)
        return "Message sent successfully."
    except Exception as e:
        return log_and_format_error("send_message", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Send Scheduled Message",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=False,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def send_scheduled_message(
    chat_id: Union[int, str],
    message: str,
    schedule_date: Union[str, int],
    account: str = None,
) -> str:
    """
    Schedule a message to be sent at a future time.
    Args:
        chat_id: The ID or username of the chat.
        message: The message content to send.
        schedule_date: When to send the message. Either an ISO-8601 string
            (e.g. "2026-05-01T14:30:00" or "2026-05-01T14:30:00Z") or a Unix
            timestamp (int). Naive datetimes are treated as UTC.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        if isinstance(schedule_date, int):
            dt = datetime.fromtimestamp(schedule_date, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(schedule_date.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)

        if dt <= datetime.now(timezone.utc):
            return (
                f"schedule_date must be in the future (got {dt.isoformat()}, "
                f"now {datetime.now(timezone.utc).isoformat()})."
            )

        entity = await resolve_entity(chat_id, cl)
        result = await cl.send_message(entity, message, schedule=dt)
        message_id = getattr(result, "id", None)
        return f"Scheduled message {message_id} for {dt.isoformat()} in chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )
    except telethon.errors.rpcerrorlist.ScheduleDateTooLateError as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )
    except telethon.errors.rpcerrorlist.ScheduleDateInvalidError as e:
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )
    except Exception as e:
        logger.exception(
            f"send_scheduled_message failed (chat_id={chat_id}, schedule_date={schedule_date})"
        )
        return log_and_format_error(
            "send_scheduled_message", e, chat_id=chat_id, schedule_date=str(schedule_date)
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Scheduled Messages", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_scheduled_messages(chat_id: Union[int, str], account: str = None) -> str:
    """
    List all scheduled (pending) messages in a chat.
    Args:
        chat_id: The ID or username of the chat.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        result = await cl(functions.messages.GetScheduledHistoryRequest(peer=entity, hash=0))
        messages = getattr(result, "messages", []) or []
        if not messages:
            return f"No scheduled messages in chat {chat_id}."
        lines = [f"Scheduled messages in chat {chat_id} ({len(messages)}):"]
        for msg in messages:
            text = getattr(msg, "message", "") or ""
            preview = text[:100] + ("..." if len(text) > 100 else "")
            date_iso = msg.date.isoformat() if getattr(msg, "date", None) else "unknown"
            lines.append(f"ID: {msg.id} | Scheduled: {date_iso} | Text: {preview}")
        return "\n".join(lines)
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError as e:
        return log_and_format_error("get_scheduled_messages", e, chat_id=chat_id)
    except Exception as e:
        logger.exception(f"get_scheduled_messages failed (chat_id={chat_id})")
        return log_and_format_error("get_scheduled_messages", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Scheduled Message", openWorldHint=True, destructiveHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def delete_scheduled_message(
    chat_id: Union[int, str], message_ids: List[int], account: str = None
) -> str:
    """
    Delete one or more scheduled (pending) messages from a chat.
    Args:
        chat_id: The ID or username of the chat.
        message_ids: List of scheduled message IDs to delete.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        if not message_ids:
            return "message_ids must be a non-empty list."
        entity = await resolve_entity(chat_id, cl)
        await cl(functions.messages.DeleteScheduledMessagesRequest(peer=entity, id=message_ids))
        return f"Deleted {len(message_ids)} scheduled message(s) from chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError as e:
        return log_and_format_error(
            "delete_scheduled_message", e, chat_id=chat_id, message_ids=message_ids
        )
    except Exception as e:
        logger.exception(
            f"delete_scheduled_message failed (chat_id={chat_id}, message_ids={message_ids})"
        )
        return log_and_format_error(
            "delete_scheduled_message", e, chat_id=chat_id, message_ids=message_ids
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Subscribe Public Channel",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("channel")
async def subscribe_public_channel(channel: Union[int, str], account: str = None) -> str:
    """
    Subscribe (join) to a public channel or supergroup by username or ID.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(channel, cl)
        await cl(functions.channels.JoinChannelRequest(channel=entity))
        title = getattr(entity, "title", getattr(entity, "username", "Unknown channel"))
        return f"Subscribed to {title}."
    except telethon.errors.rpcerrorlist.UserAlreadyParticipantError:
        title = getattr(entity, "title", getattr(entity, "username", "this channel"))
        return f"Already subscribed to {title}."
    except telethon.errors.rpcerrorlist.ChannelPrivateError:
        return "Cannot subscribe: this channel is private or requires an invite link."
    except Exception as e:
        return log_and_format_error("subscribe_public_channel", e, channel=channel)


@mcp.tool(
    annotations=ToolAnnotations(title="List Inline Buttons", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def list_inline_buttons(
    chat_id: Union[int, str],
    message_id: Optional[Union[int, str]] = None,
    limit: int = 20,
    account: str = None,
) -> str:
    """
    Inspect inline buttons on a recent message to discover their indices/text/URLs.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        if isinstance(message_id, str):
            if message_id.isdigit():
                message_id = int(message_id)
            else:
                return "message_id must be an integer."

        entity = await resolve_entity(chat_id, cl)

        def _has_inline(msg):
            if getattr(msg, "buttons", None):
                return True
            rm = getattr(msg, "reply_markup", None)
            return bool(rm and hasattr(rm, "rows"))

        def _flat_buttons(msg):
            btns = getattr(msg, "buttons", None)
            if btns:
                return [btn for row in btns for btn in row]
            rm = getattr(msg, "reply_markup", None)
            if rm and hasattr(rm, "rows"):
                return [btn for row in rm.rows for btn in row.buttons]
            return []

        target_message = None

        if message_id is not None:
            target_message = await cl.get_messages(entity, ids=message_id)
            if isinstance(target_message, list):
                target_message = target_message[0] if target_message else None
        else:
            recent_messages = await cl.get_messages(entity, limit=limit)
            target_message = next((msg for msg in recent_messages if _has_inline(msg)), None)

        if not target_message:
            return "No message with inline buttons found."

        buttons = _flat_buttons(target_message)
        if not buttons:
            return f"Message {target_message.id} does not contain inline buttons."

        lines = [
            f"Buttons for message {target_message.id} (date {target_message.date}):",
        ]
        for idx, btn in enumerate(buttons):
            text = getattr(btn, "text", "") or "<no text>"
            url = getattr(btn, "url", None)
            has_callback = bool(getattr(btn, "data", None))
            parts = [f"[{idx}] text='{text}'"]
            parts.append("callback=yes" if has_callback else "callback=no")
            if url:
                parts.append(f"url={url}")
            lines.append(", ".join(parts))

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "list_inline_buttons",
            e,
            chat_id=chat_id,
            message_id=message_id,
            limit=limit,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Press Inline Button", openWorldHint=True, destructiveHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def press_inline_button(
    chat_id: Union[int, str],
    message_id: Optional[Union[int, str]] = None,
    button_text: Optional[str] = None,
    button_index: Optional[int] = None,
    account: str = None,
) -> str:
    """
    Press an inline button (callback) in a chat message.

    Args:
        chat_id: Chat or bot where the inline keyboard exists.
        message_id: Specific message ID to inspect. If omitted, searches recent messages for one containing buttons.
        button_text: Exact text of the button to press (case-insensitive).
        button_index: Zero-based index among all buttons if you prefer positional access.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        if button_text is None and button_index is None:
            return "Provide button_text or button_index to choose a button."

        # Normalize message_id if provided as a string
        if isinstance(message_id, str):
            if message_id.isdigit():
                message_id = int(message_id)
            else:
                return "message_id must be an integer."

        if isinstance(button_index, str):
            if button_index.isdigit():
                button_index = int(button_index)
            else:
                return "button_index must be an integer."

        entity = await resolve_entity(chat_id, cl)

        def _has_inline_buttons(msg):
            """Check if a message has inline buttons via buttons property or reply_markup."""
            if getattr(msg, "buttons", None):
                return True
            rm = getattr(msg, "reply_markup", None)
            return bool(rm and hasattr(rm, "rows"))

        def _extract_buttons(msg):
            """Extract flat list of buttons from buttons property or reply_markup fallback."""
            btns = getattr(msg, "buttons", None)
            if btns:
                return [btn for row in btns for btn in row]
            rm = getattr(msg, "reply_markup", None)
            if rm and hasattr(rm, "rows"):
                return [btn for row in rm.rows for btn in row.buttons]
            return []

        target_message = None
        if message_id is not None:
            # Fetch by ID first, then fall back to recent-message search if
            # reply_markup is missing (Telethon sometimes omits it for ID fetches).
            target_message = await cl.get_messages(entity, ids=message_id)
            if isinstance(target_message, list):
                target_message = target_message[0] if target_message else None
            if target_message and not _has_inline_buttons(target_message):
                # Fallback: search recent messages for the same ID with markup
                recent = await cl.get_messages(entity, limit=30)
                fallback = next(
                    (m for m in recent if m.id == target_message.id and _has_inline_buttons(m)),
                    None,
                )
                if fallback:
                    target_message = fallback
        else:
            recent_messages = await cl.get_messages(entity, limit=20)
            target_message = next(
                (msg for msg in recent_messages if _has_inline_buttons(msg)), None
            )

        if not target_message:
            return "No message with inline buttons found. Specify message_id to target a specific message."

        buttons = _extract_buttons(target_message)
        if not buttons:
            return f"Message {target_message.id} does not contain inline buttons."

        target_button = None
        if button_text:
            normalized = button_text.strip().lower()
            target_button = next(
                (
                    btn
                    for btn in buttons
                    if (getattr(btn, "text", "") or "").strip().lower() == normalized
                ),
                None,
            )

        if target_button is None and button_index is not None:
            if button_index < 0 or button_index >= len(buttons):
                return f"button_index out of range. Valid indices: 0-{len(buttons) - 1}."
            target_button = buttons[button_index]

        if not target_button:
            available = ", ".join(
                f"[{idx}] {getattr(btn, 'text', '') or '<no text>'}"
                for idx, btn in enumerate(buttons)
            )
            return f"Button not found. Available buttons: {available}"

        btn_data = getattr(target_button, "data", None)
        if not btn_data:
            url = getattr(target_button, "url", None)
            if url:
                return f"Selected button opens a URL instead of sending a callback: {url}"
            return "Selected button does not provide callback data to press."

        callback_result = await cl(
            functions.messages.GetBotCallbackAnswerRequest(
                peer=entity, msg_id=target_message.id, data=btn_data
            )
        )

        response_parts = []
        if getattr(callback_result, "message", None):
            response_parts.append(callback_result.message)
        if getattr(callback_result, "alert", None):
            response_parts.append("Telegram displayed an alert to the user.")
        if not response_parts:
            response_parts.append("Button pressed successfully.")

        return " ".join(response_parts)
    except Exception as e:
        return log_and_format_error(
            "press_inline_button",
            e,
            chat_id=chat_id,
            message_id=message_id,
            button_text=button_text,
            button_index=button_index,
        )


@mcp.tool(
    annotations=ToolAnnotations(title="List Contacts", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def list_contacts(account: str = None) -> str:
    """
    List all contacts in your Telegram account.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.GetContactsRequest(hash=0))
        users = result.users
        if not users:
            return "No contacts found."
        lines = []
        for user in users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = getattr(user, "username", "")
            phone = getattr(user, "phone", "")
            contact_info = f"ID: {user.id}, Name: {name}"
            if username:
                contact_info += f", Username: @{username}"
            if phone:
                contact_info += f", Phone: {phone}"
            lines.append(contact_info)
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("list_contacts", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Search Contacts", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def search_contacts(query: str, account: str = None) -> str:
    """
    Search for contacts by name, username, or phone number using Telethon's SearchRequest.
    Args:
        query: The search term to look for in contact names, usernames, or phone numbers.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.SearchRequest(q=query, limit=50))
        users = result.users
        if not users:
            return f"No contacts found matching '{query}'."
        lines = []
        for user in users:
            name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
            username = getattr(user, "username", "")
            phone = getattr(user, "phone", "")
            contact_info = f"ID: {user.id}, Name: {name}"
            if username:
                contact_info += f", Username: @{username}"
            if phone:
                contact_info += f", Phone: {phone}"
            lines.append(contact_info)
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("search_contacts", e, query=query)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Contact Ids", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_contact_ids(account: str = None) -> str:
    """
    Get all contact IDs in your Telegram account.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.GetContactIDsRequest(hash=0))
        if not result:
            return "No contact IDs found."
        return "Contact IDs: " + ", ".join(str(cid) for cid in result)
    except Exception as e:
        return log_and_format_error("get_contact_ids", e)


@mcp.tool(
    annotations=ToolAnnotations(title="List Messages", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def list_messages(
    chat_id: Union[int, str],
    limit: int = 20,
    search_query: str = None,
    from_date: str = None,
    to_date: str = None,
    account: str = None,
) -> str:
    """
    Retrieve messages with optional filters.

    Args:
        chat_id: The ID or username of the chat to get messages from.
        limit: Maximum number of messages to retrieve.
        search_query: Filter messages containing this text.
        from_date: Filter messages starting from this date (format: YYYY-MM-DD).
        to_date: Filter messages until this date (format: YYYY-MM-DD).
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Parse date filters if provided
        from_date_obj = None
        to_date_obj = None

        if from_date:
            try:
                from_date_obj = datetime.strptime(from_date, "%Y-%m-%d")
                # Make it timezone aware by adding UTC timezone info
                # Use datetime.timezone.utc for Python 3.9+ or import timezone directly for 3.13+
                try:
                    # For Python 3.9+
                    from_date_obj = from_date_obj.replace(tzinfo=datetime.timezone.utc)
                except AttributeError:
                    # For Python 3.13+
                    from datetime import timezone

                    from_date_obj = from_date_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                return f"Invalid from_date format. Use YYYY-MM-DD."

        if to_date:
            try:
                to_date_obj = datetime.strptime(to_date, "%Y-%m-%d")
                # Set to end of day and make timezone aware
                to_date_obj = to_date_obj + timedelta(days=1, microseconds=-1)
                # Add timezone info
                try:
                    to_date_obj = to_date_obj.replace(tzinfo=datetime.timezone.utc)
                except AttributeError:
                    from datetime import timezone

                    to_date_obj = to_date_obj.replace(tzinfo=timezone.utc)
            except ValueError:
                return f"Invalid to_date format. Use YYYY-MM-DD."

        # Prepare filter parameters
        params = {}
        if search_query:
            # IMPORTANT: Do not combine offset_date with search.
            # Use server-side search alone, then enforce date bounds client-side.
            params["search"] = search_query
            messages = []
            async for msg in cl.iter_messages(entity, **params):  # newest -> oldest
                if to_date_obj and msg.date > to_date_obj:
                    continue
                if from_date_obj and msg.date < from_date_obj:
                    break
                messages.append(msg)
                if len(messages) >= limit:
                    break

        else:
            # Use server-side iteration when only date bounds are present
            # (no search) to avoid over-fetching.
            if from_date_obj or to_date_obj:
                messages = []
                if from_date_obj:
                    # Walk forward from start date (oldest -> newest)
                    async for msg in cl.iter_messages(
                        entity, offset_date=from_date_obj, reverse=True
                    ):
                        if to_date_obj and msg.date > to_date_obj:
                            break
                        if msg.date < from_date_obj:
                            continue
                        messages.append(msg)
                        if len(messages) >= limit:
                            break
                else:
                    # Only upper bound: walk backward from end bound
                    async for msg in cl.iter_messages(
                        # offset_date is exclusive; +1µs makes to_date inclusive
                        entity,
                        offset_date=to_date_obj + timedelta(microseconds=1),
                    ):
                        messages.append(msg)
                        if len(messages) >= limit:
                            break
            else:
                messages = await cl.get_messages(entity, limit=limit, **params)

        if not messages:
            return "No messages found matching the criteria."

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            message_text = msg.message or "[Media/No text]"
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"

            engagement_info = get_engagement_info(msg)

            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info}{engagement_info} | Message: {message_text}"
            )

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("list_messages", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="List Topics", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def list_topics(
    chat_id: int,
    limit: int = 200,
    offset_topic: int = 0,
    search_query: str = None,
    account: str = None,
) -> str:
    """
    Retrieve forum topics from a supergroup with the forum feature enabled.

    Note for LLM: You can send a message to a selected topic via reply_to_message tool
    by using Topic ID as the message_id parameter.

    Args:
        chat_id: The ID of the forum-enabled chat (supergroup).
        limit: Maximum number of topics to retrieve.
        offset_topic: Topic ID offset for pagination.
        search_query: Optional query to filter topics by title.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "The specified chat is not a supergroup."

        if not getattr(entity, "forum", False):
            return "The specified supergroup does not have forum topics enabled."

        result = await cl(
            functions.channels.GetForumTopicsRequest(
                channel=entity,
                offset_date=0,
                offset_id=0,
                offset_topic=offset_topic,
                limit=limit,
                q=search_query or None,
            )
        )

        topics = getattr(result, "topics", None) or []
        if not topics:
            return "No topics found for this chat."

        messages_map = {}
        if getattr(result, "messages", None):
            messages_map = {message.id: message for message in result.messages}

        lines = []
        for topic in topics:
            line_parts = [f"Topic ID: {topic.id}"]

            title = getattr(topic, "title", None) or "(no title)"
            line_parts.append(f"Title: {title}")

            total_messages = getattr(topic, "total_messages", None)
            if total_messages is not None:
                line_parts.append(f"Messages: {total_messages}")

            unread_count = getattr(topic, "unread_count", None)
            if unread_count:
                line_parts.append(f"Unread: {unread_count}")

            if getattr(topic, "closed", False):
                line_parts.append("Closed: Yes")

            if getattr(topic, "hidden", False):
                line_parts.append("Hidden: Yes")

            top_message_id = getattr(topic, "top_message", None)
            top_message = messages_map.get(top_message_id)
            if top_message and getattr(top_message, "date", None):
                line_parts.append(f"Last Activity: {top_message.date.isoformat()}")

            lines.append(" | ".join(line_parts))

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "list_topics",
            e,
            chat_id=chat_id,
            limit=limit,
            offset_topic=offset_topic,
            search_query=search_query,
        )


@mcp.tool(annotations=ToolAnnotations(title="List Chats", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def list_chats(
    account: str = None,
    chat_type: str = None,
    limit: int = 20,
    unread_only: bool = False,
    unmuted_only: bool = False,
    with_about: bool = False,
) -> str:
    """
    List available chats with metadata.

    Args:
        chat_type: Filter by chat type ('user', 'group', 'channel', or None for all)
        limit: Maximum number of chats to retrieve from Telegram API (applied before filtering, so fewer results may be returned when filters are active).
        unread_only: If True, only return chats with unread messages.
        unmuted_only: If True, only return unmuted chats.
        with_about: If True, fetch each chat's description/bio via an additional
            API call per chat (slower — use only when needed for dispatch
            disambiguation).

    **Performance:** when `with_about=True`, makes one extra API call per chat
    returned. Avoid large `limit` values.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        dialogs = await cl.get_dialogs(limit=limit)

        results = []
        for dialog in dialogs:
            entity = dialog.entity

            # Filter by type if requested
            current_type = get_entity_filter_type(entity)

            if chat_type and current_type != chat_type.lower():
                continue

            # Format chat info
            chat_info = f"Chat ID: {entity.id}"

            if hasattr(entity, "title"):
                chat_info += f", Title: {entity.title}"
            elif hasattr(entity, "first_name"):
                name = f"{entity.first_name}"
                if hasattr(entity, "last_name") and entity.last_name:
                    name += f" {entity.last_name}"
                chat_info += f", Name: {name}"

            chat_info += f", Type: {get_entity_type(entity)}"

            if hasattr(entity, "username") and entity.username:
                chat_info += f", Username: @{entity.username}"

            # Add unread count if available
            unread_count = getattr(dialog, "unread_count", 0) or 0
            # Also check unread_mark (manual "mark as unread" flag)
            inner_dialog = getattr(dialog, "dialog", None)
            unread_mark = (
                bool(getattr(inner_dialog, "unread_mark", False)) if inner_dialog else False
            )

            # Extract mute status from notify_settings
            notify_settings = getattr(inner_dialog, "notify_settings", None)
            mute_until = getattr(notify_settings, "mute_until", None)
            if mute_until is None:
                is_muted = False
            elif isinstance(mute_until, datetime):
                is_muted = mute_until.timestamp() > time.time()
            else:
                is_muted = mute_until > time.time()

            # Filter by mute status if requested
            if unmuted_only and is_muted:
                continue

            # Filter by unread status if requested
            if unread_only and unread_count == 0 and not unread_mark:
                continue

            if unread_count > 0:
                chat_info += f", Unread: {unread_count}"
            elif unread_mark:
                chat_info += ", Unread: marked"
            else:
                chat_info += ", No unread messages"

            chat_info += f", Muted: {'yes' if is_muted else 'no'}"

            # Add unread mentions count if available
            unread_mentions = getattr(dialog, "unread_mentions_count", 0) or 0
            if unread_mentions > 0:
                chat_info += f", Unread mentions: {unread_mentions}"

            # Optionally fetch per-chat description/bio. Each call is guarded
            # so one failure (permissions, flood, etc.) doesn't abort the whole
            # listing.
            if with_about:
                about_text = ""
                try:
                    if isinstance(entity, Channel):
                        full = await cl(functions.channels.GetFullChannelRequest(channel=entity))
                        about_text = getattr(full.full_chat, "about", "") or ""
                    elif isinstance(entity, Chat):
                        full = await cl(functions.messages.GetFullChatRequest(chat_id=entity.id))
                        about_text = getattr(full.full_chat, "about", "") or ""
                    elif isinstance(entity, User):
                        full = await cl(functions.users.GetFullUserRequest(id=entity))
                        about_text = getattr(full.full_user, "about", "") or ""
                except Exception as about_err:
                    logger.warning(
                        f"list_chats: failed to fetch about for {entity.id}: {about_err}"
                    )
                    about_text = "<error fetching description>"

                if len(about_text) > 200:
                    about_text = about_text[:200] + "..."

                chat_info += f', About: "{about_text}"'

            results.append(chat_info)

        if not results:
            return f"No chats found matching the criteria."

        return "\n".join(results)
    except Exception as e:
        return log_and_format_error(
            "list_chats",
            e,
            chat_type=chat_type,
            limit=limit,
            unread_only=unread_only,
            unmuted_only=unmuted_only,
            with_about=with_about,
        )


@mcp.tool(annotations=ToolAnnotations(title="Get Chat", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
@validate_id("chat_id")
async def get_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get detailed information about a specific chat.

    Args:
        chat_id: The ID or username of the chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        result = []
        result.append(f"ID: {entity.id}")

        is_user = isinstance(entity, User)

        if hasattr(entity, "title"):
            result.append(f"Title: {entity.title}")
            result.append(f"Type: {get_entity_type(entity)}")
            if hasattr(entity, "username") and entity.username:
                result.append(f"Username: @{entity.username}")

            # Fetch participants count reliably
            try:
                participants_count = (await cl.get_participants(entity, limit=0)).total
                result.append(f"Participants: {participants_count}")
            except Exception as pe:
                result.append(f"Participants: Error fetching ({pe})")

        elif is_user:
            name = f"{entity.first_name}"
            if entity.last_name:
                name += f" {entity.last_name}"
            result.append(f"Name: {name}")
            result.append(f"Type: {get_entity_type(entity)}")
            if entity.username:
                result.append(f"Username: @{entity.username}")
            if entity.phone:
                result.append(f"Phone: {entity.phone}")
            result.append(f"Bot: {'Yes' if entity.bot else 'No'}")
            result.append(f"Verified: {'Yes' if entity.verified else 'No'}")

        # Get last activity if it's a dialog
        try:
            # Using get_dialogs might be slow if there are many dialogs
            # Alternative: Get entity again via get_dialogs if needed for unread count
            dialog = await cl.get_dialogs(limit=1, offset_id=0, offset_peer=entity)
            if dialog:
                dialog = dialog[0]
                result.append(f"Unread Messages: {dialog.unread_count}")
                if dialog.message:
                    last_msg = dialog.message
                    sender_name = "Unknown"
                    if last_msg.sender:
                        sender_name = getattr(last_msg.sender, "first_name", "") or getattr(
                            last_msg.sender, "title", "Unknown"
                        )
                        if hasattr(last_msg.sender, "last_name") and last_msg.sender.last_name:
                            sender_name += f" {last_msg.sender.last_name}"
                    sender_name = sender_name.strip() or "Unknown"
                    result.append(f"Last Message: From {sender_name} at {last_msg.date}")
                    result.append(f"Message: {last_msg.message or '[Media/No text]'}")
        except Exception as diag_ex:
            logger.warning(f"Could not get dialog info for {chat_id}: {diag_ex}")
            pass

        return "\n".join(result)
    except Exception as e:
        return log_and_format_error("get_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Direct Chat By Contact", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
async def get_direct_chat_by_contact(contact_query: str, account: str = None) -> str:
    """
    Find a direct chat with a specific contact by name, username, or phone.

    Args:
        contact_query: Name, username, or phone number to search for.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Fetch all contacts using the correct Telethon method
        result = await cl(functions.contacts.GetContactsRequest(hash=0))
        contacts = result.users
        found_contacts = []
        for contact in contacts:
            if not contact:
                continue
            name = (
                f"{getattr(contact, 'first_name', '')} {getattr(contact, 'last_name', '')}".strip()
            )
            username = getattr(contact, "username", "")
            phone = getattr(contact, "phone", "")
            if (
                contact_query.lower() in name.lower()
                or (username and contact_query.lower() in username.lower())
                or (phone and contact_query in phone)
            ):
                found_contacts.append(contact)
        if not found_contacts:
            return f"No contacts found matching '{contact_query}'."
        # If we found contacts, look for direct chats with them
        results = []
        dialogs = await cl.get_dialogs()
        for contact in found_contacts:
            contact_name = (
                f"{getattr(contact, 'first_name', '')} {getattr(contact, 'last_name', '')}".strip()
            )
            for dialog in dialogs:
                if isinstance(dialog.entity, User) and dialog.entity.id == contact.id:
                    chat_info = f"Chat ID: {dialog.entity.id}, Contact: {contact_name}"
                    if getattr(contact, "username", ""):
                        chat_info += f", Username: @{contact.username}"
                    if dialog.unread_count:
                        chat_info += f", Unread: {dialog.unread_count}"
                    results.append(chat_info)
                    break
        if not results:
            found_names = ", ".join(
                [f"{c.first_name} {c.last_name}".strip() for c in found_contacts]
            )
            return f"Found contacts: {found_names}, but no direct chats were found with them."
        return "\n".join(results)
    except Exception as e:
        return log_and_format_error("get_direct_chat_by_contact", e, contact_query=contact_query)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Contact Chats", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("contact_id")
async def get_contact_chats(contact_id: Union[int, str], account: str = None) -> str:
    """
    List all chats involving a specific contact.

    Args:
        contact_id: The ID or username of the contact.
    """
    try:
        cl = get_client(account)
        # Get contact info
        contact = await resolve_entity(contact_id, cl)
        if not isinstance(contact, User):
            return f"ID {contact_id} is not a user/contact."

        contact_name = (
            f"{getattr(contact, 'first_name', '')} {getattr(contact, 'last_name', '')}".strip()
        )

        # Find direct chat
        direct_chat = None
        dialogs = await cl.get_dialogs()

        results = []

        # Look for direct chat
        for dialog in dialogs:
            if isinstance(dialog.entity, User) and dialog.entity.id == contact_id:
                chat_info = f"Direct Chat ID: {dialog.entity.id}, Type: Private"
                if dialog.unread_count:
                    chat_info += f", Unread: {dialog.unread_count}"
                results.append(chat_info)
                break

        # Look for common groups/channels
        common_chats = []
        try:
            common = await cl.get_common_chats(contact)
            for chat in common:
                chat_type = get_entity_type(chat)
                chat_info = f"Chat ID: {chat.id}, Title: {chat.title}, Type: {chat_type}"
                results.append(chat_info)
        except:
            results.append("Could not retrieve common groups.")

        if not results:
            return f"No chats found with {contact_name} (ID: {contact_id})."

        return f"Chats with {contact_name} (ID: {contact_id}):\n" + "\n".join(results)
    except Exception as e:
        return log_and_format_error("get_contact_chats", e, contact_id=contact_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Last Interaction", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
@validate_id("contact_id")
async def get_last_interaction(contact_id: Union[int, str], account: str = None) -> str:
    """
    Get the most recent message with a contact.

    Args:
        contact_id: The ID or username of the contact.
    """
    try:
        cl = get_client(account)
        # Get contact info
        contact = await resolve_entity(contact_id, cl)
        if not isinstance(contact, User):
            return f"ID {contact_id} is not a user/contact."

        contact_name = (
            f"{getattr(contact, 'first_name', '')} {getattr(contact, 'last_name', '')}".strip()
        )

        # Get the last few messages
        messages = await cl.get_messages(contact, limit=5)

        if not messages:
            return f"No messages found with {contact_name} (ID: {contact_id})."

        results = [f"Last interactions with {contact_name} (ID: {contact_id}):"]

        for msg in messages:
            sender = "You" if msg.out else contact_name
            message_text = msg.message or "[Media/No text]"
            results.append(f"Date: {msg.date}, From: {sender}, Message: {message_text}")

        return "\n".join(results)
    except Exception as e:
        return log_and_format_error("get_last_interaction", e, contact_id=contact_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Message Context", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_message_context(
    chat_id: Union[int, str],
    message_id: int,
    context_size: int = 3,
    account: str = None,
) -> str:
    """
    Retrieve context around a specific message.

    Args:
        chat_id: The ID or username of the chat.
        message_id: The ID of the central message.
        context_size: Number of messages before and after to include.
    """
    try:
        cl = get_client(account)
        chat = await resolve_entity(chat_id, cl)
        # Get messages around the specified message
        messages_before = await cl.get_messages(chat, limit=context_size, max_id=message_id)
        central_message = await cl.get_messages(chat, ids=message_id)
        # Fix: get_messages(ids=...) returns a single Message, not a list
        if central_message is not None and not isinstance(central_message, list):
            central_message = [central_message]
        elif central_message is None:
            central_message = []
        messages_after = await cl.get_messages(
            chat, limit=context_size, min_id=message_id, reverse=True
        )
        if not central_message:
            return f"Message with ID {message_id} not found in chat {chat_id}."
        # Combine messages in chronological order
        all_messages = list(messages_before) + list(central_message) + list(messages_after)
        all_messages.sort(key=lambda m: m.id)
        results = [f"Context for message {message_id} in chat {chat_id}:"]
        for msg in all_messages:
            sender_name = get_sender_name(msg)
            highlight = " [THIS MESSAGE]" if msg.id == message_id else ""

            # Check if this message is a reply and get the replied message
            reply_content = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                try:
                    replied_msg = await cl.get_messages(chat, ids=msg.reply_to.reply_to_msg_id)
                    if replied_msg:
                        replied_sender = "Unknown"
                        if replied_msg.sender:
                            replied_sender = getattr(
                                replied_msg.sender, "first_name", ""
                            ) or getattr(replied_msg.sender, "title", "Unknown")
                        reply_content = f" | reply to {msg.reply_to.reply_to_msg_id}\n  → Replied message: [{replied_sender}] {replied_msg.message or '[Media/No text]'}"
                except Exception:
                    reply_content = (
                        f" | reply to {msg.reply_to.reply_to_msg_id} (original message not found)"
                    )

            results.append(
                f"ID: {msg.id} | {sender_name} | {msg.date}{highlight}{reply_content}\n{msg.message or '[Media/No text]'}\n"
            )
        return "\n".join(results)
    except Exception as e:
        return log_and_format_error(
            "get_message_context",
            e,
            chat_id=chat_id,
            message_id=message_id,
            context_size=context_size,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add Contact", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def add_contact(
    account: str = None,
    phone: Optional[str] = None,
    first_name: str = "",
    last_name: str = "",
    username: Optional[str] = None,
) -> str:
    """
    Add a new contact to your Telegram account.
    Args:
        phone: The phone number of the contact (with country code). Required if username is not provided.
        first_name: The contact's first name.
        last_name: The contact's last name (optional).
        username: The Telegram username (without @). Use this for adding contacts without phone numbers.

    Note: Either phone or username must be provided. If username is provided, the function will resolve it
    and add the contact using contacts.addContact API (which supports adding contacts without phone numbers).
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Normalize None to empty string for easier checking
        phone = phone or ""
        username = username or ""

        # Validate that at least one identifier is provided
        if not phone and not username:
            return "Error: Either phone or username must be provided."

        # If username is provided, use it for username-based contact addition
        if username:
            # Remove @ if present
            username_clean = username.lstrip("@")
            if not username_clean:
                return "Error: Username cannot be empty."

            # Resolve username to get user information
            try:
                resolve_result = await cl(
                    functions.contacts.ResolveUsernameRequest(username=username_clean)
                )

                # Extract user from the result
                if not resolve_result.users:
                    return f"Error: User with username @{username_clean} not found."

                user = resolve_result.users[0]
                if not isinstance(user, User):
                    return f"Error: Resolved entity is not a user."

                user_id = user.id
                access_hash = user.access_hash

                # Use contacts.addContact to add the contact by user ID
                from telethon.tl.types import InputUser

                result = await cl(
                    functions.contacts.AddContactRequest(
                        id=InputUser(user_id=user_id, access_hash=access_hash),
                        first_name=first_name,
                        last_name=last_name,
                        phone="",  # Empty phone for username-based contacts
                    )
                )

                if hasattr(result, "updates") and result.updates:
                    return (
                        f"Contact {first_name} {last_name} (@{username_clean}) added successfully."
                    )
                else:
                    return f"Contact {first_name} {last_name} (@{username_clean}) added successfully (no updates returned)."

            except Exception as resolve_e:
                logger.exception(
                    f"add_contact (username resolve) failed (username={username_clean})"
                )
                return log_and_format_error("add_contact", resolve_e, username=username_clean)

        elif phone:
            # Original phone-based contact addition
            from telethon.tl.types import InputPhoneContact

            result = await cl(
                functions.contacts.ImportContactsRequest(
                    contacts=[
                        InputPhoneContact(
                            client_id=0,
                            phone=phone,
                            first_name=first_name,
                            last_name=last_name,
                        )
                    ]
                )
            )
            if result.imported:
                return f"Contact {first_name} {last_name} added successfully."
            else:
                return f"Contact not added. Response: {str(result)}"
        else:
            return "Error: Phone number is required when username is not provided."
    except (ImportError, AttributeError) as type_err:
        # Try alternative approach using raw API (only for phone-based)
        if phone and not username:
            try:
                result = await cl(
                    functions.contacts.ImportContactsRequest(
                        contacts=[
                            {
                                "client_id": 0,
                                "phone": phone,
                                "first_name": first_name,
                                "last_name": last_name,
                            }
                        ]
                    )
                )
                if hasattr(result, "imported") and result.imported:
                    return f"Contact {first_name} {last_name} added successfully (alt method)."
                else:
                    return f"Contact not added. Alternative method response: {str(result)}"
            except Exception as alt_e:
                logger.exception(f"add_contact (alt method) failed (phone={phone})")
                return log_and_format_error("add_contact", alt_e, phone=phone)
        else:
            logger.exception(f"add_contact (type error) failed")
            return log_and_format_error("add_contact", type_err)
    except Exception as e:
        logger.exception(f"add_contact failed (phone={phone}, username={username})")
        return log_and_format_error("add_contact", e, phone=phone, username=username)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Contact", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("user_id")
async def delete_contact(user_id: Union[int, str], account: str = None) -> str:
    """
    Delete a contact by user ID.
    Args:
        user_id: The Telegram user ID or username of the contact to delete.
    """
    try:
        cl = get_client(account)
        user = await resolve_entity(user_id, cl)
        await cl(functions.contacts.DeleteContactsRequest(id=[user]))
        return f"Contact with user ID {user_id} deleted."
    except Exception as e:
        return log_and_format_error("delete_contact", e, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Block User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("user_id")
async def block_user(user_id: Union[int, str], account: str = None) -> str:
    """
    Block a user by user ID.
    Args:
        user_id: The Telegram user ID or username to block.
    """
    try:
        cl = get_client(account)
        user = await resolve_entity(user_id, cl)
        await cl(functions.contacts.BlockRequest(id=user))
        return f"User {user_id} blocked."
    except Exception as e:
        return log_and_format_error("block_user", e, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unblock User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("user_id")
async def unblock_user(user_id: Union[int, str], account: str = None) -> str:
    """
    Unblock a user by user ID.
    Args:
        user_id: The Telegram user ID or username to unblock.
    """
    try:
        cl = get_client(account)
        user = await resolve_entity(user_id, cl)
        await cl(functions.contacts.UnblockRequest(id=user))
        return f"User {user_id} unblocked."
    except Exception as e:
        return log_and_format_error("unblock_user", e, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Me", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def get_me(account: str = None) -> str:
    """
    Get your own user information.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        me = await cl.get_me()
        return json.dumps(format_entity(me), indent=2)
    except Exception as e:
        return log_and_format_error("get_me", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Create Group", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("user_ids")
async def create_group(title: str, user_ids: List[Union[int, str]], account: str = None) -> str:
    """
    Create a new group or supergroup and add users.

    Args:
        title: Title for the new group
        user_ids: List of user IDs or usernames to add to the group
    """
    try:
        cl = get_client(account)
        # Convert user IDs to entities
        users = []
        for user_id in user_ids:
            try:
                user = await resolve_entity(user_id, cl)
                users.append(user)
            except Exception as e:
                logger.error(f"Failed to get entity for user ID {user_id}: {e}")
                return f"Error: Could not find user with ID {user_id}"

        if not users:
            return "Error: No valid users provided"

        # Create the group with the users
        try:
            # Create a new chat with selected users
            result = await cl(functions.messages.CreateChatRequest(users=users, title=title))

            # Check what type of response we got
            if hasattr(result, "chats") and result.chats:
                created_chat = result.chats[0]
                return f"Group created with ID: {created_chat.id}"
            elif hasattr(result, "chat") and result.chat:
                return f"Group created with ID: {result.chat.id}"
            elif hasattr(result, "chat_id"):
                return f"Group created with ID: {result.chat_id}"
            else:
                # If we can't determine the chat ID directly from the result
                # Try to find it in recent dialogs
                await asyncio.sleep(1)  # Give Telegram a moment to register the new group
                dialogs = await cl.get_dialogs(limit=5)  # Get recent dialogs
                for dialog in dialogs:
                    if dialog.title == title:
                        return f"Group created with ID: {dialog.id}"

                # If we still can't find it, at least return success
                return f"Group created successfully. Please check your recent chats for '{title}'."

        except Exception as create_err:
            if "PEER_FLOOD" in str(create_err):
                return "Error: Cannot create group due to Telegram limits. Try again later."
            else:
                raise  # Let the outer exception handler catch it
    except Exception as e:
        logger.exception(f"create_group failed (title={title}, user_ids={user_ids})")
        return log_and_format_error("create_group", e, title=title, user_ids=user_ids)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Invite To Group", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("group_id", "user_ids")
async def invite_to_group(
    group_id: Union[int, str], user_ids: List[Union[int, str]], account: str = None
) -> str:
    """
    Invite users to a group or channel.

    Args:
        group_id: The ID or username of the group/channel.
        user_ids: List of user IDs or usernames to invite.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(group_id, cl)
        users_to_add = []

        for user_id in user_ids:
            try:
                user = await resolve_entity(user_id, cl)
                users_to_add.append(user)
            except ValueError as e:
                return f"Error: User with ID {user_id} could not be found. {e}"

        try:
            result = await cl(
                functions.channels.InviteToChannelRequest(channel=entity, users=users_to_add)
            )

            invited_count = 0
            if hasattr(result, "users") and result.users:
                invited_count = len(result.users)
            elif hasattr(result, "count"):
                invited_count = result.count

            return f"Successfully invited {invited_count} users to {entity.title}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot invite users who are not mutual contacts. Please ensure the users are in your contacts and have added you back."
        except telethon.errors.rpcerrorlist.UserPrivacyRestrictedError:
            return (
                "Error: One or more users have privacy settings that prevent you from adding them."
            )
        except Exception as e:
            return log_and_format_error("invite_to_group", e, group_id=group_id, user_ids=user_ids)

    except Exception as e:
        logger.error(
            f"telegram_mcp invite_to_group failed (group_id={group_id}, user_ids={user_ids})",
            exc_info=True,
        )
        return log_and_format_error("invite_to_group", e, group_id=group_id, user_ids=user_ids)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Leave Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def leave_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Leave a group or channel by chat ID.

    Args:
        chat_id: The chat ID or username to leave.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Check the entity type carefully
        if isinstance(entity, Channel):
            # Handle both channels and supergroups (which are also channels in Telegram)
            try:
                await cl(functions.channels.LeaveChannelRequest(channel=entity))
                chat_name = getattr(entity, "title", str(chat_id))
                return f"Left channel/supergroup {chat_name} (ID: {chat_id})."
            except Exception as chan_err:
                return log_and_format_error("leave_chat", chan_err, chat_id=chat_id)

        elif isinstance(entity, Chat):
            # Traditional basic groups (not supergroups)
            try:
                # First try with InputPeerUser
                me = await cl.get_me(input_peer=True)
                await cl(
                    functions.messages.DeleteChatUserRequest(
                        chat_id=entity.id,
                        user_id=me,  # Use the entity ID directly
                    )
                )
                chat_name = getattr(entity, "title", str(chat_id))
                return f"Left basic group {chat_name} (ID: {chat_id})."
            except Exception as chat_err:
                # If the above fails, try the second approach
                logger.warning(
                    f"First leave attempt failed: {chat_err}, trying alternative method"
                )

                try:
                    # Alternative approach - sometimes this works better
                    me_full = await cl.get_me()
                    await cl(
                        functions.messages.DeleteChatUserRequest(
                            chat_id=entity.id, user_id=me_full.id
                        )
                    )
                    chat_name = getattr(entity, "title", str(chat_id))
                    return f"Left basic group {chat_name} (ID: {chat_id})."
                except Exception as alt_err:
                    return log_and_format_error("leave_chat", alt_err, chat_id=chat_id)
        else:
            # Cannot leave a user chat this way
            entity_type = type(entity).__name__
            return log_and_format_error(
                "leave_chat",
                Exception(
                    f"Cannot leave chat ID {chat_id} of type {entity_type}. This function is for groups and channels only."
                ),
                chat_id=chat_id,
            )

    except Exception as e:
        logger.exception(f"leave_chat failed (chat_id={chat_id})")

        # Provide helpful hint for common errors
        error_str = str(e).lower()
        if "invalid" in error_str and "chat" in error_str:
            return log_and_format_error(
                "leave_chat",
                Exception(
                    f"Error leaving chat: This appears to be a channel/supergroup. Please check the chat ID and try again."
                ),
                chat_id=chat_id,
            )

        return log_and_format_error("leave_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Participants", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_participants(chat_id: Union[int, str], account: str = None) -> str:
    """
    List all participants in a group or channel.
    Args:
        chat_id: The group or channel ID or username.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        participants = await cl.get_participants(chat_id)
        lines = [
            f"ID: {p.id}, Name: {getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}"
            for p in participants
        ]
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_participants", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="Send File", openWorldHint=True, destructiveHint=True))
@with_account(readonly=False)
@validate_id("chat_id")
async def send_file(
    chat_id: Union[int, str],
    file_path: str,
    caption: str = None,
    ctx: Optional[Context] = None,
    account: str = None,
) -> str:
    """
    Send a file to a chat.
    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path to the file under allowed roots.
        caption: Optional caption for the file.
    """
    try:
        cl = get_client(account)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="send_file",
        )
        if path_error:
            return path_error
        entity = await resolve_entity(chat_id, cl)
        await cl.send_file(entity, str(safe_path), caption=caption)
        return f"File sent to chat {chat_id} from {safe_path}."
    except Exception as e:
        return log_and_format_error(
            "send_file", e, chat_id=chat_id, file_path=file_path, caption=caption
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Download Media", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def download_media(
    chat_id: Union[int, str],
    message_id: int,
    file_path: Optional[str] = None,
    ctx: Optional[Context] = None,
    account: str = None,
) -> str:
    """
    Download media from a message in a chat.
    Args:
        chat_id: The chat ID or username.
        message_id: The message ID containing the media.
        file_path: Optional absolute or relative path under allowed roots.
            If omitted, saves into `<first_root>/downloads/`.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        msg = await cl.get_messages(entity, ids=message_id)
        if not msg or not msg.media:
            return "No media found in the specified message."

        default_name = f"telegram_{chat_id}_{message_id}_{int(time.time())}"
        out_path, path_error = await _resolve_writable_file_path(
            raw_path=file_path,
            default_filename=default_name,
            ctx=ctx,
            tool_name="download_media",
        )
        if path_error:
            return path_error

        # Strip user-supplied extension so Telethon auto-detects the real media type.
        # If a path with extension is passed (e.g. ticket.jpg), Telethon writes to that
        # exact path even if the file is actually a PDF. Stripping the suffix lets
        # Telethon append the correct extension based on the actual file content.
        out_path_for_dl = out_path.with_suffix("")
        downloaded = await cl.download_media(msg, file=str(out_path_for_dl))
        if not downloaded:
            return f"Download failed for message {message_id}."

        final_path = Path(downloaded).resolve(strict=True)
        roots, roots_error = await _ensure_allowed_roots(ctx, "download_media")
        if roots_error:
            return roots_error
        if not _path_is_within_any_root(final_path, roots):
            return "Download failed: resulting path is outside allowed roots."

        return f"Media downloaded to {final_path}."
    except Exception as e:
        return log_and_format_error(
            "download_media",
            e,
            chat_id=chat_id,
            message_id=message_id,
            file_path=file_path,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Update Profile", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def update_profile(
    account: str = None, first_name: str = None, last_name: str = None, about: str = None
) -> str:
    """
    Update your profile information (name, bio).
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        await cl(
            functions.account.UpdateProfileRequest(
                first_name=first_name, last_name=last_name, about=about
            )
        )
        return "Profile updated."
    except Exception as e:
        return log_and_format_error(
            "update_profile", e, first_name=first_name, last_name=last_name, about=about
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Profile Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def set_profile_photo(
    file_path: str, ctx: Optional[Context] = None, account: str = None
) -> str:
    """
    Set a new profile photo.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="set_profile_photo",
        )
        if path_error:
            return path_error
        await cl(
            functions.photos.UploadProfilePhotoRequest(file=await cl.upload_file(str(safe_path)))
        )
        return f"Profile photo updated from {safe_path}."
    except Exception as e:
        return log_and_format_error("set_profile_photo", e, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Profile Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def delete_profile_photo(account: str = None) -> str:
    """
    Delete your current profile photo.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        photos = await cl(
            functions.photos.GetUserPhotosRequest(user_id="me", offset=0, max_id=0, limit=1)
        )
        if not photos.photos:
            return "No profile photo to delete."
        await cl(functions.photos.DeletePhotosRequest(id=[photos.photos[0]]))
        return "Profile photo deleted."
    except Exception as e:
        return log_and_format_error("delete_profile_photo", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Privacy Settings", openWorldHint=True, readOnlyHint=True
    )
)
@with_account(readonly=True)
async def get_privacy_settings(account: str = None) -> str:
    """
    Get your privacy settings for last seen status.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Import needed types directly
        from telethon.tl.types import InputPrivacyKeyStatusTimestamp

        try:
            settings = await cl(
                functions.account.GetPrivacyRequest(key=InputPrivacyKeyStatusTimestamp())
            )
            return str(settings)
        except TypeError as e:
            if "TLObject was expected" in str(e):
                return "Error: Privacy settings API call failed due to type mismatch. This is likely a version compatibility issue with Telethon."
            else:
                raise
    except Exception as e:
        logger.exception("get_privacy_settings failed")
        return log_and_format_error("get_privacy_settings", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Privacy Settings", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("allow_users", "disallow_users")
async def set_privacy_settings(
    key: str,
    allow_users: Optional[List[Union[int, str]]] = None,
    disallow_users: Optional[List[Union[int, str]]] = None,
    account: str = None,
) -> str:
    """
    Set privacy settings (e.g., last seen, phone, etc.).

    Args:
        key: The privacy setting to modify ('status' for last seen, 'phone', 'profile_photo', etc.)
        allow_users: List of user IDs or usernames to allow
        disallow_users: List of user IDs or usernames to disallow
    """
    try:
        cl = get_client(account)
        # Import needed types
        from telethon.tl.types import (
            InputPrivacyKeyStatusTimestamp,
            InputPrivacyKeyPhoneNumber,
            InputPrivacyKeyProfilePhoto,
            InputPrivacyValueAllowUsers,
            InputPrivacyValueDisallowUsers,
            InputPrivacyValueAllowAll,
            InputPrivacyValueDisallowAll,
        )

        # Map the simplified keys to their corresponding input types
        key_mapping = {
            "status": InputPrivacyKeyStatusTimestamp,
            "phone": InputPrivacyKeyPhoneNumber,
            "profile_photo": InputPrivacyKeyProfilePhoto,
        }

        # Get the appropriate key class
        if key not in key_mapping:
            return f"Error: Unsupported privacy key '{key}'. Supported keys: {', '.join(key_mapping.keys())}"

        privacy_key = key_mapping[key]()

        # Prepare the rules
        rules = []

        # Process allow rules
        if allow_users is None or len(allow_users) == 0:
            # If no specific users to allow, allow everyone by default
            rules.append(InputPrivacyValueAllowAll())
        else:
            # Convert user IDs to InputUser entities
            try:
                allow_entities = []
                for user_id in allow_users:
                    try:
                        user = await resolve_entity(user_id, cl)
                        allow_entities.append(user)
                    except Exception as user_err:
                        logger.warning(f"Could not get entity for user ID {user_id}: {user_err}")

                if allow_entities:
                    rules.append(InputPrivacyValueAllowUsers(users=allow_entities))
            except Exception as allow_err:
                logger.error(f"Error processing allowed users: {allow_err}")
                return log_and_format_error("set_privacy_settings", allow_err, key=key)

        # Process disallow rules
        if disallow_users and len(disallow_users) > 0:
            try:
                disallow_entities = []
                for user_id in disallow_users:
                    try:
                        user = await resolve_entity(user_id, cl)
                        disallow_entities.append(user)
                    except Exception as user_err:
                        logger.warning(f"Could not get entity for user ID {user_id}: {user_err}")

                if disallow_entities:
                    rules.append(InputPrivacyValueDisallowUsers(users=disallow_entities))
            except Exception as disallow_err:
                logger.error(f"Error processing disallowed users: {disallow_err}")
                return log_and_format_error("set_privacy_settings", disallow_err, key=key)

        # Apply the privacy settings
        try:
            result = await cl(functions.account.SetPrivacyRequest(key=privacy_key, rules=rules))
            return f"Privacy settings for {key} updated successfully."
        except TypeError as type_err:
            if "TLObject was expected" in str(type_err):
                return "Error: Privacy settings API call failed due to type mismatch. This is likely a version compatibility issue with Telethon."
            else:
                raise
    except Exception as e:
        logger.exception(f"set_privacy_settings failed (key={key})")
        return log_and_format_error("set_privacy_settings", e, key=key)


@mcp.tool(
    annotations=ToolAnnotations(title="Import Contacts", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def import_contacts(contacts: list, account: str = None) -> str:
    """
    Import a list of contacts. Each contact should be a dict with phone, first_name, last_name.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        input_contacts = [
            functions.contacts.InputPhoneContact(
                client_id=i,
                phone=c["phone"],
                first_name=c["first_name"],
                last_name=c.get("last_name", ""),
            )
            for i, c in enumerate(contacts)
        ]
        result = await cl(functions.contacts.ImportContactsRequest(contacts=input_contacts))
        return f"Imported {len(result.imported)} contacts."
    except Exception as e:
        return log_and_format_error("import_contacts", e, contacts=contacts)


@mcp.tool(
    annotations=ToolAnnotations(title="Export Contacts", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def export_contacts(account: str = None) -> str:
    """
    Export all contacts as a JSON string.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.GetContactsRequest(hash=0))
        users = result.users
        return json.dumps([format_entity(u) for u in users], indent=2)
    except Exception as e:
        return log_and_format_error("export_contacts", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Blocked Users", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_blocked_users(account: str = None) -> str:
    """
    Get a list of blocked users.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.GetBlockedRequest(offset=0, limit=100))
        return json.dumps([format_entity(u) for u in result.users], indent=2)
    except Exception as e:
        return log_and_format_error("get_blocked_users", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Create Channel", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def create_channel(
    title: str, about: str = "", megagroup: bool = False, account: str = None
) -> str:
    """
    Create a new channel or supergroup.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(
            functions.channels.CreateChannelRequest(title=title, about=about, megagroup=megagroup)
        )
        return f"Channel '{title}' created with ID: {result.chats[0].id}"
    except Exception as e:
        return log_and_format_error(
            "create_channel", e, title=title, about=about, megagroup=megagroup
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Chat Title", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def edit_chat_title(chat_id: Union[int, str], title: str, account: str = None) -> str:
    """
    Edit the title of a chat, group, or channel.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        if isinstance(entity, Channel):
            await cl(functions.channels.EditTitleRequest(channel=entity, title=title))
        elif isinstance(entity, Chat):
            await cl(functions.messages.EditChatTitleRequest(chat_id=chat_id, title=title))
        else:
            return f"Cannot edit title for this entity type ({type(entity)})."
        return f"Chat {chat_id} title updated to '{title}'."
    except Exception as e:
        logger.exception(f"edit_chat_title failed (chat_id={chat_id}, title='{title}')")
        return log_and_format_error("edit_chat_title", e, chat_id=chat_id, title=title)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Chat Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def edit_chat_photo(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
    account: str = None,
) -> str:
    """
    Edit the photo of a chat, group, or channel. Requires a file path to an image.
    """
    try:
        cl = get_client(account)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="edit_chat_photo",
        )
        if path_error:
            return path_error

        entity = await resolve_entity(chat_id, cl)
        uploaded_file = await cl.upload_file(str(safe_path))

        if isinstance(entity, Channel):
            # For channels/supergroups, use EditPhotoRequest with InputChatUploadedPhoto
            input_photo = InputChatUploadedPhoto(file=uploaded_file)
            await cl(functions.channels.EditPhotoRequest(channel=entity, photo=input_photo))
        elif isinstance(entity, Chat):
            # For basic groups, use EditChatPhotoRequest with InputChatUploadedPhoto
            input_photo = InputChatUploadedPhoto(file=uploaded_file)
            await cl(functions.messages.EditChatPhotoRequest(chat_id=chat_id, photo=input_photo))
        else:
            return f"Cannot edit photo for this entity type ({type(entity)})."

        return f"Chat {chat_id} photo updated from {safe_path}."
    except Exception as e:
        logger.exception(f"edit_chat_photo failed (chat_id={chat_id}, file_path='{file_path}')")
        return log_and_format_error("edit_chat_photo", e, chat_id=chat_id, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Chat About",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def edit_chat_about(chat_id: Union[int, str], about: str, account: str = None) -> str:
    """
    Edit the description ("About") of a chat, group, or channel.

    Args:
        chat_id: The ID or username of the chat.
        about: New description text. Telegram limits About to 255 characters.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        await cl(functions.messages.EditChatAboutRequest(peer=entity, about=about))
        return f"Chat {chat_id} description updated."
    except telethon.errors.rpcerrorlist.ChatAboutNotModifiedError:
        return f"Chat {chat_id} description is already set to the requested value."
    except telethon.errors.rpcerrorlist.ChatAboutTooLongError:
        return "Error: description exceeds Telegram's 255 character limit."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: admin rights required to edit the chat description."
    except Exception as e:
        logger.exception(f"edit_chat_about failed (chat_id={chat_id})")
        return log_and_format_error("edit_chat_about", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Chat Photo", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def delete_chat_photo(chat_id: Union[int, str], account: str = None) -> str:
    """
    Delete the photo of a chat, group, or channel.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        if isinstance(entity, Channel):
            # Use InputChatPhotoEmpty for channels/supergroups
            await cl(
                functions.channels.EditPhotoRequest(channel=entity, photo=InputChatPhotoEmpty())
            )
        elif isinstance(entity, Chat):
            # Use None (or InputChatPhotoEmpty) for basic groups
            await cl(
                functions.messages.EditChatPhotoRequest(
                    chat_id=chat_id, photo=InputChatPhotoEmpty()
                )
            )
        else:
            return f"Cannot delete photo for this entity type ({type(entity)})."

        return f"Chat {chat_id} photo deleted."
    except Exception as e:
        logger.exception(f"delete_chat_photo failed (chat_id={chat_id})")
        return log_and_format_error("delete_chat_photo", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Promote Admin", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("group_id", "user_id")
async def promote_admin(
    group_id: Union[int, str],
    user_id: Union[int, str],
    rights: dict = None,
    account: str = None,
) -> str:
    """
    Promote a user to admin in a group/channel.

    Args:
        group_id: ID or username of the group/channel
        user_id: User ID or username to promote
        rights: Admin rights to give (optional)
    """
    try:
        cl = get_client(account)
        chat = await resolve_entity(group_id, cl)
        user = await resolve_entity(user_id, cl)

        # Set default admin rights if not provided
        if not rights:
            rights = {
                "change_info": True,
                "post_messages": True,
                "edit_messages": True,
                "delete_messages": True,
                "ban_users": True,
                "invite_users": True,
                "pin_messages": True,
                "add_admins": False,
                "anonymous": False,
                "manage_call": True,
                "other": True,
            }

        admin_rights = ChatAdminRights(
            change_info=rights.get("change_info", True),
            post_messages=rights.get("post_messages", True),
            edit_messages=rights.get("edit_messages", True),
            delete_messages=rights.get("delete_messages", True),
            ban_users=rights.get("ban_users", True),
            invite_users=rights.get("invite_users", True),
            pin_messages=rights.get("pin_messages", True),
            add_admins=rights.get("add_admins", False),
            anonymous=rights.get("anonymous", False),
            manage_call=rights.get("manage_call", True),
            other=rights.get("other", True),
        )

        try:
            result = await cl(
                functions.channels.EditAdminRequest(
                    channel=chat, user_id=user, admin_rights=admin_rights, rank="Admin"
                )
            )
            return f"Successfully promoted user {user_id} to admin in {chat.title}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot promote users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("promote_admin", e, group_id=group_id, user_id=user_id)

    except Exception as e:
        logger.error(
            f"telegram_mcp promote_admin failed (group_id={group_id}, user_id={user_id})",
            exc_info=True,
        )
        return log_and_format_error("promote_admin", e, group_id=group_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Demote Admin", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("group_id", "user_id")
async def demote_admin(
    group_id: Union[int, str], user_id: Union[int, str], account: str = None
) -> str:
    """
    Demote a user from admin in a group/channel.

    Args:
        group_id: ID or username of the group/channel
        user_id: User ID or username to demote
    """
    try:
        cl = get_client(account)
        chat = await resolve_entity(group_id, cl)
        user = await resolve_entity(user_id, cl)

        # Create empty admin rights (regular user)
        admin_rights = ChatAdminRights(
            change_info=False,
            post_messages=False,
            edit_messages=False,
            delete_messages=False,
            ban_users=False,
            invite_users=False,
            pin_messages=False,
            add_admins=False,
            anonymous=False,
            manage_call=False,
            other=False,
        )

        try:
            result = await cl(
                functions.channels.EditAdminRequest(
                    channel=chat, user_id=user, admin_rights=admin_rights, rank=""
                )
            )
            return f"Successfully demoted user {user_id} from admin in {chat.title}"
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot modify admin status of users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("demote_admin", e, group_id=group_id, user_id=user_id)

    except Exception as e:
        logger.error(
            f"telegram_mcp demote_admin failed (group_id={group_id}, user_id={user_id})",
            exc_info=True,
        )
        return log_and_format_error("demote_admin", e, group_id=group_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Ban User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id", "user_id")
async def ban_user(chat_id: Union[int, str], user_id: Union[int, str], account: str = None) -> str:
    """
    Ban a user from a group or channel.

    Args:
        chat_id: ID or username of the group/channel
        user_id: User ID or username to ban
    """
    try:
        cl = get_client(account)
        chat = await resolve_entity(chat_id, cl)
        user = await resolve_entity(user_id, cl)

        # Create banned rights (all restrictions enabled)
        banned_rights = ChatBannedRights(
            until_date=None,  # Ban forever
            view_messages=True,
            send_messages=True,
            send_media=True,
            send_stickers=True,
            send_gifs=True,
            send_games=True,
            send_inline=True,
            embed_links=True,
            send_polls=True,
            change_info=True,
            invite_users=True,
            pin_messages=True,
        )

        try:
            await cl(
                functions.channels.EditBannedRequest(
                    channel=chat, participant=user, banned_rights=banned_rights
                )
            )
            return f"User {user_id} banned from chat {chat.title} (ID: {chat_id})."
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot ban users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.exception(f"ban_user failed (chat_id={chat_id}, user_id={user_id})")
        return log_and_format_error("ban_user", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unban User", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id", "user_id")
async def unban_user(
    chat_id: Union[int, str], user_id: Union[int, str], account: str = None
) -> str:
    """
    Unban a user from a group or channel.

    Args:
        chat_id: ID or username of the group/channel
        user_id: User ID or username to unban
    """
    try:
        cl = get_client(account)
        chat = await resolve_entity(chat_id, cl)
        user = await resolve_entity(user_id, cl)

        # Create unbanned rights (no restrictions)
        unbanned_rights = ChatBannedRights(
            until_date=None,
            view_messages=False,
            send_messages=False,
            send_media=False,
            send_stickers=False,
            send_gifs=False,
            send_games=False,
            send_inline=False,
            embed_links=False,
            send_polls=False,
            change_info=False,
            invite_users=False,
            pin_messages=False,
        )

        try:
            await cl(
                functions.channels.EditBannedRequest(
                    channel=chat, participant=user, banned_rights=unbanned_rights
                )
            )
            return f"User {user_id} unbanned from chat {chat.title} (ID: {chat_id})."
        except telethon.errors.rpcerrorlist.UserNotMutualContactError:
            return "Error: Cannot modify status of users who are not mutual contacts. Please ensure the user is in your contacts and has added you back."
        except Exception as e:
            return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.exception(f"unban_user failed (chat_id={chat_id}, user_id={user_id})")
        return log_and_format_error("unban_user", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Default Chat Permissions",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def set_default_chat_permissions(
    chat_id: Union[int, str],
    send_messages: bool = True,
    send_media: bool = True,
    send_stickers: bool = True,
    send_gifs: bool = True,
    send_games: bool = True,
    send_inline: bool = True,
    embed_links: bool = True,
    send_polls: bool = True,
    change_info: bool = False,
    invite_users: bool = True,
    pin_messages: bool = False,
    until_date: int = 0,
    account: str = None,
) -> str:
    """
    Set default member permissions for a group, supergroup, or channel.

    Pass True to allow, False to restrict. (Internally inverted to match
    Telegram's ChatBannedRights semantics where True means "banned".)

    Args:
        chat_id: ID or username of the chat.
        send_messages: allow sending text messages
        send_media: allow sending media (photos, videos, docs, audio)
        send_stickers: allow sending stickers
        send_gifs: allow sending GIFs
        send_games: allow sending games
        send_inline: allow using inline bots
        embed_links: allow link previews
        send_polls: allow sending polls
        change_info: allow members to change group info (title, photo, description)
        invite_users: allow members to invite others
        pin_messages: allow members to pin messages
        until_date: restriction expiry as Unix timestamp, 0 = permanent (default)
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        banned_rights = ChatBannedRights(
            until_date=until_date if until_date else None,
            send_messages=not send_messages,
            send_media=not send_media,
            send_stickers=not send_stickers,
            send_gifs=not send_gifs,
            send_games=not send_games,
            send_inline=not send_inline,
            embed_links=not embed_links,
            send_polls=not send_polls,
            change_info=not change_info,
            invite_users=not invite_users,
            pin_messages=not pin_messages,
        )
        await cl(
            functions.messages.EditChatDefaultBannedRightsRequest(
                peer=entity, banned_rights=banned_rights
            )
        )
        return f"Default permissions for chat {chat_id} updated."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: admin rights required to change default permissions."
    except telethon.errors.rpcerrorlist.ChatNotModifiedError:
        return f"Chat {chat_id} default permissions unchanged (already matched)."
    except Exception as e:
        logger.exception(f"set_default_chat_permissions failed (chat_id={chat_id})")
        return log_and_format_error("set_default_chat_permissions", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Toggle Slow Mode",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def toggle_slow_mode(chat_id: Union[int, str], seconds: int = 0, account: str = None) -> str:
    """
    Enable or disable slow mode for a supergroup.

    Only works on supergroups (not basic groups or regular channels). Telegram
    accepts seconds in {0, 10, 30, 60, 300, 900, 3600}. 0 disables slow mode.

    Args:
        chat_id: ID or username of the supergroup.
        seconds: interval between messages per user. 0 = disabled (default).
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        if not isinstance(entity, Channel) or not getattr(entity, "megagroup", False):
            return "Error: slow mode is only supported for supergroups."
        await cl(functions.channels.ToggleSlowModeRequest(channel=entity, seconds=seconds))
        if seconds == 0:
            return f"Slow mode disabled for chat {chat_id}."
        return f"Slow mode enabled for chat {chat_id} (interval: {seconds}s)."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: admin rights required to toggle slow mode."
    except Exception as e:
        logger.exception(f"toggle_slow_mode failed (chat_id={chat_id}, seconds={seconds})")
        return log_and_format_error("toggle_slow_mode", e, chat_id=chat_id, seconds=seconds)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Admin Rights",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id", "user_id")
async def edit_admin_rights(
    chat_id: Union[int, str],
    user_id: Union[int, str],
    rank: str = "",
    change_info: bool = False,
    post_messages: bool = False,
    edit_messages: bool = False,
    delete_messages: bool = False,
    ban_users: bool = False,
    invite_users: bool = False,
    pin_messages: bool = False,
    add_admins: bool = False,
    anonymous: bool = False,
    manage_call: bool = False,
    other: bool = False,
    account: str = None,
) -> str:
    """
    Set granular admin rights for a user in a supergroup or channel.

    Extends `promote_admin` (which uses a default set) by letting each right
    be specified individually. Pass True to grant, False to revoke. Passing
    all False revokes admin status (equivalent to `demote_admin`).

    Args:
        chat_id: ID or username of the supergroup/channel.
        user_id: User ID or username.
        rank: Custom admin title (max 16 chars). Empty = no custom title.
        change_info: can change chat info (title, photo, description)
        post_messages: can post in channel (channel-only)
        edit_messages: can edit other users' messages
        delete_messages: can delete messages
        ban_users: can restrict/ban members
        invite_users: can invite new members
        pin_messages: can pin messages
        add_admins: can add new admins with their own rights
        anonymous: admin actions appear anonymous
        manage_call: can manage voice/video chats
        other: reserved for future rights
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        user = await resolve_entity(user_id, cl)
        admin_rights = ChatAdminRights(
            change_info=change_info,
            post_messages=post_messages,
            edit_messages=edit_messages,
            delete_messages=delete_messages,
            ban_users=ban_users,
            invite_users=invite_users,
            pin_messages=pin_messages,
            add_admins=add_admins,
            anonymous=anonymous,
            manage_call=manage_call,
            other=other,
        )
        await cl(
            functions.channels.EditAdminRequest(
                channel=entity, user_id=user, admin_rights=admin_rights, rank=rank
            )
        )
        return f"Admin rights updated for user {user_id} in chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Error: you need admin rights (with 'add_admins') to modify admin rights."
    except telethon.errors.rpcerrorlist.UserAdminInvalidError:
        return "Error: cannot modify admin rights for this user (you may need to have promoted them originally)."
    except telethon.errors.rpcerrorlist.RightForbiddenError:
        return "Error: some of the requested rights are not allowed for your account or for this chat."
    except Exception as e:
        logger.exception(f"edit_admin_rights failed (chat_id={chat_id}, user_id={user_id})")
        return log_and_format_error("edit_admin_rights", e, chat_id=chat_id, user_id=user_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Admins", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
@validate_id("chat_id")
async def get_admins(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get all admins in a group or channel.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Fix: Use the correct filter type ChannelParticipantsAdmins
        participants = await cl.get_participants(chat_id, filter=ChannelParticipantsAdmins())
        lines = [
            f"ID: {p.id}, Name: {getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
            for p in participants
        ]
        return "\n".join(lines) if lines else "No admins found."
    except Exception as e:
        logger.exception(f"get_admins failed (chat_id={chat_id})")
        return log_and_format_error("get_admins", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Banned Users", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_banned_users(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get all banned users in a group or channel.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Fix: Use the correct filter type ChannelParticipantsKicked
        participants = await cl.get_participants(chat_id, filter=ChannelParticipantsKicked(q=""))
        lines = [
            f"ID: {p.id}, Name: {getattr(p, 'first_name', '')} {getattr(p, 'last_name', '')}".strip()
            for p in participants
        ]
        return "\n".join(lines) if lines else "No banned users found."
    except Exception as e:
        logger.exception(f"get_banned_users failed (chat_id={chat_id})")
        return log_and_format_error("get_banned_users", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Invite Link", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_invite_link(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get the invite link for a group or channel.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Try using ExportChatInviteRequest first
        try:
            from telethon.tl import functions

            result = await cl(functions.messages.ExportChatInviteRequest(peer=entity))
            return result.link
        except AttributeError:
            # If the function doesn't exist in the current Telethon version
            logger.warning("ExportChatInviteRequest not available, using alternative method")
        except Exception as e1:
            # If that fails, log and try alternative approach
            logger.warning(f"ExportChatInviteRequest failed: {e1}")

        # Alternative approach using cl.export_chat_invite_link
        try:
            invite_link = await cl.export_chat_invite_link(entity)
            return invite_link
        except Exception as e2:
            logger.warning(f"export_chat_invite_link failed: {e2}")

        # Last resort: Try directly fetching chat info
        try:
            if isinstance(entity, (Chat, Channel)):
                full_chat = await cl(functions.messages.GetFullChatRequest(chat_id=entity.id))
                if hasattr(full_chat, "full_chat") and hasattr(full_chat.full_chat, "invite_link"):
                    return full_chat.full_chat.invite_link or "No invite link available."
        except Exception as e3:
            logger.warning(f"GetFullChatRequest failed: {e3}")

        return "Could not retrieve invite link for this chat."
    except Exception as e:
        logger.exception(f"get_invite_link failed (chat_id={chat_id})")
        return log_and_format_error("get_invite_link", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Join Chat By Link", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def join_chat_by_link(link: str, account: str = None) -> str:
    """
    Join a chat by invite link.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Extract the hash from the invite link
        if "/" in link:
            hash_part = link.split("/")[-1]
            if hash_part.startswith("+"):
                hash_part = hash_part[1:]  # Remove the '+' if present
        else:
            hash_part = link

        # Try checking the invite before joining
        try:
            # Try to check invite info first (will often fail if not a member)
            invite_info = await cl(functions.messages.CheckChatInviteRequest(hash=hash_part))
            if hasattr(invite_info, "chat") and invite_info.chat:
                # If we got chat info, we're already a member
                chat_title = getattr(invite_info.chat, "title", "Unknown Chat")
                return f"You are already a member of this chat: {chat_title}"
        except Exception:
            # This often fails if not a member - just continue
            pass

        # Join the chat using the hash
        result = await cl(functions.messages.ImportChatInviteRequest(hash=hash_part))
        if result and hasattr(result, "chats") and result.chats:
            chat_title = getattr(result.chats[0], "title", "Unknown Chat")
            return f"Successfully joined chat: {chat_title}"
        return f"Joined chat via invite hash."
    except Exception as e:
        err_str = str(e).lower()
        if "expired" in err_str:
            return "The invite hash has expired and is no longer valid."
        elif "invalid" in err_str:
            return "The invite hash is invalid or malformed."
        elif "already" in err_str and "participant" in err_str:
            return "You are already a member of this chat."
        logger.exception(f"join_chat_by_link failed (link={link})")
        return f"Error joining chat: {e}"


@mcp.tool(
    annotations=ToolAnnotations(title="Export Chat Invite", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def export_chat_invite(chat_id: Union[int, str], account: str = None) -> str:
    """
    Export a chat invite link.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Try using ExportChatInviteRequest first
        try:
            from telethon.tl import functions

            result = await cl(functions.messages.ExportChatInviteRequest(peer=entity))
            return result.link
        except AttributeError:
            # If the function doesn't exist in the current Telethon version
            logger.warning("ExportChatInviteRequest not available, using alternative method")
        except Exception as e1:
            # If that fails, log and try alternative approach
            logger.warning(f"ExportChatInviteRequest failed: {e1}")

        # Alternative approach using cl.export_chat_invite_link
        try:
            invite_link = await cl.export_chat_invite_link(entity)
            return invite_link
        except Exception as e2:
            logger.warning(f"export_chat_invite_link failed: {e2}")
            return log_and_format_error("export_chat_invite", e2, chat_id=chat_id)

    except Exception as e:
        logger.exception(f"export_chat_invite failed (chat_id={chat_id})")
        return log_and_format_error("export_chat_invite", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Import Chat Invite", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def import_chat_invite(hash: str, account: str = None) -> str:
    """
    Import a chat invite by hash.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Remove any prefixes like '+' if present
        if hash.startswith("+"):
            hash = hash[1:]

        # Try checking the invite before joining
        try:
            from telethon.errors import (
                InviteHashExpiredError,
                InviteHashInvalidError,
                UserAlreadyParticipantError,
                ChatAdminRequiredError,
                UsersTooMuchError,
            )

            # Try to check invite info first (will often fail if not a member)
            invite_info = await cl(functions.messages.CheckChatInviteRequest(hash=hash))
            if hasattr(invite_info, "chat") and invite_info.chat:
                # If we got chat info, we're already a member
                chat_title = getattr(invite_info.chat, "title", "Unknown Chat")
                return f"You are already a member of this chat: {chat_title}"
        except Exception as check_err:
            # This often fails if not a member - just continue
            pass

        # Join the chat using the hash
        try:
            result = await cl(functions.messages.ImportChatInviteRequest(hash=hash))
            if result and hasattr(result, "chats") and result.chats:
                chat_title = getattr(result.chats[0], "title", "Unknown Chat")
                return f"Successfully joined chat: {chat_title}"
            return f"Joined chat via invite hash."
        except Exception as join_err:
            err_str = str(join_err).lower()
            if "expired" in err_str:
                return "The invite hash has expired and is no longer valid."
            elif "invalid" in err_str:
                return "The invite hash is invalid or malformed."
            elif "already" in err_str and "participant" in err_str:
                return "You are already a member of this chat."
            elif "admin" in err_str:
                return "Cannot join this chat - requires admin approval."
            elif "too much" in err_str or "too many" in err_str:
                return "Cannot join this chat - it has reached maximum number of participants."
            else:
                raise  # Re-raise to be caught by the outer exception handler

    except Exception as e:
        logger.exception(f"import_chat_invite failed (hash={hash})")
        return log_and_format_error("import_chat_invite", e, hash=hash)


@mcp.tool(
    annotations=ToolAnnotations(title="Send Voice", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def send_voice(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
    account: str = None,
) -> str:
    """
    Send a voice message to a chat. File must be an OGG/OPUS voice note.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path under allowed roots to the OGG/OPUS file.
    """
    try:
        cl = get_client(account)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="send_voice",
        )
        if path_error:
            return path_error

        mime, _ = mimetypes.guess_type(str(safe_path))
        if not (
            mime
            and (
                mime == "audio/ogg"
                or str(safe_path).lower().endswith(".ogg")
                or str(safe_path).lower().endswith(".opus")
            )
        ):
            return "Voice file must be .ogg or .opus format."

        entity = await resolve_entity(chat_id, cl)
        await cl.send_file(entity, str(safe_path), voice_note=True)
        return f"Voice message sent to chat {chat_id} from {safe_path}."
    except Exception as e:
        return log_and_format_error("send_voice", e, chat_id=chat_id, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(title="Upload File", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def upload_file(file_path: str, ctx: Optional[Context] = None, account: str = None) -> str:
    """
    Upload a local file to Telegram and return upload metadata.

    Args:
        file_path: Absolute or relative path under allowed roots.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="upload_file",
        )
        if path_error:
            return path_error

        uploaded = await cl.upload_file(str(safe_path))
        payload = {
            "path": str(safe_path),
            "name": getattr(uploaded, "name", safe_path.name),
            "size": getattr(uploaded, "size", safe_path.stat().st_size),
            "md5_checksum": getattr(uploaded, "md5_checksum", None),
        }
        return json.dumps(payload, indent=2, default=json_serializer)
    except Exception as e:
        return log_and_format_error("upload_file", e, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(title="Forward Message", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("from_chat_id", "to_chat_id")
async def forward_message(
    from_chat_id: Union[int, str],
    message_id: int,
    to_chat_id: Union[int, str],
    account: str = None,
) -> str:
    """
    Forward a message from one chat to another.
    """
    try:
        cl = get_client(account)
        from_entity = await resolve_entity(from_chat_id, cl)
        to_entity = await resolve_entity(to_chat_id, cl)
        await cl.forward_messages(to_entity, message_id, from_entity)
        return f"Message {message_id} forwarded from {from_chat_id} to {to_chat_id}."
    except Exception as e:
        return log_and_format_error(
            "forward_message",
            e,
            from_chat_id=from_chat_id,
            message_id=message_id,
            to_chat_id=to_chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def edit_message(
    chat_id: Union[int, str], message_id: int, new_text: str, account: str = None
) -> str:
    """
    Edit a message you sent.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.edit_message(entity, message_id, new_text)
        return f"Message {message_id} edited."
    except Exception as e:
        return log_and_format_error(
            "edit_message", e, chat_id=chat_id, message_id=message_id, new_text=new_text
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def delete_message(chat_id: Union[int, str], message_id: int, account: str = None) -> str:
    """
    Delete a message by ID.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.delete_messages(entity, message_id)
        return f"Message {message_id} deleted."
    except Exception as e:
        return log_and_format_error("delete_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Chat History",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=False,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def delete_chat_history(
    chat_id: Union[int, str], max_id: int = 0, revoke: bool = False, account: str = None
) -> str:
    """
    Clear the full message history of a chat.

    Args:
        chat_id: Chat ID or username.
        max_id: Delete messages up to this ID; 0 deletes all messages (default).
        revoke: If True, delete for both parties (default False = only for you).
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        result = await cl(
            functions.messages.DeleteHistoryRequest(peer=entity, max_id=max_id, revoke=revoke)
        )
        pts_count = getattr(result, "pts_count", 0)
        offset = getattr(result, "offset", 0)
        scope = "for both parties" if revoke else "for you"
        return (
            f"Chat {chat_id} history cleared {scope}: "
            f"{pts_count} messages deleted (offset={offset})."
        )
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Cannot delete chat history: admin privileges are required."
    except Exception as e:
        return log_and_format_error(
            "delete_chat_history",
            e,
            chat_id=chat_id,
            max_id=max_id,
            revoke=revoke,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Messages Bulk",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def delete_messages_bulk(
    chat_id: Union[int, str],
    message_ids: List[int],
    revoke: bool = True,
    account: str = None,
) -> str:
    """
    Delete multiple messages in a single call.

    Args:
        chat_id: Chat ID or username.
        message_ids: List of message IDs to delete.
        revoke: If True, delete for both parties (default True). Ignored for channels.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        if isinstance(entity, Channel):
            result = await cl(
                functions.channels.DeleteMessagesRequest(channel=entity, id=message_ids)
            )
        else:
            result = await cl(
                functions.messages.DeleteMessagesRequest(id=message_ids, revoke=revoke)
            )
        pts_count = getattr(result, "pts_count", 0)
        return f"Deleted {pts_count} of {len(message_ids)} messages from chat {chat_id}."
    except telethon.errors.rpcerrorlist.MessageIdInvalidError:
        return "Cannot delete messages: one or more message IDs are invalid."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Cannot delete messages: admin privileges are required."
    except Exception as e:
        return log_and_format_error(
            "delete_messages_bulk",
            e,
            chat_id=chat_id,
            message_ids=message_ids,
            revoke=revoke,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Pin Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def pin_message(chat_id: Union[int, str], message_id: int, account: str = None) -> str:
    """
    Pin a message in a chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.pin_message(entity, message_id)
        return f"Message {message_id} pinned in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("pin_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unpin Message", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def unpin_message(chat_id: Union[int, str], message_id: int, account: str = None) -> str:
    """
    Unpin a message in a chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.unpin_message(entity, message_id)
        return f"Message {message_id} unpinned in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("unpin_message", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unpin All Messages",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def unpin_all_messages(chat_id: Union[int, str], account: str = None) -> str:
    """
    Unpin all pinned messages in a chat.

    Args:
        chat_id: Chat ID or username.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        await cl(functions.messages.UnpinAllMessagesRequest(peer=entity))
        return f"All messages unpinned in chat {chat_id}."
    except telethon.errors.rpcerrorlist.ChatAdminRequiredError:
        return "Cannot unpin messages: admin privileges are required."
    except Exception as e:
        return log_and_format_error("unpin_all_messages", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Mark As Read", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def mark_as_read(chat_id: Union[int, str], account: str = None) -> str:
    """
    Mark all messages as read in a chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.send_read_acknowledge(entity)
        return f"Marked all messages as read in chat {chat_id}."
    except Exception as e:
        return log_and_format_error("mark_as_read", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Reply To Message", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def reply_to_message(
    chat_id: Union[int, str],
    message_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    account: str = None,
) -> str:
    """
    Reply to a specific message in a chat.
    Args:
        chat_id: The chat ID or username.
        message_id: The message ID to reply to.
        text: The reply text.
        parse_mode: Optional formatting mode. Use 'html' for HTML tags (<b>, <i>, <code>, <pre>,
            <a href="...">), 'md' or 'markdown' for Markdown (**bold**, __italic__, `code`,
            ```pre```), or omit for plain text (no formatting).
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        await cl.send_message(entity, text, reply_to=message_id, parse_mode=parse_mode)
        return f"Replied to message {message_id} in chat {chat_id}."
    except Exception as e:
        return log_and_format_error(
            "reply_to_message", e, chat_id=chat_id, message_id=message_id, text=text
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Media Info", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_media_info(chat_id: Union[int, str], message_id: int, account: str = None) -> str:
    """
    Get info about media in a message.

    Args:
        chat_id: The chat ID or username.
        message_id: The message ID.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        msg = await cl.get_messages(entity, ids=message_id)

        if not msg or not msg.media:
            return "No media found in the specified message."

        return str(msg.media)
    except Exception as e:
        return log_and_format_error("get_media_info", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Search Public Chats", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def search_public_chats(query: str, limit: int = 20, account: str = None) -> str:
    """
    Search for public chats, channels, or bots by username or title.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.SearchRequest(q=query, limit=limit))
        entities = [format_entity(e) for e in result.chats + result.users]
        return json.dumps(entities, indent=2)
    except Exception as e:
        return log_and_format_error("search_public_chats", e, query=query, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Search Messages", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def search_messages(
    chat_id: Union[int, str], query: str, limit: int = 20, account: str = None
) -> str:
    """
    Search for messages in a chat by text.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        messages = await cl.get_messages(entity, limit=limit, search=query)

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"
            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info} | Message: {msg.message}"
            )
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "search_messages", e, chat_id=chat_id, query=query, limit=limit
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search Global Messages",
        openWorldHint=True,
        readOnlyHint=True,
    )
)
@with_account(readonly=True)
async def search_global(
    query: str, page: int = 1, page_size: int = 20, account: str = None
) -> str:
    """
    Search for messages across all public chats and channels by text content.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        offset = (page - 1) * page_size
        messages = await cl.get_messages(None, limit=page_size, search=query, add_offset=offset)

        if not messages:
            return "No messages found for this page."

        lines = []
        for msg in messages:
            chat = msg.chat
            chat_name = (
                getattr(chat, "title", None) or getattr(chat, "first_name", "") or str(msg.chat_id)
            )
            sender_name = get_sender_name(msg)
            lines.append(
                f"Chat: {chat_name} | ID: {msg.id} | {sender_name} | "
                f"Date: {msg.date} | Message: {msg.message}"
            )

        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error(
            "search_global", e, query=query, page=page, page_size=page_size
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Resolve Username", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def resolve_username(username: str, account: str = None) -> str:
    """
    Resolve a username to a user or chat ID.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.contacts.ResolveUsernameRequest(username=username))
        return str(result)
    except Exception as e:
        return log_and_format_error("resolve_username", e, username=username)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Full User", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_full_user(username: Union[int, str], account: str = None) -> str:
    """
    Get full profile info of a Telegram user including bio/about text,
    personal channel link, and other profile details.

    Args:
        username: The username (without @) or user ID to look up.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(username, cl)
        full = await cl(functions.users.GetFullUserRequest(id=entity))

        user = full.users[0] if full.users else None
        full_user = full.full_user

        personal_channel_id = getattr(full_user, "personal_channel_id", None)
        personal_channel = None
        if personal_channel_id:
            try:
                ch = await cl.get_entity(personal_channel_id)
                ch_username = getattr(ch, "username", None)
                personal_channel = (
                    f"https://t.me/{ch_username}" if ch_username else str(personal_channel_id)
                )
            except Exception:
                personal_channel = str(personal_channel_id)

        result = {
            "id": user.id if user else None,
            "first_name": getattr(user, "first_name", None) if user else None,
            "last_name": getattr(user, "last_name", None) if user else None,
            "username": getattr(user, "username", None) if user else None,
            "phone": getattr(user, "phone", None) if user else None,
            "bio": full_user.about or "",
            "personal_channel": personal_channel,
            "bot": getattr(user, "bot", False) if user else False,
            "verified": getattr(user, "verified", False) if user else False,
            "premium": getattr(user, "premium", False) if user else False,
            "common_chats_count": getattr(full_user, "common_chats_count", None),
        }

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error("get_full_user", e, username=username)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Full Chat", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_full_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get full info of a channel or group including description/about text.

    Args:
        chat_id: The channel/group username (without @) or ID.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        full = await cl(functions.channels.GetFullChannelRequest(channel=entity))

        chat = full.chats[0] if full.chats else None
        full_chat = full.full_chat

        result = {
            "id": chat.id if chat else None,
            "title": getattr(chat, "title", None) if chat else None,
            "username": getattr(chat, "username", None) if chat else None,
            "about": full_chat.about or "",
            "participants_count": getattr(full_chat, "participants_count", None),
            "linked_chat_id": getattr(full_chat, "linked_chat_id", None),
        }

        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return log_and_format_error("get_full_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Mute Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def mute_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Mute notifications for a chat.
    """
    try:
        cl = get_client(account)
        from telethon.tl.types import InputPeerNotifySettings

        peer = await resolve_entity(chat_id, cl)
        await cl(
            functions.account.UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(mute_until=2**31 - 1)
            )
        )
        return f"Chat {chat_id} muted."
    except (ImportError, AttributeError) as type_err:
        try:
            # Alternative approach directly using raw API
            peer = await resolve_input_entity(chat_id, cl)
            await cl(
                functions.account.UpdateNotifySettingsRequest(
                    peer=peer,
                    settings={
                        "mute_until": 2**31 - 1,  # Far future
                        "show_previews": False,
                        "silent": True,
                    },
                )
            )
            return f"Chat {chat_id} muted (using alternative method)."
        except Exception as alt_e:
            logger.exception(f"mute_chat (alt method) failed (chat_id={chat_id})")
            return log_and_format_error("mute_chat", alt_e, chat_id=chat_id)
    except Exception as e:
        logger.exception(f"mute_chat failed (chat_id={chat_id})")
        return log_and_format_error("mute_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unmute Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def unmute_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Unmute notifications for a chat.
    """
    try:
        cl = get_client(account)
        from telethon.tl.types import InputPeerNotifySettings

        peer = await resolve_entity(chat_id, cl)
        await cl(
            functions.account.UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(mute_until=0)
            )
        )
        return f"Chat {chat_id} unmuted."
    except (ImportError, AttributeError) as type_err:
        try:
            # Alternative approach directly using raw API
            peer = await resolve_input_entity(chat_id, cl)
            await cl(
                functions.account.UpdateNotifySettingsRequest(
                    peer=peer,
                    settings={
                        "mute_until": 0,  # Unmute (current time)
                        "show_previews": True,
                        "silent": False,
                    },
                )
            )
            return f"Chat {chat_id} unmuted (using alternative method)."
        except Exception as alt_e:
            logger.exception(f"unmute_chat (alt method) failed (chat_id={chat_id})")
            return log_and_format_error("unmute_chat", alt_e, chat_id=chat_id)
    except Exception as e:
        logger.exception(f"unmute_chat failed (chat_id={chat_id})")
        return log_and_format_error("unmute_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Archive Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def archive_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Archive a chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        peer = utils.get_input_peer(entity)
        await cl(
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[types.InputFolderPeer(peer=peer, folder_id=1)]
            )
        )
        return f"Chat {chat_id} archived."
    except Exception as e:
        return log_and_format_error("archive_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Unarchive Chat", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def unarchive_chat(chat_id: Union[int, str], account: str = None) -> str:
    """
    Unarchive a chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        peer = utils.get_input_peer(entity)
        await cl(
            functions.folders.EditPeerFoldersRequest(
                folder_peers=[types.InputFolderPeer(peer=peer, folder_id=0)]
            )
        )
        return f"Chat {chat_id} unarchived."
    except Exception as e:
        return log_and_format_error("unarchive_chat", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Sticker Sets", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_sticker_sets(account: str = None) -> str:
    """
    Get all sticker sets.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.messages.GetAllStickersRequest(hash=0))
        return json.dumps([s.title for s in result.sets], indent=2)
    except Exception as e:
        return log_and_format_error("get_sticker_sets", e)


@mcp.tool(
    annotations=ToolAnnotations(title="Send Sticker", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def send_sticker(
    chat_id: Union[int, str],
    file_path: str,
    ctx: Optional[Context] = None,
    account: str = None,
) -> str:
    """
    Send a sticker to a chat. File must be a valid .webp sticker file.

    Args:
        chat_id: The chat ID or username.
        file_path: Absolute or relative path under allowed roots to the .webp sticker file.
    """
    try:
        cl = get_client(account)
        safe_path, path_error = await _resolve_readable_file_path(
            raw_path=file_path,
            ctx=ctx,
            tool_name="send_sticker",
        )
        if path_error:
            return path_error

        entity = await resolve_entity(chat_id, cl)
        await cl.send_file(entity, str(safe_path), force_document=False)
        return f"Sticker sent to chat {chat_id} from {safe_path}."
    except Exception as e:
        return log_and_format_error("send_sticker", e, chat_id=chat_id, file_path=file_path)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Gif Search", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
async def get_gif_search(query: str, limit: int = 10, account: str = None) -> str:
    """
    Search for GIFs by query. Returns a list of Telegram document IDs (not file paths).

    Args:
        query: Search term for GIFs.
        limit: Max number of GIFs to return.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Try approach 1: SearchGifsRequest
        try:
            result = await cl(
                functions.messages.SearchGifsRequest(q=query, offset_id=0, limit=limit)
            )
            if not result.gifs:
                return "[]"
            return json.dumps(
                [g.document.id for g in result.gifs], indent=2, default=json_serializer
            )
        except (AttributeError, ImportError):
            # Fallback approach: Use SearchRequest with GIF filter
            try:
                from telethon.tl.types import InputMessagesFilterGif

                result = await cl(
                    functions.messages.SearchRequest(
                        peer="gif",
                        q=query,
                        filter=InputMessagesFilterGif(),
                        min_date=None,
                        max_date=None,
                        offset_id=0,
                        add_offset=0,
                        limit=limit,
                        max_id=0,
                        min_id=0,
                        hash=0,
                    )
                )
                if not result or not hasattr(result, "messages") or not result.messages:
                    return "[]"
                # Extract document IDs from any messages with media
                gif_ids = []
                for msg in result.messages:
                    if hasattr(msg, "media") and msg.media and hasattr(msg.media, "document"):
                        gif_ids.append(msg.media.document.id)
                return json.dumps(gif_ids, default=json_serializer)
            except Exception as inner_e:
                # Last resort: Try to fetch from a public bot
                return f"Could not search GIFs using available methods: {inner_e}"
    except Exception as e:
        logger.exception(f"get_gif_search failed (query={query}, limit={limit})")
        return log_and_format_error("get_gif_search", e, query=query, limit=limit)


@mcp.tool(annotations=ToolAnnotations(title="Send Gif", openWorldHint=True, destructiveHint=True))
@with_account(readonly=False)
@validate_id("chat_id")
async def send_gif(chat_id: Union[int, str], gif_id: int, account: str = None) -> str:
    """
    Send a GIF to a chat by Telegram GIF document ID (not a file path).

    Args:
        chat_id: The chat ID or username.
        gif_id: Telegram document ID for the GIF (from get_gif_search).
    """
    try:
        cl = get_client(account)
        if not isinstance(gif_id, int):
            return "gif_id must be a Telegram document ID (integer), not a file path. Use get_gif_search to find IDs."
        entity = await resolve_entity(chat_id, cl)
        await cl.send_file(entity, gif_id)
        return f"GIF sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error("send_gif", e, chat_id=chat_id, gif_id=gif_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Send Contact", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
@validate_id("chat_id")
async def send_contact(
    chat_id: Union[int, str],
    phone_number: str,
    first_name: str,
    last_name: str = "",
    vcard: str = "",
    account: str = None,
) -> str:
    """
    Send a contact to a chat.
    Args:
        chat_id: The chat ID or username.
        phone_number: Contact's phone number.
        first_name: Contact's first name.
        last_name: Contact's last name (optional).
        vcard: Additional vCard data (optional).
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        from telethon.tl.types import InputMediaContact
        import random

        await cl(
            functions.messages.SendMediaRequest(
                peer=entity,
                media=InputMediaContact(
                    phone_number=phone_number,
                    first_name=first_name,
                    last_name=last_name,
                    vcard=vcard,
                ),
                message="",
                random_id=random.randint(0, 2**63 - 1),
            )
        )
        return f"Contact sent to chat {chat_id}."
    except Exception as e:
        return log_and_format_error("send_contact", e, chat_id=chat_id, phone_number=phone_number)


@mcp.tool(annotations=ToolAnnotations(title="Get Bot Info", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def get_bot_info(bot_username: str, account: str = None) -> str:
    """
    Get information about a bot by username.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(bot_username, cl)
        if not entity:
            return f"Bot with username {bot_username} not found."

        result = await cl(functions.users.GetFullUserRequest(id=entity))

        # Create a more structured, serializable response
        if hasattr(result, "to_dict"):
            # Use custom serializer to handle non-serializable types
            return json.dumps(result.to_dict(), indent=2, default=json_serializer)
        else:
            # Fallback if to_dict is not available
            info = {
                "bot_info": {
                    "id": entity.id,
                    "username": entity.username,
                    "first_name": entity.first_name,
                    "last_name": getattr(entity, "last_name", ""),
                    "is_bot": getattr(entity, "bot", False),
                    "verified": getattr(entity, "verified", False),
                }
            }
            if hasattr(result, "full_user") and hasattr(result.full_user, "about"):
                info["bot_info"]["about"] = result.full_user.about
            return json.dumps(info, indent=2)
    except Exception as e:
        logger.exception(f"get_bot_info failed (bot_username={bot_username})")
        return log_and_format_error("get_bot_info", e, bot_username=bot_username)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Set Bot Commands", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def set_bot_commands(bot_username: str, commands: list, account: str = None) -> str:
    """
    Set bot commands for a bot you own.
    Note: This function can only be used if the Telegram client is a bot account.
    Regular user accounts cannot set bot commands.

    Args:
        bot_username: The username of the bot to set commands for.
        commands: List of command dictionaries with 'command' and 'description' keys.
    """
    try:
        cl = get_client(account)
        # First check if the current client is a bot
        me = await cl.get_me()
        if not getattr(me, "bot", False):
            return "Error: This function can only be used by bot accounts. Your current Telegram account is a regular user account, not a bot."

        # Import required types
        from telethon.tl.types import BotCommand, BotCommandScopeDefault
        from telethon.tl.functions.bots import SetBotCommandsRequest

        # Create BotCommand objects from the command dictionaries
        bot_commands = [
            BotCommand(command=c["command"], description=c["description"]) for c in commands
        ]

        # Get the bot entity
        bot = await resolve_entity(bot_username, cl)

        # Set the commands with proper scope
        await cl(
            SetBotCommandsRequest(
                scope=BotCommandScopeDefault(),
                lang_code="en",  # Default language code
                commands=bot_commands,
            )
        )

        return f"Bot commands set for {bot_username}."
    except ImportError as ie:
        logger.exception(f"set_bot_commands failed - ImportError: {ie}")
        return log_and_format_error("set_bot_commands", ie)
    except Exception as e:
        logger.exception(f"set_bot_commands failed (bot_username={bot_username})")
        return log_and_format_error("set_bot_commands", e, bot_username=bot_username)


@mcp.tool(annotations=ToolAnnotations(title="Get History", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
@validate_id("chat_id")
async def get_history(chat_id: Union[int, str], limit: int = 100, account: str = None) -> str:
    """
    Get full chat history (up to limit).
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)
        messages = await cl.get_messages(entity, limit=limit)

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"
            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info} | Message: {msg.message}"
            )
        return "\n".join(lines)
    except Exception as e:
        return log_and_format_error("get_history", e, chat_id=chat_id, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Get User Photos", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("user_id")
async def get_user_photos(user_id: Union[int, str], limit: int = 10, account: str = None) -> str:
    """
    Get profile photos of a user.
    """
    try:
        cl = get_client(account)
        user = await resolve_entity(user_id, cl)
        photos = await cl(
            functions.photos.GetUserPhotosRequest(user_id=user, offset=0, max_id=0, limit=limit)
        )
        return json.dumps([p.id for p in photos.photos], indent=2)
    except Exception as e:
        return log_and_format_error("get_user_photos", e, user_id=user_id, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(title="Get User Status", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("user_id")
async def get_user_status(user_id: Union[int, str], account: str = None) -> str:
    """
    Get the online status of a user.
    """
    try:
        cl = get_client(account)
        user = await resolve_entity(user_id, cl)
        return str(user.status)
    except Exception as e:
        return log_and_format_error("get_user_status", e, user_id=user_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Recent Actions", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_recent_actions(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get recent admin actions (admin log) in a group or channel.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(
            functions.channels.GetAdminLogRequest(
                channel=chat_id,
                q="",
                events_filter=None,
                admins=[],
                max_id=0,
                min_id=0,
                limit=20,
            )
        )

        if not result or not result.events:
            return "No recent admin actions found."

        # Use the custom serializer to handle datetime objects
        return json.dumps([e.to_dict() for e in result.events], indent=2, default=json_serializer)
    except Exception as e:
        logger.exception(f"get_recent_actions failed (chat_id={chat_id})")
        return log_and_format_error("get_recent_actions", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Get Pinned Messages", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_pinned_messages(chat_id: Union[int, str], account: str = None) -> str:
    """
    Get all pinned messages in a chat.
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Use correct filter based on Telethon version
        try:
            # Try newer Telethon approach
            from telethon.tl.types import InputMessagesFilterPinned

            messages = await cl.get_messages(entity, filter=InputMessagesFilterPinned())
        except (ImportError, AttributeError):
            # Fallback - try without filter and manually filter pinned
            all_messages = await cl.get_messages(entity, limit=50)
            messages = [m for m in all_messages if getattr(m, "pinned", False)]

        if not messages:
            return "No pinned messages found in this chat."

        lines = []
        for msg in messages:
            sender_name = get_sender_name(msg)
            reply_info = ""
            if msg.reply_to and msg.reply_to.reply_to_msg_id:
                reply_info = f" | reply to {msg.reply_to.reply_to_msg_id}"
            lines.append(
                f"ID: {msg.id} | {sender_name} | Date: {msg.date}{reply_info} | Message: {msg.message or '[Media/No text]'}"
            )

        return "\n".join(lines)
    except Exception as e:
        logger.exception(f"get_pinned_messages failed (chat_id={chat_id})")
        return log_and_format_error("get_pinned_messages", e, chat_id=chat_id)


@mcp.tool(
    annotations=ToolAnnotations(title="Create Poll", openWorldHint=True, destructiveHint=True)
)
@with_account(readonly=False)
async def create_poll(
    chat_id: int,
    question: str,
    options: list,
    multiple_choice: bool = False,
    quiz_mode: bool = False,
    public_votes: bool = True,
    close_date: str = None,
    account: str = None,
) -> str:
    """
    Create a poll in a chat using Telegram's native poll feature.

    Args:
        chat_id: The ID of the chat to send the poll to
        question: The poll question
        options: List of answer options (2-10 options)
        multiple_choice: Whether users can select multiple answers
        quiz_mode: Whether this is a quiz (has correct answer)
        public_votes: Whether votes are public
        close_date: Optional close date in ISO format (YYYY-MM-DD HH:MM:SS)
    """
    try:
        cl = get_client(account)
        entity = await resolve_entity(chat_id, cl)

        # Validate options
        if len(options) < 2:
            return "Error: Poll must have at least 2 options."
        if len(options) > 10:
            return "Error: Poll can have at most 10 options."

        # Parse close date if provided
        close_date_obj = None
        if close_date:
            try:
                close_date_obj = datetime.fromisoformat(close_date.replace("Z", "+00:00"))
            except ValueError:
                return f"Invalid close_date format. Use YYYY-MM-DD HH:MM:SS format."

        # Create the poll using InputMediaPoll with SendMediaRequest
        from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, TextWithEntities
        import random

        poll = Poll(
            id=random.randint(0, 2**63 - 1),
            question=TextWithEntities(text=question, entities=[]),
            answers=[
                PollAnswer(text=TextWithEntities(text=option, entities=[]), option=bytes([i]))
                for i, option in enumerate(options)
            ],
            multiple_choice=multiple_choice,
            quiz=quiz_mode,
            public_voters=public_votes,
            close_date=close_date_obj,
        )

        result = await cl(
            functions.messages.SendMediaRequest(
                peer=entity,
                media=InputMediaPoll(poll=poll),
                message="",
                random_id=random.randint(0, 2**63 - 1),
            )
        )

        return f"Poll created successfully in chat {chat_id}."
    except Exception as e:
        logger.exception(f"create_poll failed (chat_id={chat_id}, question='{question}')")
        return log_and_format_error(
            "create_poll", e, chat_id=chat_id, question=question, options=options
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Send Reaction", openWorldHint=True, destructiveHint=False, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def send_reaction(
    chat_id: Union[int, str],
    message_id: int,
    emoji: str,
    big: bool = False,
    account: str = None,
) -> str:
    """
    Send a reaction to a message.

    Args:
        chat_id: The chat ID or username
        message_id: The message ID to react to
        emoji: The emoji to react with (e.g., "👍", "❤️", "🔥", "😂", "😮", "😢", "🎉", "💩", "👎")
        big: Whether to show a big animation for the reaction (default: False)
    """
    try:
        cl = get_client(account)
        from telethon.tl.types import ReactionEmoji

        peer = await resolve_input_entity(chat_id, cl)
        await cl(
            functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                big=big,
                reaction=[ReactionEmoji(emoticon=emoji)],
            )
        )
        return f"Reaction '{emoji}' sent to message {message_id} in chat {chat_id}."
    except Exception as e:
        logger.exception(
            f"send_reaction failed (chat_id={chat_id}, message_id={message_id}, emoji={emoji})"
        )
        return log_and_format_error(
            "send_reaction", e, chat_id=chat_id, message_id=message_id, emoji=emoji
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Remove Reaction", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def remove_reaction(
    chat_id: Union[int, str],
    message_id: int,
    account: str = None,
) -> str:
    """
    Remove your reaction from a message.

    Args:
        chat_id: The chat ID or username
        message_id: The message ID to remove reaction from
    """
    try:
        cl = get_client(account)
        peer = await resolve_input_entity(chat_id, cl)
        await cl(
            functions.messages.SendReactionRequest(
                peer=peer,
                msg_id=message_id,
                reaction=[],  # Empty list removes reaction
            )
        )
        return f"Reaction removed from message {message_id} in chat {chat_id}."
    except Exception as e:
        logger.exception(f"remove_reaction failed (chat_id={chat_id}, message_id={message_id})")
        return log_and_format_error("remove_reaction", e, chat_id=chat_id, message_id=message_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Get Message Reactions", openWorldHint=True, readOnlyHint=True, idempotentHint=True
    )
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_message_reactions(
    chat_id: Union[int, str],
    message_id: int,
    limit: int = 50,
    account: str = None,
) -> str:
    """
    Get the list of reactions on a message.

    Args:
        chat_id: The chat ID or username
        message_id: The message ID to get reactions from
        limit: Maximum number of users to return per reaction (default: 50)
    """
    try:
        cl = get_client(account)
        from telethon.tl.types import ReactionEmoji, ReactionCustomEmoji

        peer = await resolve_input_entity(chat_id, cl)

        result = await cl(
            functions.messages.GetMessageReactionsListRequest(
                peer=peer,
                id=message_id,
                limit=limit,
            )
        )

        if not result.reactions:
            return f"No reactions on message {message_id} in chat {chat_id}."

        reactions_data = []
        for reaction in result.reactions:
            user_id = reaction.peer_id.user_id if hasattr(reaction.peer_id, "user_id") else None
            emoji = None
            if isinstance(reaction.reaction, ReactionEmoji):
                emoji = reaction.reaction.emoticon
            elif isinstance(reaction.reaction, ReactionCustomEmoji):
                emoji = f"custom:{reaction.reaction.document_id}"

            reactions_data.append(
                {
                    "user_id": user_id,
                    "emoji": emoji,
                    "date": reaction.date.isoformat() if reaction.date else None,
                }
            )

        return json.dumps(
            {
                "message_id": message_id,
                "chat_id": str(chat_id),
                "reactions": reactions_data,
                "count": len(reactions_data),
            },
            indent=2,
            default=json_serializer,
        )
    except Exception as e:
        logger.exception(
            f"get_message_reactions failed (chat_id={chat_id}, message_id={message_id})"
        )
        return log_and_format_error(
            "get_message_reactions", e, chat_id=chat_id, message_id=message_id
        )


# ============================================================================
# DRAFT MANAGEMENT TOOLS
# ============================================================================


@mcp.tool(
    annotations=ToolAnnotations(
        title="Save Draft", openWorldHint=True, destructiveHint=False, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def save_draft(
    chat_id: Union[int, str],
    message: str,
    reply_to_msg_id: Optional[int] = None,
    no_webpage: bool = False,
    account: str = None,
) -> str:
    """
    Save a draft message to a chat or channel. The draft will appear in the Telegram
    app's input field when you open that chat, allowing you to review and send it manually.

    Args:
        chat_id: The chat ID or username/channel to save the draft to
        message: The draft message text
        reply_to_msg_id: Optional message ID to reply to
        no_webpage: If True, disable link preview in the draft
    """
    try:
        cl = get_client(account)
        peer = await resolve_input_entity(chat_id, cl)

        # Build reply_to parameter if provided
        reply_to = None
        if reply_to_msg_id:
            from telethon.tl.types import InputReplyToMessage

            reply_to = InputReplyToMessage(reply_to_msg_id=reply_to_msg_id)

        await cl(
            functions.messages.SaveDraftRequest(
                peer=peer,
                message=message,
                no_webpage=no_webpage,
                reply_to=reply_to,
            )
        )

        return f"Draft saved to chat {chat_id}. Open the chat in Telegram to see and send it."
    except Exception as e:
        logger.exception(f"save_draft failed (chat_id={chat_id})")
        return log_and_format_error("save_draft", e, chat_id=chat_id)


@mcp.tool(annotations=ToolAnnotations(title="Get Drafts", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def get_drafts(account: str = None) -> str:
    """
    Get all draft messages across all chats.
    Returns a list of drafts with their chat info and message content.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.messages.GetAllDraftsRequest())

        # The result contains updates with draft info
        drafts_info = []

        # GetAllDraftsRequest returns Updates object with updates array
        if hasattr(result, "updates"):
            for update in result.updates:
                if hasattr(update, "draft") and update.draft:
                    draft = update.draft
                    peer_id = None

                    # Extract peer ID based on type
                    if hasattr(update, "peer"):
                        peer = update.peer
                        if hasattr(peer, "user_id"):
                            peer_id = peer.user_id
                        elif hasattr(peer, "chat_id"):
                            peer_id = -peer.chat_id
                        elif hasattr(peer, "channel_id"):
                            peer_id = -1000000000000 - peer.channel_id

                    draft_data = {
                        "peer_id": peer_id,
                        "message": getattr(draft, "message", ""),
                        "date": (
                            draft.date.isoformat()
                            if hasattr(draft, "date") and draft.date
                            else None
                        ),
                        "no_webpage": getattr(draft, "no_webpage", False),
                        "reply_to_msg_id": (
                            draft.reply_to.reply_to_msg_id
                            if hasattr(draft, "reply_to") and draft.reply_to
                            else None
                        ),
                    }
                    drafts_info.append(draft_data)

        if not drafts_info:
            return "No drafts found."

        return json.dumps(
            {"drafts": drafts_info, "count": len(drafts_info)}, indent=2, default=json_serializer
        )
    except Exception as e:
        logger.exception("get_drafts failed")
        return log_and_format_error("get_drafts", e)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Clear Draft", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def clear_draft(chat_id: Union[int, str], account: str = None) -> str:
    """
    Clear/delete a draft from a specific chat.

    Args:
        chat_id: The chat ID or username to clear the draft from
    """
    try:
        cl = get_client(account)
        peer = await resolve_input_entity(chat_id, cl)

        # Saving an empty message clears the draft
        await cl(
            functions.messages.SaveDraftRequest(
                peer=peer,
                message="",
            )
        )

        return f"Draft cleared from chat {chat_id}."
    except Exception as e:
        logger.exception(f"clear_draft failed (chat_id={chat_id})")
        return log_and_format_error("clear_draft", e, chat_id=chat_id)


# ============================================================================
# FOLDER MANAGEMENT TOOLS
# ============================================================================


@mcp.tool(annotations=ToolAnnotations(title="List Folders", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def list_folders(account: str = None) -> str:
    """
    Get all dialog folders (filters) with their IDs, names, and emoji.
    Returns a list of folders that can be used with other folder tools.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        result = await cl(functions.messages.GetDialogFiltersRequest())

        folders = []
        for f in result.filters:
            # Skip system default folder
            if isinstance(f, DialogFilterDefault):
                continue

            if isinstance(f, DialogFilter):
                # Handle title which can be str or TextWithEntities
                title = f.title
                if isinstance(title, TextWithEntities):
                    title = title.text
                folder_data = {
                    "id": f.id,
                    "title": title,
                    "emoticon": getattr(f, "emoticon", None),
                    "contacts": getattr(f, "contacts", False),
                    "non_contacts": getattr(f, "non_contacts", False),
                    "groups": getattr(f, "groups", False),
                    "broadcasts": getattr(f, "broadcasts", False),
                    "bots": getattr(f, "bots", False),
                    "exclude_muted": getattr(f, "exclude_muted", False),
                    "exclude_read": getattr(f, "exclude_read", False),
                    "exclude_archived": getattr(f, "exclude_archived", False),
                    "included_peers_count": len(getattr(f, "include_peers", [])),
                    "excluded_peers_count": len(getattr(f, "exclude_peers", [])),
                    "pinned_peers_count": len(getattr(f, "pinned_peers", [])),
                }
                folders.append(folder_data)

            elif isinstance(f, DialogFilterChatlist):
                # Shared folders use DialogFilterChatlist type
                title = f.title
                if isinstance(title, TextWithEntities):
                    title = title.text
                folder_data = {
                    "id": f.id,
                    "title": title,
                    "emoticon": getattr(f, "emoticon", None),
                    "type": "shared",
                    "included_peers_count": len(getattr(f, "include_peers", [])),
                    "pinned_peers_count": len(getattr(f, "pinned_peers", [])),
                }
                folders.append(folder_data)

        if not folders:
            return "No folders found. Create one with create_folder tool."

        return json.dumps(
            {"folders": folders, "count": len(folders)}, indent=2, default=json_serializer
        )
    except Exception as e:
        logger.exception("list_folders failed")
        return log_and_format_error("list_folders", e, ErrorCategory.FOLDER)


@mcp.tool(annotations=ToolAnnotations(title="Get Folder", openWorldHint=True, readOnlyHint=True))
@with_account(readonly=True)
async def get_folder(folder_id: int, account: str = None) -> str:
    """
    Get detailed information about a specific folder including all included chats.

    Args:
        folder_id: The folder ID (get from list_folders)
    """
    try:
        cl = get_client(account)
        result = await cl(functions.messages.GetDialogFiltersRequest())

        target_folder = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                target_folder = f
                break

        if not target_folder:
            return (
                f"Folder with ID {folder_id} not found. Use list_folders to see available folders."
            )

        # Resolve included peers to readable names
        included_chats = []
        for peer in getattr(target_folder, "include_peers", []):
            try:
                entity = await resolve_entity(peer, cl)
                chat_info = {
                    "id": entity.id,
                    "name": getattr(entity, "title", None)
                    or getattr(entity, "first_name", "Unknown"),
                    "type": get_entity_type(entity),
                }
                if hasattr(entity, "username") and entity.username:
                    chat_info["username"] = entity.username
                included_chats.append(chat_info)
            except Exception:
                included_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Resolve excluded peers
        excluded_chats = []
        for peer in getattr(target_folder, "exclude_peers", []):
            try:
                entity = await resolve_entity(peer, cl)
                chat_info = {
                    "id": entity.id,
                    "name": getattr(entity, "title", None)
                    or getattr(entity, "first_name", "Unknown"),
                    "type": get_entity_type(entity),
                }
                excluded_chats.append(chat_info)
            except Exception:
                excluded_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Resolve pinned peers
        pinned_chats = []
        for peer in getattr(target_folder, "pinned_peers", []):
            try:
                entity = await resolve_entity(peer, cl)
                chat_info = {
                    "id": entity.id,
                    "name": getattr(entity, "title", None)
                    or getattr(entity, "first_name", "Unknown"),
                    "type": get_entity_type(entity),
                }
                pinned_chats.append(chat_info)
            except Exception:
                pinned_chats.append({"id": str(peer), "name": "Unknown", "type": "Unknown"})

        # Handle title which can be str or TextWithEntities
        title = target_folder.title
        if isinstance(title, TextWithEntities):
            title = title.text

        folder_data = {
            "id": target_folder.id,
            "title": title,
            "emoticon": getattr(target_folder, "emoticon", None),
            "included_chats": included_chats,
            "excluded_chats": excluded_chats,
            "pinned_chats": pinned_chats,
        }

        if isinstance(target_folder, DialogFilterChatlist):
            folder_data["type"] = "shared"
        else:
            folder_data["filters"] = {
                "contacts": getattr(target_folder, "contacts", False),
                "non_contacts": getattr(target_folder, "non_contacts", False),
                "groups": getattr(target_folder, "groups", False),
                "broadcasts": getattr(target_folder, "broadcasts", False),
                "bots": getattr(target_folder, "bots", False),
                "exclude_muted": getattr(target_folder, "exclude_muted", False),
                "exclude_read": getattr(target_folder, "exclude_read", False),
                "exclude_archived": getattr(target_folder, "exclude_archived", False),
            }

        return json.dumps(folder_data, indent=2, default=json_serializer)
    except Exception as e:
        logger.exception(f"get_folder failed (folder_id={folder_id})")
        return log_and_format_error("get_folder", e, ErrorCategory.FOLDER, folder_id=folder_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Folder", openWorldHint=True, destructiveHint=True, idempotentHint=False
    )
)
@with_account(readonly=False)
async def create_folder(
    title: str,
    emoticon: Optional[str] = None,
    chat_ids: Optional[List[Union[int, str]]] = None,
    contacts: bool = False,
    non_contacts: bool = False,
    groups: bool = False,
    broadcasts: bool = False,
    bots: bool = False,
    exclude_muted: bool = False,
    exclude_read: bool = False,
    exclude_archived: bool = True,
    account: str = None,
) -> str:
    """
    Create a new dialog folder.

    Args:
        title: Folder name (required)
        emoticon: Folder emoji (optional, e.g., "📁", "🏠", "💼")
        chat_ids: List of chat IDs or usernames to include (optional)
        contacts: Include all contacts
        non_contacts: Include all non-contacts
        groups: Include all groups
        broadcasts: Include all channels
        bots: Include all bots
        exclude_muted: Exclude muted chats
        exclude_read: Exclude read chats
        exclude_archived: Exclude archived chats (default True)
    """
    try:
        cl = get_client(account)
        # Get existing folders to check count and find next ID
        result = await cl(functions.messages.GetDialogFiltersRequest())

        existing_ids = set()
        folder_count = 0
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)):
                existing_ids.add(f.id)
                folder_count += 1

        # Telegram limit: max 10 custom folders
        if folder_count >= 10:
            return "Cannot create folder: Telegram limit is 10 folders. Delete one first."

        # Find next available ID (IDs 0 and 1 are reserved for system)
        new_id = 2
        while new_id in existing_ids:
            new_id += 1

        # Resolve chat_ids to input peers
        include_peers = []
        if chat_ids:
            for chat_id in chat_ids:
                try:
                    peer = await resolve_input_entity(chat_id, cl)
                    include_peers.append(peer)
                except Exception as e:
                    return f"Failed to resolve chat '{chat_id}': {str(e)}"

        # Create the folder (title must be TextWithEntities)
        title_obj = TextWithEntities(text=title, entities=[])
        new_filter = DialogFilter(
            id=new_id,
            title=title_obj,
            emoticon=emoticon,
            pinned_peers=[],
            include_peers=include_peers,
            exclude_peers=[],
            contacts=contacts,
            non_contacts=non_contacts,
            groups=groups,
            broadcasts=broadcasts,
            bots=bots,
            exclude_muted=exclude_muted,
            exclude_read=exclude_read,
            exclude_archived=exclude_archived,
        )

        await cl(functions.messages.UpdateDialogFilterRequest(id=new_id, filter=new_filter))

        return json.dumps(
            {
                "success": True,
                "folder_id": new_id,
                "title": title,
                "emoticon": emoticon,
                "included_chats_count": len(include_peers),
            },
            indent=2,
        )
    except Exception as e:
        logger.exception(f"create_folder failed (title={title})")
        return log_and_format_error("create_folder", e, ErrorCategory.FOLDER, title=title)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Add Chat to Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def add_chat_to_folder(
    folder_id: int,
    chat_id: Union[int, str],
    pinned: bool = False,
    account: str = None,
) -> str:
    """
    Add a chat to an existing folder.

    Args:
        folder_id: The folder ID (get from list_folders)
        chat_id: Chat ID or username to add
        pinned: Pin the chat in this folder (default False)
    """
    try:
        cl = get_client(account)
        # Get the folder
        result = await cl(functions.messages.GetDialogFiltersRequest())

        target_folder = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                target_folder = f
                break

        if not target_folder:
            return (
                f"Folder with ID {folder_id} not found. Use list_folders to see available folders."
            )

        # Resolve chat to input peer
        try:
            peer = await resolve_input_entity(chat_id, cl)
        except Exception as e:
            return f"Failed to resolve chat '{chat_id}': {str(e)}"

        # Check if already included (idempotent)
        include_peers = list(getattr(target_folder, "include_peers", []))
        pinned_peers = list(getattr(target_folder, "pinned_peers", []))

        # Get peer ID for comparison
        peer_id = utils.get_peer_id(peer)
        already_included = any(utils.get_peer_id(p) == peer_id for p in include_peers)
        already_pinned = any(utils.get_peer_id(p) == peer_id for p in pinned_peers)

        if already_included and (not pinned or already_pinned):
            return f"Chat {chat_id} is already in folder {folder_id}."

        # Add to appropriate list
        if not already_included:
            include_peers.append(peer)
        if pinned and not already_pinned:
            pinned_peers.append(peer)

        # Update the folder (keep all original attributes)
        if isinstance(target_folder, DialogFilterChatlist):
            updated_filter = DialogFilterChatlist(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )
        else:
            updated_filter = DialogFilter(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                exclude_peers=list(getattr(target_folder, "exclude_peers", [])),
                contacts=getattr(target_folder, "contacts", False),
                non_contacts=getattr(target_folder, "non_contacts", False),
                groups=getattr(target_folder, "groups", False),
                broadcasts=getattr(target_folder, "broadcasts", False),
                bots=getattr(target_folder, "bots", False),
                exclude_muted=getattr(target_folder, "exclude_muted", False),
                exclude_read=getattr(target_folder, "exclude_read", False),
                exclude_archived=getattr(target_folder, "exclude_archived", False),
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )

        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=updated_filter))

        return (
            f"Chat {chat_id} added to folder {folder_id}" + (" (pinned)" if pinned else "") + "."
        )
    except Exception as e:
        logger.exception(f"add_chat_to_folder failed (folder_id={folder_id}, chat_id={chat_id})")
        return log_and_format_error(
            "add_chat_to_folder", e, ErrorCategory.FOLDER, folder_id=folder_id, chat_id=chat_id
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Remove Chat from Folder",
        openWorldHint=True,
        destructiveHint=True,
        idempotentHint=True,
    )
)
@with_account(readonly=False)
@validate_id("chat_id")
async def remove_chat_from_folder(
    folder_id: int, chat_id: Union[int, str], account: str = None
) -> str:
    """
    Remove a chat from a folder.

    Args:
        folder_id: The folder ID (get from list_folders)
        chat_id: Chat ID or username to remove
    """
    try:
        cl = get_client(account)
        # Get the folder
        result = await cl(functions.messages.GetDialogFiltersRequest())

        target_folder = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                target_folder = f
                break

        if not target_folder:
            return (
                f"Folder with ID {folder_id} not found. Use list_folders to see available folders."
            )

        # Resolve chat to get peer ID
        try:
            peer = await resolve_input_entity(chat_id, cl)
            peer_id = utils.get_peer_id(peer)
        except Exception as e:
            return f"Failed to resolve chat '{chat_id}': {str(e)}"

        # Filter out the peer from both include and pinned lists
        include_peers = [
            p
            for p in getattr(target_folder, "include_peers", [])
            if utils.get_peer_id(p) != peer_id
        ]
        pinned_peers = [
            p
            for p in getattr(target_folder, "pinned_peers", [])
            if utils.get_peer_id(p) != peer_id
        ]

        original_include_count = len(getattr(target_folder, "include_peers", []))
        original_pinned_count = len(getattr(target_folder, "pinned_peers", []))

        # Check if anything was removed (idempotent)
        if (
            len(include_peers) == original_include_count
            and len(pinned_peers) == original_pinned_count
        ):
            return f"Chat {chat_id} was not in folder {folder_id}."

        # Update the folder (keep all original attributes)
        if isinstance(target_folder, DialogFilterChatlist):
            updated_filter = DialogFilterChatlist(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )
        else:
            updated_filter = DialogFilter(
                id=target_folder.id,
                title=target_folder.title,
                emoticon=getattr(target_folder, "emoticon", None),
                pinned_peers=pinned_peers,
                include_peers=include_peers,
                exclude_peers=list(getattr(target_folder, "exclude_peers", [])),
                contacts=getattr(target_folder, "contacts", False),
                non_contacts=getattr(target_folder, "non_contacts", False),
                groups=getattr(target_folder, "groups", False),
                broadcasts=getattr(target_folder, "broadcasts", False),
                bots=getattr(target_folder, "bots", False),
                exclude_muted=getattr(target_folder, "exclude_muted", False),
                exclude_read=getattr(target_folder, "exclude_read", False),
                exclude_archived=getattr(target_folder, "exclude_archived", False),
                title_noanimate=getattr(target_folder, "title_noanimate", None),
                color=getattr(target_folder, "color", None),
            )

        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=updated_filter))

        return f"Chat {chat_id} removed from folder {folder_id}."
    except Exception as e:
        logger.exception(
            f"remove_chat_from_folder failed (folder_id={folder_id}, chat_id={chat_id})"
        )
        return log_and_format_error(
            "remove_chat_from_folder",
            e,
            ErrorCategory.FOLDER,
            folder_id=folder_id,
            chat_id=chat_id,
        )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Folder", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def delete_folder(folder_id: int, account: str = None) -> str:
    """
    Delete a folder. Chats in the folder are preserved, only the folder is removed.

    Args:
        folder_id: The folder ID to delete (get from list_folders)
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # System folders (id < 2) cannot be deleted
        if folder_id < 2:
            return f"Cannot delete system folder (ID {folder_id}). Only custom folders can be deleted."

        # Check if folder exists
        result = await cl(functions.messages.GetDialogFiltersRequest())

        folder_exists = False
        folder_title = None
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)) and f.id == folder_id:
                folder_exists = True
                # Handle title which can be str or TextWithEntities
                title = f.title
                if isinstance(title, TextWithEntities):
                    title = title.text
                folder_title = title
                break

        if not folder_exists:
            return f"Folder with ID {folder_id} not found (may already be deleted)."

        # Delete by passing None as filter
        await cl(functions.messages.UpdateDialogFilterRequest(id=folder_id, filter=None))

        return f"Folder '{folder_title}' (ID {folder_id}) deleted. Chats are preserved."
    except Exception as e:
        logger.exception(f"delete_folder failed (folder_id={folder_id})")
        return log_and_format_error("delete_folder", e, ErrorCategory.FOLDER, folder_id=folder_id)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Reorder Folders", openWorldHint=True, destructiveHint=True, idempotentHint=True
    )
)
@with_account(readonly=False)
async def reorder_folders(folder_ids: List[int], account: str = None) -> str:
    """
    Change the order of folders in the folder list.

    Args:
        folder_ids: List of folder IDs in the desired order
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Get existing folders to validate
        result = await cl(functions.messages.GetDialogFiltersRequest())

        existing_ids = set()
        for f in result.filters:
            if isinstance(f, (DialogFilter, DialogFilterChatlist)):
                existing_ids.add(f.id)

        # Validate all provided IDs exist
        for fid in folder_ids:
            if fid not in existing_ids:
                return f"Folder ID {fid} not found. Use list_folders to see available folders."

        # Validate all existing folders are included
        if set(folder_ids) != existing_ids:
            missing = existing_ids - set(folder_ids)
            return f"All folder IDs must be included. Missing: {missing}"

        # Reorder
        await cl(functions.messages.UpdateDialogFiltersOrderRequest(order=folder_ids))

        return f"Folders reordered: {folder_ids}"
    except Exception as e:
        logger.exception(f"reorder_folders failed (folder_ids={folder_ids})")
        return log_and_format_error(
            "reorder_folders", e, ErrorCategory.FOLDER, folder_ids=folder_ids
        )


# ============================================================================
# CHAT DISCOVERY TOOLS
# ============================================================================


@mcp.tool(
    annotations=ToolAnnotations(title="Get Common Chats", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("user_id")
async def get_common_chats(
    user_id: Union[int, str], limit: int = 100, max_id: int = 0, account: str = None
) -> str:
    """
    List chats shared with a specific user.

    Args:
        user_id: The user ID or username to check shared chats for.
        limit: Maximum number of shared chats to return (max 100).
        max_id: Pagination cursor — pass the last chat ID from the previous
            page to fetch older shared chats. Use 0 (default) for the first page.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        # Telegram caps the limit at 100
        if limit > 100:
            limit = 100
        if limit < 1:
            limit = 1

        user_entity = await resolve_entity(user_id, cl)
        result = await cl(
            functions.messages.GetCommonChatsRequest(
                user_id=user_entity, max_id=max_id, limit=limit
            )
        )

        chats = getattr(result, "chats", []) or []
        if not chats:
            return f"No common chats found with user {user_id}."

        lines = []
        for chat in chats:
            line = f"Chat ID: {chat.id}"
            if hasattr(chat, "title") and chat.title:
                line += f", Title: {chat.title}"
            line += f", Type: {get_entity_type(chat)}"
            if hasattr(chat, "username") and chat.username:
                line += f", Username: @{chat.username}"
            lines.append(line)

        return "\n".join(lines)
    except Exception as e:
        logger.exception(
            f"get_common_chats failed (user_id={user_id}, limit={limit}, max_id={max_id})"
        )
        return log_and_format_error(
            "get_common_chats", e, user_id=user_id, limit=limit, max_id=max_id
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Message Read By", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_message_read_by(
    chat_id: Union[int, str], message_id: int, account: str = None
) -> str:
    """
    List user IDs who have read a specific message.

    Works in small groups and supergroups where read-marker tracking is
    enabled (Telegram exposes read receipts for groups up to a fixed size
    and only for messages sent within the last ~7 days).

    Args:
        chat_id: The chat ID or username.
        message_id: The message ID to check read receipts for.
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        from telethon.errors.rpcerrorlist import (
            ChatAdminRequiredError,
            UserNotParticipantError,
            MsgTooOldError,
            PeerIdInvalidError,
        )

        entity = await resolve_entity(chat_id, cl)
        try:
            result = await cl(
                functions.messages.GetMessageReadParticipantsRequest(
                    peer=entity, msg_id=message_id
                )
            )
        except MsgTooOldError:
            return (
                f"Read receipts unavailable for message {message_id} in chat "
                f"{chat_id}: message is too old or read receipts are disabled."
            )
        except ChatAdminRequiredError:
            return (
                f"Cannot read receipts for message {message_id} in chat {chat_id}: "
                f"admin rights are required."
            )
        except UserNotParticipantError:
            return (
                f"Cannot read receipts for message {message_id} in chat {chat_id}: "
                f"you are not a participant of this chat."
            )
        except PeerIdInvalidError:
            return f"Invalid chat: {chat_id}."

        # result is a list of ReadParticipantDate objects in newer Telethon,
        # or a list of user IDs (ints) in older layers. Handle both.
        if not result:
            return f"No read receipts available for message {message_id} in chat " f"{chat_id}."

        readers = []
        for item in result:
            if hasattr(item, "user_id"):
                readers.append(
                    {
                        "user_id": item.user_id,
                        "read_at": item.date.isoformat() if getattr(item, "date", None) else None,
                    }
                )
            else:
                # Older layer: plain int
                readers.append({"user_id": item, "read_at": None})

        return json.dumps(
            {
                "chat_id": str(chat_id),
                "message_id": message_id,
                "read_by": readers,
                "count": len(readers),
            },
            indent=2,
            default=json_serializer,
        )
    except Exception as e:
        logger.exception(
            f"get_message_read_by failed (chat_id={chat_id}, message_id={message_id})"
        )
        return log_and_format_error(
            "get_message_read_by", e, chat_id=chat_id, message_id=message_id
        )


@mcp.tool(
    annotations=ToolAnnotations(title="Get Message Link", openWorldHint=True, readOnlyHint=True)
)
@with_account(readonly=True)
@validate_id("chat_id")
async def get_message_link(
    chat_id: Union[int, str], message_id: int, thread: bool = False, account: str = None
) -> str:
    """
    Export a t.me/... link for a specific message.

    Only works on channels and supergroups — basic groups and private chats
    do not expose message links.

    Args:
        chat_id: The channel/supergroup ID or username.
        message_id: The message ID to export a link for.
        thread: If True, returns a link that opens the message inside its
            discussion thread (only meaningful for supergroups with linked
            discussion).
    """
    try:
        cl = get_client(account)
        await ensure_connected(cl)
        entity = await resolve_entity(chat_id, cl)
        if not isinstance(entity, Channel):
            return (
                f"Cannot export message link for this entity type "
                f"({type(entity).__name__}). Message links are only available "
                f"for channels and supergroups."
            )

        result = await cl(
            functions.channels.ExportMessageLinkRequest(
                channel=entity, id=message_id, grouped=False, thread=thread
            )
        )

        link = getattr(result, "link", None)
        html = getattr(result, "html", None)
        if not link:
            return f"Could not export link for message {message_id} in chat {chat_id}."

        output = f"Link: {link}"
        if html:
            output += f"\nHTML: {html}"
        return output
    except Exception as e:
        logger.exception(
            f"get_message_link failed (chat_id={chat_id}, message_id={message_id}, "
            f"thread={thread})"
        )
        return log_and_format_error(
            "get_message_link",
            e,
            chat_id=chat_id,
            message_id=message_id,
            thread=thread,
        )


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
