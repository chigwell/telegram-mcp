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
from mcp.types import Annotations, TextContent, ToolAnnotations
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
from sanitize import sanitize_user_content, sanitize_name, sanitize_dict, format_tool_result


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

# Annotate all tool results with audience=["user"] so MCP clients know
# the content is user-generated data, not instructions for the model.
# We wrap the low-level request handler (after FastMCP registers it) to inject
# annotations into the final CallToolResult, preserving structured output.
_USER_AUDIENCE = Annotations(audience=["user"])


def _install_annotation_hook() -> None:
    from mcp.types import CallToolRequest, ServerResult, CallToolResult

    original_handler = mcp._mcp_server.request_handlers[CallToolRequest]

    async def annotated_handler(req):
        response = await original_handler(req)
        if isinstance(response, ServerResult) and isinstance(response.root, CallToolResult):
            content = response.root.content
            if content:
                response.root.content = [
                    (
                        block.model_copy(update={"annotations": _USER_AUDIENCE})
                        if isinstance(block, TextContent) and block.annotations is None
                        else block
                    )
                    for block in content
                ]
        return response

    mcp._mcp_server.request_handlers[CallToolRequest] = annotated_handler


_install_annotation_hook()


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

# Create file handler with absolute path. Keep the legacy location next to
# top-level main.py, even though runtime code now lives inside telegram_mcp/.
package_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = os.path.dirname(package_dir)
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
    """Helper function to format entity information consistently.

    Names and titles are sanitized to prevent prompt injection.
    """
    result = {"id": entity.id}

    if hasattr(entity, "title"):
        result["name"] = sanitize_name(entity.title)
        result["type"] = "group" if isinstance(entity, Chat) else "channel"
    elif hasattr(entity, "first_name"):
        name_parts = []
        if entity.first_name:
            name_parts.append(entity.first_name)
        if hasattr(entity, "last_name") and entity.last_name:
            name_parts.append(entity.last_name)
        result["name"] = sanitize_name(" ".join(name_parts))
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
    """Helper function to format message information consistently.

    Message text is sanitized to prevent prompt injection.
    """
    result = {
        "id": message.id,
        "date": message.date.isoformat(),
        "text": sanitize_user_content(message.message),
    }

    if message.from_id:
        result["from_id"] = utils.get_peer_id(message.from_id)

    if message.media:
        result["has_media"] = True
        result["media_type"] = type(message.media).__name__

    return result


def get_sender_name(message) -> str:
    """Helper function to get sender name from a message.

    Returns a sanitized single-line display name to prevent prompt injection
    via crafted Telegram display names.
    """
    if not message.sender:
        return "Unknown"

    # Check for group/channel title first
    if hasattr(message.sender, "title") and message.sender.title:
        return sanitize_name(message.sender.title)
    elif hasattr(message.sender, "first_name"):
        # User sender
        first_name = getattr(message.sender, "first_name", "") or ""
        last_name = getattr(message.sender, "last_name", "") or ""
        full_name = f"{first_name} {last_name}".strip()
        return sanitize_name(full_name) if full_name else "Unknown"
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


def get_engagement_dict(message) -> Optional[Dict[str, Any]]:
    """Return engagement metrics as a dict for JSON-formatted tool results."""
    result = {}
    views = getattr(message, "views", None)
    if views is not None:
        result["views"] = views
    forwards = getattr(message, "forwards", None)
    if forwards is not None:
        result["forwards"] = forwards
    reactions = getattr(message, "reactions", None)
    if reactions is not None:
        results = getattr(reactions, "results", None)
        result["reactions"] = sum(getattr(r, "count", 0) or 0 for r in results) if results else 0
    return result if result else None


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


# Re-export shared runtime names for tool modules that use star imports.
__all__ = [name for name in globals() if not name.startswith("__")]
