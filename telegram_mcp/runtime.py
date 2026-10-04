import argparse
import os
import re
import sys
import json
import time
import asyncio
import sqlite3
import logging
import mimetypes
import unicodedata

# Ensure sys.stderr is reconfigured for UTF-8 on Windows where possible
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
from contextlib import contextmanager
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import List, Dict, Optional, Union, Any, Iterable, get_args
from pathlib import Path
from urllib.parse import unquote, urlparse

# Third-party libraries
from dotenv import find_dotenv, load_dotenv
from mcp.server.fastmcp import FastMCP, Context, Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from mcp.types import Annotations, ImageContent, TextContent, ToolAnnotations
from mcp.shared.exceptions import McpError
from pythonjsonlogger import jsonlogger
from telethon import TelegramClient, functions, types, utils
from telethon.errors import AuthKeyDuplicatedError, FloodWaitError, BotMethodInvalidError
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
import hashlib
import tempfile

try:
    import fcntl  # POSIX advisory locks; unavailable on Windows
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

from telegram_mcp.singleton import try_lock_exclusive

from functools import wraps
import telethon.errors.rpcerrorlist
from sanitize import sanitize_user_content, sanitize_name, sanitize_dict, format_tool_result
from telegram_mcp.client_identity import client_identity_kwargs
from telegram_mcp import serialization as __serialization
from telegram_mcp import core_types as __core_types
from telegram_mcp import validation as __validation
from telegram_mcp import formatting as __formatting
from telegram_mcp import error_formatting as __error_formatting
from telegram_mcp import file_policy as __file_policy
from telegram_mcp import access_policy as __access_policy
from telegram_mcp import alias_store as __alias_store
from telegram_mcp import account_config as __account_config
from telegram_mcp import connections as __connections
from telegram_mcp import mcp_policy as __mcp_policy

ValidationError = __core_types.ValidationError


def json_serializer(obj):
    """Helper function to convert non-serializable objects for JSON serialization."""
    return __serialization.json_default(obj, datetime_type=datetime)


def get_entity_type(entity: Any) -> str:
    """Return a normalized, human-readable chat/entity type."""
    return __formatting.get_entity_type(
        entity=entity,
        Channel=Channel,
        Chat=Chat,
        User=User,
    )


def get_marked_id(entity: Any) -> int:
    """Return a Telethon-compatible marked ID for an entity."""
    return __formatting.get_marked_id(
        entity=entity,
        Channel=Channel,
        Chat=Chat,
    )


def get_entity_filter_type(entity: Any) -> Optional[str]:
    """Return list_chats-compatible filter type: user/group/channel."""
    return __formatting.get_entity_filter_type(
        entity=entity,
        get_entity_type=get_entity_type,
    )


def parse_schedule_date(
    schedule_date: Union[str, int],
) -> tuple[Optional[datetime], Optional[str]]:
    """Return (datetime, None) for a usable schedule_date, or (None, error message).

    Accepts an ISO-8601 string or a Unix timestamp; naive datetimes are UTC.
    """
    return __formatting.parse_schedule_date(
        schedule_date=schedule_date,
        datetime=datetime,
        timezone=timezone,
    )


load_dotenv()

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

# The shared HTTP service can be consumed by long-lived MCP clients. Stateless requests keep
# those clients usable across server-process restarts instead of rejecting their next call
# with "No valid session ID provided". Stdio transport remains unaffected.
mcp = FastMCP("telegram", stateless_http=True)

# Annotate all tool results with audience=["user"] so MCP clients know
# the content is user-generated data, not instructions for the model.
# We wrap the low-level request handler (after FastMCP registers it) to inject
# annotations into the final CallToolResult, preserving structured output.
_USER_AUDIENCE = Annotations(audience=["user"])
TOOL_TIMEOUT_SECONDS_DEFAULT = 55.0


def _tool_timeout_seconds(value: Optional[str] = None) -> Optional[float]:
    """Return the server-side ceiling for one MCP tool call.

    The default stays just above the two event-wait tools' 50-second defaults,
    while ensuring a wedged Telethon request becomes an explicit MCP error
    before common client-side one-minute timeouts. Set the value to ``0`` or a
    negative number only for a deliberately unbounded operator session.
    """
    return __mcp_policy._tool_timeout_seconds(
        value=value,
        TOOL_TIMEOUT_SECONDS_DEFAULT=TOOL_TIMEOUT_SECONDS_DEFAULT,
        os=os,
    )


def _install_annotation_hook() -> None:
    from mcp.types import CallToolRequest, ServerResult, CallToolResult

    original_handler = mcp._mcp_server.request_handlers[CallToolRequest]

    async def annotated_handler(req):
        response = await __mcp_policy.call_with_timeout(
            req,
            original_handler,
            timeout=_tool_timeout_seconds(),
            asyncio=asyncio,
            ServerResult=ServerResult,
            CallToolResult=CallToolResult,
            TextContent=TextContent,
        )
        return __mcp_policy.annotate_result(
            response,
            audience=_USER_AUDIENCE,
            ServerResult=ServerResult,
            CallToolResult=CallToolResult,
            TextContent=TextContent,
            ImageContent=ImageContent,
        )

    mcp._mcp_server.request_handlers[CallToolRequest] = annotated_handler


_install_annotation_hook()


_EXPOSED_TOOLS_MODES = {"all", "read-only"}
_EXPOSED_TOOLS_ALLOW_SEPARATOR = "+"


def _split_exposed_tools_mode(mode: str) -> tuple[str, list[str]]:
    """Split a normalised exposure mode into its base mode and write allowlist."""
    return __mcp_policy._split_exposed_tools_mode(
        mode=mode,
        _EXPOSED_TOOLS_ALLOW_SEPARATOR=_EXPOSED_TOOLS_ALLOW_SEPARATOR,
    )


def _get_exposed_tools_mode(value: Optional[str] = None) -> str:
    """Return the configured MCP tool exposure mode.

    ``TELEGRAM_EXPOSED_TOOLS=read-only`` keeps only tools annotated with
    ``readOnlyHint=True``. ``read-only+send_message,reply_to_message`` keeps
    those plus the named write tools. The default is ``all`` for backward
    compatibility.
    """
    return __mcp_policy._get_exposed_tools_mode(
        value=value,
        _EXPOSED_TOOLS_ALLOW_SEPARATOR=_EXPOSED_TOOLS_ALLOW_SEPARATOR,
        _EXPOSED_TOOLS_MODES=_EXPOSED_TOOLS_MODES,
        _split_exposed_tools_mode=_split_exposed_tools_mode,
        os=os,
    )


def _apply_exposed_tools_mode(server: FastMCP = mcp, mode: Optional[str] = None) -> list[str]:
    """Prune registered MCP tools according to the configured exposure mode."""
    return __mcp_policy._apply_exposed_tools_mode(
        server=server,
        mode=mode,
        _get_exposed_tools_mode=_get_exposed_tools_mode,
        _split_exposed_tools_mode=_split_exposed_tools_mode,
    )


_FILE_EXTENSION_TOKEN_PATTERN = re.compile(r"^\.[A-Za-z0-9_-]+$")
_FILE_EXTENSIONS_ENTRY_SEPARATOR = ";"
_FILE_EXTENSIONS_TOOL_SEPARATOR = ":"
_FILE_EXTENSIONS_LIST_SEPARATOR = ","


def _get_file_extension_overrides(value: Optional[str] = None) -> dict[str, set[str]]:
    """Parse ``TELEGRAM_FILE_EXTENSIONS`` into a tool -> extension-set mapping.

    ``TELEGRAM_FILE_EXTENSIONS=send_file:.pdf,.png;upload_file:.pdf`` mirrors
    the ``TELEGRAM_EXPOSED_TOOLS`` convention: unset (or blank) means no
    overrides at all, which keeps today's behaviour unchanged. A malformed
    entry fails loudly here, at parse time, the same way a malformed
    ``TELEGRAM_EXPOSED_TOOLS`` mode fails loudly in
    ``_get_exposed_tools_mode`` -- a typo must not silently produce a
    narrower (or wider) allowlist that looks like it worked.

    This only parses the tool -> extensions shape; it does not know the set
    of real tool names, so it cannot reject an unknown tool. That check
    happens in ``_apply_file_extension_overrides``, which has a server to
    check against.
    """
    return __file_policy._get_file_extension_overrides(
        value=value,
        _FILE_EXTENSIONS_ENTRY_SEPARATOR=_FILE_EXTENSIONS_ENTRY_SEPARATOR,
        _FILE_EXTENSIONS_LIST_SEPARATOR=_FILE_EXTENSIONS_LIST_SEPARATOR,
        _FILE_EXTENSIONS_TOOL_SEPARATOR=_FILE_EXTENSIONS_TOOL_SEPARATOR,
        _FILE_EXTENSION_TOKEN_PATTERN=_FILE_EXTENSION_TOKEN_PATTERN,
        os=os,
    )


def _apply_file_extension_overrides(
    server: FastMCP = mcp, value: Optional[str] = None
) -> dict[str, set[str]]:
    """Rebuild ``EXTENSION_ALLOWLISTS`` from defaults plus ``TELEGRAM_FILE_EXTENSIONS``.

    Overrides merge over ``_DEFAULT_EXTENSION_ALLOWLISTS``: naming a tool
    that already has a hardcoded default replaces that tool's whole set
    (not a union), and any tool not mentioned keeps its default (including
    ``send_file``/``upload_file``, which have no default and so stay
    unrestricted when unset). This is the only thing that changes --
    ``_ensure_extension_allowed`` itself is untouched and keeps reading the
    module-level ``EXTENSION_ALLOWLISTS`` dict.

    An unknown tool name aborts startup exactly like an unknown name in a
    ``TELEGRAM_EXPOSED_TOOLS`` allowlist: validated against the server's own
    registered tools, not a hardcoded guess at what tools exist. That check
    reads the tool manager, so this must run *before*
    ``_apply_exposed_tools_mode`` prunes it -- otherwise narrowing the
    extensions of a tool that exposure hid would abort startup on a valid
    configuration.
    """
    global EXTENSION_ALLOWLISTS
    EXTENSION_ALLOWLISTS = __file_policy._apply_file_extension_overrides(
        server=server,
        value=value,
        _DEFAULT_EXTENSION_ALLOWLISTS=_DEFAULT_EXTENSION_ALLOWLISTS,
        _get_file_extension_overrides=_get_file_extension_overrides,
    )
    return EXTENSION_ALLOWLISTS


# ---------------------------------------------------------------------------
# Multi-account configuration
# ---------------------------------------------------------------------------


_PROXY_TYPES_SOCKS_HTTP = {"socks5", "socks4", "http"}
_PROXY_TYPES_ALL = _PROXY_TYPES_SOCKS_HTTP | {"mtproxy"}


def _get_proxy_env(name: str, label: str) -> Optional[str]:
    """Resolve a TELEGRAM_PROXY_* env var with optional ``_<LABEL>`` suffix.

    Per-account values override the unsuffixed defaults so a global proxy can
    coexist with per-label overrides.
    """
    return __account_config._get_proxy_env(
        name=name,
        label=label,
        os=os,
    )


def _parse_bool_env(value: Optional[str], default: bool) -> bool:
    return __account_config._parse_bool_env(
        value=value,
        default=default,
    )


def _build_proxy_for_label(label: str) -> tuple[Optional[Any], Optional[Any]]:
    """Return ``(proxy, connection)`` kwargs for ``TelegramClient`` for a label.

    Reads ``TELEGRAM_PROXY_*`` env vars (with optional ``_<LABEL>`` suffix).
    Returns ``(None, None)`` when no proxy is configured. Raises
    :class:`ValidationError` for malformed configuration so the server fails
    fast instead of silently bypassing the proxy.
    """
    return __account_config._build_proxy_for_label(
        label=label,
        Any=Any,
        ValidationError=ValidationError,
        _PROXY_TYPES_ALL=_PROXY_TYPES_ALL,
        _get_proxy_env=_get_proxy_env,
        _parse_bool_env=_parse_bool_env,
    )


def _get_flood_sleep_threshold() -> int:
    """Read TELEGRAM_FLOOD_SLEEP_THRESHOLD from environment (default: 60)."""
    raw = os.getenv("TELEGRAM_FLOOD_SLEEP_THRESHOLD", "60").strip()
    try:
        val = int(raw)
        if val < 0:
            logger.warning("Negative TELEGRAM_FLOOD_SLEEP_THRESHOLD clamped to 0 (fail-fast mode)")
            return 0
        return val
    except ValueError:
        logger.warning("Invalid TELEGRAM_FLOOD_SLEEP_THRESHOLD; falling back to default 60s")
        return 60


def _resolve_session_path(session_name: str) -> str:
    """Resolve a relative session name against the project root.

    When TELEGRAM_SESSION_NAME is a relative path/name (e.g. 'my_session' or
    'sessions/main'), running from a different working directory makes Telethon
    search os.getcwd() and fail to find the existing .session file, triggering
    an interactive login prompt.
    This resolves the relative path against the repository/project root (or the
    directory where .env was found) if the file exists there, or if running
    from a subdirectory of the project root.
    """
    return __account_config._resolve_session_path(
        session_name=session_name,
        PROJECT_ROOT=PROJECT_ROOT,
        find_dotenv=find_dotenv,
        os=os,
    )


def _build_client(session: Any, label: str) -> TelegramClient:
    """Construct a ``TelegramClient`` honoring per-label proxy and flood sleep configuration."""
    return __account_config._build_client(
        session=session,
        label=label,
        Any=Any,
        TELEGRAM_API_HASH=TELEGRAM_API_HASH,
        TELEGRAM_API_ID=TELEGRAM_API_ID,
        TelegramClient=TelegramClient,
        _build_proxy_for_label=_build_proxy_for_label,
        _get_flood_sleep_threshold=_get_flood_sleep_threshold,
        _resolve_session_path=_resolve_session_path,
        client_identity_kwargs=client_identity_kwargs,
    )


# --- Session pool ------------------------------------------------------------
# A POOL of interchangeable authorized sessions for the SAME account lets
# several concurrent MCP clients (e.g. the desktop app AND a terminal CLI) run
# against one Telegram account without tripping AuthKeyDuplicatedError.
#
# Telegram forbids one auth key (one StringSession) being used from two IPs at
# once; on a dual-stack / VPN host two local clients can egress via different
# source IPs and collide. The fix is one authorized session PER concurrent
# client (Telegram allows one account on many "devices"). Generate extra
# sessions with `uv run session_string_generator.py` and list them in
# TELEGRAM_SESSION_STRINGS (whitespace/comma/semicolon separated). Each process
# claims the first session not already locked by a live process via an advisory
# flock, so clients deterministically pick distinct slots; the OS releases the
# lock if a process dies.

# Acquired lock handles are held for the process lifetime so the advisory locks
# stay held until exit (or crash, when the OS releases them).
_SESSION_LOCKS: list = []


def _parse_session_pool() -> List[str]:
    """Parse TELEGRAM_SESSION_STRINGS into a de-duplicated list of sessions."""
    return __account_config._parse_session_pool(
        List=List,
        os=os,
        re=re,
    )


def _acquire_session(pool: List[str]) -> str:
    """Claim the first free session in the pool via an advisory file lock."""
    return __account_config._acquire_session(
        pool=pool,
        _SESSION_LOCKS=_SESSION_LOCKS,
        hashlib=hashlib,
        os=os,
        sys=sys,
        tempfile=tempfile,
        try_lock_exclusive=try_lock_exclusive,
    )


def _discover_accounts() -> dict[str, TelegramClient]:
    """Scan env vars to build account label -> TelegramClient mapping.

    Detection rules:
    - TELEGRAM_SESSION_STRING_<LABEL> / TELEGRAM_SESSION_NAME_<LABEL> -> multi-mode
    - TELEGRAM_SESSION_STRINGS (whitespace/comma/semicolon separated) -> a pool
      of interchangeable sessions for the default account; each process claims a
      free slot to avoid AuthKeyDuplicatedError (takes precedence for "default")
    - Unsuffixed TELEGRAM_SESSION_STRING / TELEGRAM_SESSION_NAME -> label "default"
    - If both suffixed and unsuffixed exist -> unsuffixed becomes "default"

    Each client is constructed via :func:`_build_client`, which applies any
    matching ``TELEGRAM_PROXY_*`` configuration (optionally per-label).
    """
    return __account_config._discover_accounts(
        StringSession=StringSession,
        TelegramClient=TelegramClient,
        _acquire_session=_acquire_session,
        _build_client=_build_client,
        _parse_session_pool=_parse_session_pool,
        _resolve_session_path=_resolve_session_path,
        os=os,
        sys=sys,
    )


clients: dict[str, TelegramClient] = _discover_accounts()


def get_client(account: str = None) -> TelegramClient:
    """Resolve account label to TelegramClient."""
    return __account_config.get_client(
        account=account,
        clients=clients,
    )


def is_multi_mode() -> bool:
    """Return True when more than one account is configured."""
    return __account_config.is_multi_mode(
        clients=clients,
    )


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
            if all(isinstance(result, str) for _, result in results):
                return "\n\n".join(f"[{label}]\n{result}" for label, result in results)

            account_labelled_content = []
            for label, result in results:
                account_labelled_content.append(f"[{label}]")
                account_labelled_content.extend(result if isinstance(result, list) else [result])
            return account_labelled_content

        return wrapper

    return decorator


_last_conn_verified: dict[int, float] = {}
_CONN_VERIFY_INTERVAL: float = 30.0  # seconds between live pings
_RECONNECT_TIMEOUT: float = 30.0  # seconds before a reconnect attempt is abandoned


async def _force_reconnect(cl: TelegramClient):
    """Force disconnect + reconnect regardless of is_connected() state."""
    return await __connections._force_reconnect(
        cl=cl,
        AuthKeyDuplicatedError=AuthKeyDuplicatedError,
        _RECONNECT_TIMEOUT=_RECONNECT_TIMEOUT,
        _last_conn_verified=_last_conn_verified,
        asyncio=asyncio,
        logging=logging,
        time=time,
    )


async def ensure_connected(cl: TelegramClient = None):
    """Verify Telegram connection is alive, reconnect if needed.

    is_connected() can return True when the underlying TCP socket is dead.
    We periodically send a lightweight request to verify the connection
    actually works, and force-reconnect on any failure.

    Accepts an explicit client; falls back to the default single-account
    client when called without one.
    """
    return await __connections.ensure_connected(
        cl=cl,
        _CONN_VERIFY_INTERVAL=_CONN_VERIFY_INTERVAL,
        _force_reconnect=_force_reconnect,
        _last_conn_verified=_last_conn_verified,
        asyncio=asyncio,
        functions=functions,
        get_client=get_client,
        time=time,
    )


# Setup robust logging with both file and console output
logger = logging.getLogger("telegram_mcp")
logger.setLevel(logging.ERROR)  # Set to ERROR for production, INFO for debugging

# Create console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.ERROR)  # Set to ERROR for production, INFO for debugging

# Create file handler with absolute path. Keep the legacy location next to
# top-level main.py, even though runtime code now lives inside telegram_mcp/.
package_dir = os.path.dirname(os.path.abspath(__file__))
script_dir = PROJECT_ROOT
log_file_path = os.path.join(script_dir, "mcp_errors.log")

try:
    file_handler = logging.FileHandler(log_file_path, mode="a", encoding="utf-8")  # Append mode
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
    logger.info("Logging initialized")
except Exception:
    print("WARNING: Error setting up log file; using console logging only.", file=sys.stderr)
    # Fallback to console-only logging
    logger.addHandler(console_handler)
    logger.error("Failed to set up log file handler; using console logging only.")


# File-path tool security configuration
SERVER_ALLOWED_ROOTS: list[Path] = []
DEFAULT_DOWNLOAD_SUBDIR = "downloads"
DISALLOWED_PATH_PATTERNS = ("*", "?", "[", "]", "{", "}", "~", "\x00")
_DEFAULT_EXTENSION_ALLOWLISTS: dict[str, set[str]] = {
    "send_voice": {".ogg", ".opus"},
    "send_sticker": {".webp"},
    "set_profile_photo": {".jpg", ".jpeg", ".png", ".webp"},
    "edit_chat_photo": {".jpg", ".jpeg", ".png", ".webp"},
}
# Mutable, TELEGRAM_FILE_EXTENSIONS-aware allowlist actually consulted by
# _ensure_extension_allowed(). Rebuilt from _DEFAULT_EXTENSION_ALLOWLISTS by
# _apply_file_extension_overrides() at startup; defaults to the hardcoded
# values so importing this module without calling that function (e.g. tests)
# keeps today's behaviour.
EXTENSION_ALLOWLISTS: dict[str, set[str]] = dict(_DEFAULT_EXTENSION_ALLOWLISTS)
MAX_FILE_BYTES: dict[str, int] = {
    "download_media": 200 * 1024 * 1024,  # 200 MB
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
ROOTS_STATUS_SERVER_FALLBACK = "server_fallback"
ROOTS_STATUS_SERVER_ONLY = "server_only"
ROOTS_STATUS_ERROR = "error"
ROOTS_STATUS_TIMEOUT = "timeout"
# Some clients accept the server-initiated roots/list request but never answer
# it (observed with Claude Code over streamable HTTP), which would otherwise
# hang every file-path tool forever instead of failing.
ROOTS_REQUEST_TIMEOUT_DEFAULT = 10.0


# Per-chat access control allowlist configuration (TELEGRAM_ALLOWED_CHAT_IDS)
ALLOWED_CHAT_IDS: Optional[set[Union[int, str]]] = None
CHAT_PARAM_NAMES: frozenset[str] = frozenset({"chat_id", "from_chat_id", "to_chat_id", "channel"})


def _parse_allowed_chat_ids(
    raw: Optional[Union[str, Iterable[Union[int, str]]]],
) -> Optional[set[Union[int, str]]]:
    """Parse TELEGRAM_ALLOWED_CHAT_IDS into a set of allowed IDs and usernames.

    Supports comma-separated integer IDs (e.g. '12345678,-100123456789') and
    usernames/handles (e.g. '@mychat,other_channel').
    Also automatically indexes marked variants for bare integers and vice versa
    so that both marked IDs (-100...) and bare positive IDs match.
    """
    return __access_policy._parse_allowed_chat_ids(
        raw=raw,
        Union=Union,
    )


def _load_allowed_chat_ids() -> Optional[set[Union[int, str]]]:
    """Load ALLOWED_CHAT_IDS from the TELEGRAM_ALLOWED_CHAT_IDS environment variable."""
    return __access_policy._load_allowed_chat_ids(
        _parse_allowed_chat_ids=_parse_allowed_chat_ids,
        os=os,
    )


# Initial load from environment
ALLOWED_CHAT_IDS = _load_allowed_chat_ids()


def get_effective_allowed_chat_ids() -> Optional[set[Union[int, str]]]:
    """Return the currently effective set of allowed chat IDs, or None if allowlist is disabled."""
    return __access_policy.get_effective_allowed_chat_ids(
        ALLOWED_CHAT_IDS=ALLOWED_CHAT_IDS,
        _parse_allowed_chat_ids=_parse_allowed_chat_ids,
        os=os,
    )


def is_chat_allowlist_enabled() -> bool:
    """Return True if chat allowlist filtering is active."""
    return __access_policy.is_chat_allowlist_enabled(
        get_effective_allowed_chat_ids=get_effective_allowed_chat_ids,
    )


def is_chat_allowed(chat_identifier: Any, entity: Any = None) -> bool:
    """Check whether a chat identifier or entity is permitted by the allowlist.

    If allowlist is not enabled, always returns True.
    """
    return __access_policy.is_chat_allowed(
        chat_identifier=chat_identifier,
        entity=entity,
        get_effective_allowed_chat_ids=get_effective_allowed_chat_ids,
        get_marked_id=get_marked_id,
    )


def check_chat_access(chat_identifier: Any, entity: Any = None) -> Optional[str]:
    """Return an error message if chat access is restricted, or None if allowed."""
    return __access_policy.check_chat_access(
        chat_identifier=chat_identifier,
        entity=entity,
        is_chat_allowed=is_chat_allowed,
    )


# Error code prefix mapping for better error tracing
ErrorCategory = __core_types.ErrorCategory


ChatAccessDeniedError = __core_types.ChatAccessDeniedError


def _is_flood_wait(error: Exception) -> bool:
    """True for Telethon FloodWaitError."""
    return __error_formatting._is_flood_wait(
        error=error,
        FloodWaitError=FloodWaitError,
    )


def _is_schema_drift(error: Exception) -> bool:
    """True for TypeNotFoundError — the installed TL schema is older than what the server sends."""
    return __error_formatting._is_schema_drift(
        error=error,
    )


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
        **kwargs: Additional context parameters. These are never written to persistent logs.

    Returns:
        A user-friendly error message with an error code.
    """
    return __error_formatting.log_and_format_error(
        function_name=function_name,
        error=error,
        prefix=prefix,
        user_message=user_message,
        AliasNeedsUser=AliasNeedsUser,
        ErrorCategory=ErrorCategory,
        _is_flood_wait=_is_flood_wait,
        _is_schema_drift=_is_schema_drift,
        logger=logger,
        **kwargs,
    )


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
                    return __validation.validate_single_id(
                        value,
                        p_name,
                        apply_alias=apply_alias,
                        AliasID=AliasID,
                        is_handle_like=is_handle_like,
                        alias_ask_payload=alias_ask_payload,
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

                # Per-chat privacy allowlist enforcement
                if is_chat_allowlist_enabled() and param_name in CHAT_PARAM_NAMES:
                    check_target = kwargs[param_name]
                    target_items = (
                        check_target if isinstance(check_target, list) else [check_target]
                    )
                    for item in target_items:
                        if not is_chat_allowed(item):
                            resolved_allowed = False
                            if isinstance(item, str):
                                try:
                                    cl = get_client(kwargs.get("account"))
                                    if cl:
                                        ent = await resolve_entity(item, cl)
                                        if is_chat_allowed(item, ent):
                                            resolved_allowed = True
                                except Exception:
                                    pass
                            if not resolved_allowed:
                                err = check_chat_access(item)
                                return log_and_format_error(
                                    func.__name__,
                                    ChatAccessDeniedError(err),
                                    prefix=ErrorCategory.PRIVACY,
                                    user_message=err,
                                    **{param_name: param_value},
                                )

            return await func(*args, **kwargs)

        return wrapper

    return decorator


def format_entity(entity) -> Dict[str, Any]:
    """Helper function to format entity information consistently.

    Names and titles are sanitized to prevent prompt injection.
    """
    return __formatting.format_entity(
        entity=entity,
        Chat=Chat,
        get_marked_id=get_marked_id,
        sanitize_name=sanitize_name,
    )


# Parse modes that request server-side rich formatting (tables, headings,
# formulas, collapsible sections — the June 2026 "Rich Messages" feature).
# Sending rich messages requires Telegram Premium on the account.
RICH_PARSE_MODES = {"rich", "rich_md", "rich_markdown", "rich_html"}


async def account_is_premium(client) -> bool:
    """Fresh Premium check at call time — Premium can expire or be bought anytime."""
    return await __formatting.account_is_premium(
        client=client,
    )


def make_rich_input(parse_mode: str, text: str):
    """Build the InputRichMessage payload for a rich parse mode."""
    return __formatting.make_rich_input(
        parse_mode=parse_mode,
        text=text,
        types=types,
    )


# Reading a rich message is the other direction, and it needs its own walk: a
# channel posting in this format leaves msg.message empty and carries every word
# as Instant-View page blocks, so a reader that only looks at msg.message
# reports the whole post as empty.
_RICH_TEXT_TYPES = tuple(get_args(types.TypeRichText))

# RichText is a recursive tree: a node either holds a plain string, wraps
# another node, or concatenates a list of them. Dispatching on the field rather
# than on the class keeps a node type Telegram adds later flattening instead of
# vanishing.
_RICH_TEXT_FIELDS = ("texts", "text", "alt", "source")

# Where a page block, list item, table row or caption keeps its words. Same walk
# covers the blocks nested inside details, collages and embedded posts.
_PAGE_TEXT_FIELDS = (
    "title",
    "subtitle",
    "author",
    "text",
    "caption",
    "credit",
    "items",
    "blocks",
    "rows",
    "articles",
)


def rich_text_to_str(node) -> str:
    """Flatten one RichText node into plain text.

    TextCustomEmoji contributes its alt character - dropping it would silently
    eat the emoji a channel used as a bullet or a heading marker.
    """
    return __formatting.rich_text_to_str(
        node=node,
        _RICH_TEXT_FIELDS=_RICH_TEXT_FIELDS,
        rich_text_to_str=rich_text_to_str,
    )


def _page_lines(node) -> List[str]:
    """Text lines carried by a page block, list item, table row or caption."""
    return __formatting._page_lines(
        node=node,
        _PAGE_TEXT_FIELDS=_PAGE_TEXT_FIELDS,
        _RICH_TEXT_TYPES=_RICH_TEXT_TYPES,
        _page_lines=_page_lines,
        rich_text_to_str=rich_text_to_str,
    )


def rich_message_text(msg) -> str:
    """Plain text of a rich (block-format) message, "" when there is none.

    Each block becomes a paragraph and the lines within one block stay together,
    so a list reads as a list instead of one run-on line. An unknown block type
    yields nothing rather than breaking the whole message.
    """
    return __formatting.rich_message_text(
        msg=msg,
        _page_lines=_page_lines,
    )


def premium_required_result(action: str) -> str:
    """Structured refusal so the agent can degrade gracefully instead of sending garbage."""
    return __formatting.premium_required_result(
        action=action,
        json=json,
    )


def is_premium_rpc_error(error: Exception) -> bool:
    """True when Telegram rejected a call because the account lacks Premium."""
    return __formatting.is_premium_rpc_error(
        error=error,
    )


_ALIASES_ENV = "TELEGRAM_ALIASES_FILE"
# Pre-XDG location; read as a fallback so existing installs keep resolving, never written.
_LEGACY_ALIASES_FILE = Path(__file__).resolve().parent.parent / "aliases.json"

# A username is >=5 chars of [A-Za-z0-9_]; phone/id/self references must never be
# fuzzy-matched or an alias could hijack a real account.
_HANDLE_RE = re.compile(r"^@?[a-zA-Z0-9_]{5,}$")
_SELF_REFS = {"me", "self"}


def aliases_file_path() -> Path:
    """Runtime data location, never the install directory (may be read-only)."""
    return __alias_store.aliases_file_path(
        Path=Path,
        _ALIASES_ENV=_ALIASES_ENV,
        os=os,
    )


def alias_key(text: str) -> str:
    """Normalize an alias so visually identical spellings collide on purpose."""
    return __alias_store.alias_key(
        text=text,
        unicodedata=unicodedata,
    )


def load_aliases(strict: bool = False) -> Dict[str, Dict[str, Any]]:
    """Return {key: {"id": int, "name": str|None, "account": str|None}}.

    Legacy `{alias: id}` files upgrade on read. Never raises: this runs inside
    resolve_entity on every call, so a damaged file must not take chat tools down.
    """
    return __alias_store.load_aliases(
        strict=strict,
        AliasStoreUnreadable=AliasStoreUnreadable,
        Any=Any,
        Dict=Dict,
        _ALIASES_ENV=_ALIASES_ENV,
        _LEGACY_ALIASES_FILE=_LEGACY_ALIASES_FILE,
        alias_key=alias_key,
        aliases_file_path=aliases_file_path,
        json=json,
        logger=logger,
        os=os,
        sanitize_name=sanitize_name,
    )


def save_aliases(aliases: Dict[str, Any]) -> None:
    """Atomically persist aliases 0600 — the file maps nicknames to real people."""
    return __alias_store.save_aliases(
        aliases=aliases,
        _ALIASES_ENV=_ALIASES_ENV,
        alias_key=alias_key,
        aliases_file_path=aliases_file_path,
        json=json,
        os=os,
        tempfile=tempfile,
        time=time,
    )


AliasStoreUnreadable = __core_types.AliasStoreUnreadable


@contextmanager
def _alias_lock(path: Path):
    """Serialize read-modify-write cycles across processes (best effort)."""
    yield from __alias_store._alias_lock(
        path=path,
        fcntl=fcntl,
        os=os,
    )


def update_aliases(mutate):
    """Apply `mutate(aliases)` to the alias file under an exclusive lock.

    Two tool calls that each load, change and save the whole map would otherwise
    lose one of the two writes — including a delete silently coming back.
    """
    return __alias_store.update_aliases(
        mutate=mutate,
        _ALIASES_ENV=_ALIASES_ENV,
        _alias_lock=_alias_lock,
        aliases_file_path=aliases_file_path,
        load_aliases=load_aliases,
        os=os,
        save_aliases=save_aliases,
    )


def is_handle_like(value: str) -> bool:
    """True for anything that could be a real username/phone/id/self reference."""
    return __alias_store.is_handle_like(
        value=value,
        _HANDLE_RE=_HANDLE_RE,
        _SELF_REFS=_SELF_REFS,
    )


def _same_word(a: str, b: str) -> bool:
    """True when two tokens are the same word, tolerating an inflected ending.

    Russian inflects at the end ("Андрею"/"андрей", "главному"/"главный"), so a real
    inflection keeps a long shared stem and swaps a few trailing characters. Three
    conditions, each pinned by a table of name pairs in tests/test_aliases.py: a stem
    of >=4 chars (or a one-character swap on equal-length words, so "лена"/"лене"
    works without letting "олег"/"олеся" through), endings of at most three
    characters, and a similarity backstop.
    """
    return __alias_store._same_word(
        a=a,
        b=b,
        SequenceMatcher=SequenceMatcher,
        os=os,
    )


def fuzzy_aliases_enabled() -> bool:
    return __alias_store.fuzzy_aliases_enabled(
        _parse_bool_env=_parse_bool_env,
        os=os,
    )


def _covers(query_tokens: List[str], alias_tokens: List[str]) -> bool:
    """True when every query token claims a DISTINCT alias token.

    Without the distinctness two query words could land on the same alias word, so
    "андрей андреев" matched a stored "андрей" and the surname the user added to
    name someone else was free. ponytail: Kuhn's algorithm, lists are 1-3 tokens.
    """
    return __alias_store._covers(
        query_tokens=query_tokens,
        alias_tokens=alias_tokens,
        Dict=Dict,
        _same_word=_same_word,
    )


def match_aliases(query: str) -> List[tuple]:
    """Return [(alias, record)] for a free-text reference.

    Exact key wins outright. Otherwise EVERY token of the query must match some
    token of the alias: word order and extra stored words are free, but a query
    word that lands nowhere disqualifies the alias. That asymmetry is what keeps
    "игорь смирнов" from matching stored "чикичев игорь" on one shared word.
    """
    return __alias_store.match_aliases(
        query=query,
        _covers=_covers,
        alias_key=alias_key,
        fuzzy_aliases_enabled=fuzzy_aliases_enabled,
        is_handle_like=is_handle_like,
        load_aliases=load_aliases,
    )


def apply_alias(identifier: Union[int, str]) -> Union[int, str]:
    """Resolve a SAVED alias to its chat ID, or return the identifier untouched.

    Exact keys only, deliberately: a fuzzy hit is a suggestion, never a recipient.
    "лена"/"леня" and "иван"/"иванов" have exactly the shape of an inflection pair,
    so silent fuzzy resolution cannot tell a case ending from a different person —
    and when the intended person is not saved at all there is no second match to
    make it look ambiguous. Near misses travel to the agent as candidates in
    alias_ask_payload() instead, costing one confirmation the first time a wording
    is used and nothing ever after.

    Non-raising by contract: resolve_entity() depends on that.
    """
    return __alias_store.apply_alias(
        identifier=identifier,
        alias_key=alias_key,
        is_handle_like=is_handle_like,
        load_aliases=load_aliases,
    )


AliasID = __core_types.AliasID


def alias_wording(value: Any) -> Optional[str]:
    """The free-text reference behind a value, if it came from one."""
    return __alias_store.alias_wording(
        value=value,
        is_handle_like=is_handle_like,
    )


# Telegram rejects a dead or malformed peer with an RPC error rather than a
# ValueError; for an aliased reference that means the saved mapping is stale.
_PEER_ERRORS = (
    telethon.errors.rpcerrorlist.ChatIdInvalidError,
    telethon.errors.rpcerrorlist.PeerIdInvalidError,
    telethon.errors.rpcerrorlist.UserIdInvalidError,
    telethon.errors.rpcerrorlist.ChannelInvalidError,
    telethon.errors.rpcerrorlist.ChannelPrivateError,
)


AliasNeedsUser = __core_types.AliasNeedsUser


def alias_ask_payload(reference: str, kind: str = "unknown", stored_id: Optional[int] = None):
    """Build the ask-the-user instruction returned instead of a blind send.

    Server-authored text interpolating only the caller's own reference; any
    Telegram-supplied name stays quarantined inside the candidates list.
    """
    return __alias_store.alias_ask_payload(
        reference=reference,
        kind=kind,
        stored_id=stored_id,
        json=json,
        load_aliases=load_aliases,
        match_aliases=match_aliases,
    )


def _marked_id_candidates(identifier: Union[int, str]) -> list[int]:
    """Return marked chat/channel ID variants for a bare positive integer ID."""
    return __connections._marked_id_candidates(
        identifier=identifier,
    )


def alias_failure(original: Any, identifier: Any) -> Optional[AliasNeedsUser]:
    """Ask-the-user error for a reference that failed to resolve, or None."""
    return __alias_store.alias_failure(
        original=original,
        identifier=identifier,
        AliasNeedsUser=AliasNeedsUser,
        alias_ask_payload=alias_ask_payload,
        alias_wording=alias_wording,
    )


async def _resolve_with_retries(
    getter: str, identifier: Union[int, str], client, label: str, try_marked: bool = True
):
    """Cache warming, reconnect, and marked-ID fallback shared by both resolvers.

    StringSession has no persistent entity cache, so a cold lookup raises ValueError;
    warming via get_dialogs() and retrying fixes it. A bare positive ID may also need
    Telethon's marked chat/channel variants.
    """
    return await __connections._resolve_with_retries(
        getter=getter,
        identifier=identifier,
        client=client,
        label=label,
        try_marked=try_marked,
        _marked_id_candidates=_marked_id_candidates,
        ensure_connected=ensure_connected,
    )


async def _resolve(getter: str, identifier: Union[int, str], client, label: str) -> Any:
    """Resolve an identifier, turning a failed free-text reference into a question.

    A saved alias resolves here as well as in @validate_id, so tools without that
    decorator understand nicknames too.
    """
    return await __connections._resolve(
        getter=getter,
        identifier=identifier,
        client=client,
        label=label,
        _PEER_ERRORS=_PEER_ERRORS,
        _resolve_with_retries=_resolve_with_retries,
        alias_failure=alias_failure,
        apply_alias=apply_alias,
        get_client=get_client,
    )


async def resolve_entity(identifier: Union[int, str], client=None) -> Any:
    """Resolve an entity, warming the cache and retrying as needed.

    Accepts IDs, usernames, phone numbers, and saved contact aliases.
    """
    return await __connections.resolve_entity(
        identifier=identifier,
        client=client,
        _resolve=_resolve,
    )


async def resolve_input_entity(identifier: Union[int, str], client=None) -> Any:
    """Like resolve_entity() but returns an InputPeer."""
    return await __connections.resolve_input_entity(
        identifier=identifier,
        client=client,
        _resolve=_resolve,
    )


def format_message(message) -> Dict[str, Any]:
    """Helper function to format message information consistently.

    Message text is sanitized to prevent prompt injection.
    """
    return __formatting.format_message(
        message=message,
        sanitize_user_content=sanitize_user_content,
        utils=utils,
    )


def get_sender_name(message) -> str:
    """Helper function to get sender name from a message.

    Returns a sanitized single-line display name to prevent prompt injection
    via crafted Telegram display names.
    """
    return __formatting.get_sender_name(
        message=message,
        sanitize_name=sanitize_name,
    )


def get_sender_username(message) -> Optional[str]:
    """Public @username of the message sender, if any (sanitized)."""
    return __formatting.get_sender_username(
        message=message,
        sanitize_name=sanitize_name,
    )


def get_sender_info(message) -> str:
    """Sender display string: name (@username) [id=NNN].

    Always exposes a numeric id (sender or from_id) so a user can be reached via
    tg://user?id=<id> even when no public @username exists.
    """
    return __formatting.get_sender_info(
        message=message,
        get_sender_name=get_sender_name,
        get_sender_username=get_sender_username,
    )


def get_engagement_info(message) -> str:
    """Helper function to get engagement metrics (views, forwards, reactions) from a message."""
    return __formatting.get_engagement_info(
        message=message,
    )


def get_engagement_dict(message) -> Optional[Dict[str, Any]]:
    """Return engagement metrics as a dict for JSON-formatted tool results."""
    return __formatting.get_engagement_dict(
        message=message,
    )


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    return __file_policy._dedupe_paths(
        paths=paths,
        List=List,
        Path=Path,
    )


def _contains_forbidden_path_patterns(raw_path: str) -> Optional[str]:
    return __file_policy._contains_forbidden_path_patterns(
        raw_path=raw_path,
        DISALLOWED_PATH_PATTERNS=DISALLOWED_PATH_PATTERNS,
        Path=Path,
    )


def _coerce_root_uri_to_path(uri: str) -> Path:
    return __file_policy._coerce_root_uri_to_path(
        uri=uri,
        Path=Path,
        os=os,
        unquote=unquote,
        urlparse=urlparse,
    )


def _path_is_within_root(candidate: Path, root: Path) -> bool:
    return __file_policy._path_is_within_root(
        candidate=candidate,
        root=root,
    )


def _path_is_within_any_root(candidate: Path, roots: List[Path]) -> bool:
    return __file_policy._path_is_within_any_root(
        candidate=candidate,
        roots=roots,
        _path_is_within_root=_path_is_within_root,
    )


def _first_resolution_root(roots: List[Path]) -> Path:
    return __file_policy._first_resolution_root(
        roots=roots,
    )


def _ensure_extension_allowed(tool_name: str, candidate: Path) -> Optional[str]:
    return __file_policy._ensure_extension_allowed(
        tool_name=tool_name,
        candidate=candidate,
        EXTENSION_ALLOWLISTS=EXTENSION_ALLOWLISTS,
    )


def _ensure_size_within_limit(tool_name: str, candidate: Path) -> Optional[str]:
    return __file_policy._ensure_size_within_limit(
        tool_name=tool_name,
        candidate=candidate,
        MAX_FILE_BYTES=MAX_FILE_BYTES,
    )


async def _get_effective_allowed_roots(ctx: Optional[Context]) -> List[Path]:
    return await __file_policy._get_effective_allowed_roots(
        ctx=ctx,
        _get_effective_allowed_roots_with_status=_get_effective_allowed_roots_with_status,
    )


def _is_roots_unsupported_error(error: Exception) -> bool:
    return __file_policy._is_roots_unsupported_error(
        error=error,
        McpError=McpError,
        ROOTS_UNSUPPORTED_ERROR_CODES=ROOTS_UNSUPPORTED_ERROR_CODES,
    )


def _coerce_paths_from_list_roots_validation_error(error: Exception) -> List[Path]:
    """Recover absolute filesystem roots when a client sends bare paths.

    Some MCP clients (notably Cursor) return workspace roots as plain absolute
    paths instead of ``file://`` URIs. The MCP SDK then fails pydantic validation
    of ``ListRootsResult`` even though the roots themselves are usable. Extract
    those paths from the validation error payload so file-path tools keep working.

    Which error pydantic reports depends on the path's shape. A POSIX path like
    ``/home/dev/ws`` has no scheme at all and yields ``url_parsing``, but on a
    Windows path like ``C:\\Users\\dev\\ws`` the drive letter parses as a scheme,
    so pydantic gets far enough to reject it as ``url_scheme`` instead. Accept
    both, or the Windows branch below is unreachable.
    """
    return __file_policy._coerce_paths_from_list_roots_validation_error(
        error=error,
        List=List,
        Path=Path,
        _dedupe_paths=_dedupe_paths,
    )


def _server_roots_fallback_enabled(value: Optional[str] = None) -> bool:
    """Whether server CLI roots may replace unusable/empty client Roots.

    Opt-in via the ``TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK`` environment variable.
    Applies when the client returns an empty roots list, or when ``list_roots``
    fails with an unexpected error (after any recoverable client paths are tried).
    Defaults to ``False`` to preserve the safe deny-all behavior.
    """
    return __file_policy._server_roots_fallback_enabled(
        value=value,
        _parse_bool_env=_parse_bool_env,
        os=os,
    )


def _server_roots_only_enabled(value: Optional[str] = None) -> bool:
    """TELEGRAM_SERVER_ROOTS_ONLY: use server roots and skip roots/list (default off)."""
    return __file_policy._server_roots_only_enabled(
        value=value,
        _parse_bool_env=_parse_bool_env,
        os=os,
    )


def _roots_request_timeout(value: Optional[str] = None) -> Optional[float]:
    """Seconds to wait for the client's ``roots/list`` reply.

    Override with ``TELEGRAM_ROOTS_TIMEOUT_SECONDS``; ``0`` or a negative value
    waits forever (the pre-timeout behavior).
    """
    return __file_policy._roots_request_timeout(
        value=value,
        ROOTS_REQUEST_TIMEOUT_DEFAULT=ROOTS_REQUEST_TIMEOUT_DEFAULT,
        os=os,
    )


async def _get_effective_allowed_roots_with_status(
    ctx: Optional[Context],
) -> tuple[List[Path], str]:
    return await __file_policy._get_effective_allowed_roots_with_status(
        ctx=ctx,
        List=List,
        Path=Path,
        ROOTS_STATUS_CLIENT_DENY_ALL=ROOTS_STATUS_CLIENT_DENY_ALL,
        ROOTS_STATUS_ERROR=ROOTS_STATUS_ERROR,
        ROOTS_STATUS_NOT_CONFIGURED=ROOTS_STATUS_NOT_CONFIGURED,
        ROOTS_STATUS_READY=ROOTS_STATUS_READY,
        ROOTS_STATUS_SERVER_FALLBACK=ROOTS_STATUS_SERVER_FALLBACK,
        ROOTS_STATUS_SERVER_ONLY=ROOTS_STATUS_SERVER_ONLY,
        ROOTS_STATUS_TIMEOUT=ROOTS_STATUS_TIMEOUT,
        ROOTS_STATUS_UNSUPPORTED_FALLBACK=ROOTS_STATUS_UNSUPPORTED_FALLBACK,
        SERVER_ALLOWED_ROOTS=SERVER_ALLOWED_ROOTS,
        _coerce_paths_from_list_roots_validation_error=_coerce_paths_from_list_roots_validation_error,
        _coerce_root_uri_to_path=_coerce_root_uri_to_path,
        _dedupe_paths=_dedupe_paths,
        _is_roots_unsupported_error=_is_roots_unsupported_error,
        _roots_request_timeout=_roots_request_timeout,
        _server_roots_fallback_enabled=_server_roots_fallback_enabled,
        _server_roots_only_enabled=_server_roots_only_enabled,
        asyncio=asyncio,
        logger=logger,
    )


async def _ensure_allowed_roots(
    ctx: Optional[Context], tool_name: str
) -> tuple[List[Path], Optional[str]]:
    return await __file_policy._ensure_allowed_roots(
        ctx=ctx,
        tool_name=tool_name,
        ROOTS_STATUS_CLIENT_DENY_ALL=ROOTS_STATUS_CLIENT_DENY_ALL,
        ROOTS_STATUS_ERROR=ROOTS_STATUS_ERROR,
        ROOTS_STATUS_TIMEOUT=ROOTS_STATUS_TIMEOUT,
        _get_effective_allowed_roots_with_status=_get_effective_allowed_roots_with_status,
    )


async def _resolve_readable_file_path(
    *,
    raw_path: str,
    ctx: Optional[Context],
    tool_name: str,
) -> tuple[Optional[Path], Optional[str]]:
    return await __file_policy._resolve_readable_file_path(
        raw_path=raw_path,
        ctx=ctx,
        tool_name=tool_name,
        Path=Path,
        _contains_forbidden_path_patterns=_contains_forbidden_path_patterns,
        _ensure_allowed_roots=_ensure_allowed_roots,
        _ensure_extension_allowed=_ensure_extension_allowed,
        _ensure_size_within_limit=_ensure_size_within_limit,
        _first_resolution_root=_first_resolution_root,
        _path_is_within_any_root=_path_is_within_any_root,
        os=os,
    )


async def _resolve_writable_file_path(
    *,
    raw_path: Optional[str],
    default_filename: str,
    ctx: Optional[Context],
    tool_name: str,
) -> tuple[Optional[Path], Optional[str]]:
    return await __file_policy._resolve_writable_file_path(
        raw_path=raw_path,
        default_filename=default_filename,
        ctx=ctx,
        tool_name=tool_name,
        DEFAULT_DOWNLOAD_SUBDIR=DEFAULT_DOWNLOAD_SUBDIR,
        Path=Path,
        _contains_forbidden_path_patterns=_contains_forbidden_path_patterns,
        _ensure_allowed_roots=_ensure_allowed_roots,
        _ensure_extension_allowed=_ensure_extension_allowed,
        _first_resolution_root=_first_resolution_root,
        _path_is_within_any_root=_path_is_within_any_root,
        os=os,
    )


# Global variables to store CLI-parsed configuration for runner.py
_CLI_TRANSPORT = None
_CLI_HOST = None
_CLI_PORT = None


def _parse_allowed_roots_env(value: Optional[str]) -> List[str]:
    """Parse a delimiter-separated list of paths from an environment variable string.

    Supports semicolon (;) and comma (,) across all platforms, as well as colon (:)
    when not part of a Windows drive letter prefix (e.g. C:\\path).
    """
    return __file_policy._parse_allowed_roots_env(
        value=value,
        re=re,
    )


def _configure_allowed_roots_from_cli(argv: Optional[List[str]] = None) -> None:
    global SERVER_ALLOWED_ROOTS, _CLI_TRANSPORT, _CLI_HOST, _CLI_PORT
    SERVER_ALLOWED_ROOTS, _CLI_TRANSPORT, _CLI_HOST, _CLI_PORT = (
        __file_policy._configure_allowed_roots_from_cli(
            argv=argv,
            List=List,
            Path=Path,
            _dedupe_paths=_dedupe_paths,
            _parse_allowed_roots_env=_parse_allowed_roots_env,
            argparse=argparse,
            os=os,
        )
    )


# Re-export shared runtime names for tool modules that use star imports.
__all__ = [name for name in globals() if not name.startswith("__")]
