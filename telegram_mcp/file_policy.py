"""Private file_policy implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from mcp.server.fastmcp import Context
from mcp.server.fastmcp import FastMCP
from typing import List
from typing import Optional
from pathlib import Path


def _get_file_extension_overrides(
    value: Optional[str],
    *,
    _FILE_EXTENSIONS_ENTRY_SEPARATOR,
    _FILE_EXTENSIONS_LIST_SEPARATOR,
    _FILE_EXTENSIONS_TOOL_SEPARATOR,
    _FILE_EXTENSION_TOKEN_PATTERN,
    os,
) -> dict[str, set[str]]:
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
    raw_value = os.getenv("TELEGRAM_FILE_EXTENSIONS", "") if value is None else value
    raw_value = raw_value.strip()
    if not raw_value:
        return {}

    overrides: dict[str, set[str]] = {}
    for entry in raw_value.split(_FILE_EXTENSIONS_ENTRY_SEPARATOR):
        entry = entry.strip()
        if not entry:
            continue
        tool_name, separator, raw_extensions = entry.partition(_FILE_EXTENSIONS_TOOL_SEPARATOR)
        tool_name = tool_name.strip().lower()
        if not separator or not tool_name:
            raise SystemExit(
                f"Invalid TELEGRAM_FILE_EXTENSIONS '{raw_value}'. Each entry must look "
                f"like 'tool{_FILE_EXTENSIONS_TOOL_SEPARATOR}.ext{_FILE_EXTENSIONS_LIST_SEPARATOR}.ext', "
                f"entries separated by '{_FILE_EXTENSIONS_ENTRY_SEPARATOR}'."
            )

        extensions: set[str] = set()
        for raw_extension in raw_extensions.split(_FILE_EXTENSIONS_LIST_SEPARATOR):
            token = raw_extension.strip().lower()
            if not token:
                raise SystemExit(
                    f"Invalid TELEGRAM_FILE_EXTENSIONS '{raw_value}'. Tool '{tool_name}' "
                    "has an empty extension entry."
                )
            if not token.startswith("."):
                token = f".{token}"
            if not _FILE_EXTENSION_TOKEN_PATTERN.match(token):
                raise SystemExit(
                    f"Invalid TELEGRAM_FILE_EXTENSIONS '{raw_value}'. Malformed extension "
                    f"'{raw_extension.strip()}' for tool '{tool_name}'."
                )
            extensions.add(token)

        # extensions is never empty here: an empty raw_extensions still yields
        # one blank token from split(","), which is caught above.
        if tool_name in overrides:
            # Fail loudly rather than last-wins: silently dropping the first
            # list would hand the operator a narrower or wider allowlist than
            # the one they wrote, with no way to notice.
            raise SystemExit(
                f"Invalid TELEGRAM_FILE_EXTENSIONS '{raw_value}'. Tool "
                f"'{tool_name}' is named more than once."
            )
        overrides[tool_name] = extensions
    return overrides


def _apply_file_extension_overrides(
    server: FastMCP,
    value: Optional[str],
    *,
    _DEFAULT_EXTENSION_ALLOWLISTS,
    _get_file_extension_overrides,
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
    overrides = _get_file_extension_overrides(value)
    if overrides:
        registered = {tool.name for tool in server._tool_manager.list_tools()}
        unknown = sorted(set(overrides) - registered)
        if unknown:
            # Fail loudly: a typo must not silently degrade into an allowlist
            # that looks like it worked.
            raise SystemExit(
                f"Invalid TELEGRAM_FILE_EXTENSIONS: unknown tool(s) {', '.join(unknown)}."
            )
    EXTENSION_ALLOWLISTS = {**_DEFAULT_EXTENSION_ALLOWLISTS, **overrides}
    return EXTENSION_ALLOWLISTS


def _dedupe_paths(paths: List[Path], *, List, Path) -> List[Path]:
    seen: set[str] = set()
    result: List[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _contains_forbidden_path_patterns(
    raw_path: str, *, DISALLOWED_PATH_PATTERNS, Path
) -> Optional[str]:
    value = raw_path.strip()
    if not value:
        return "Path must not be empty."
    if any(token in value for token in DISALLOWED_PATH_PATTERNS):
        return "Path contains disallowed wildcard/shell patterns."
    if ".." in Path(value).parts:
        return "Path traversal is not allowed."
    return None


def _coerce_root_uri_to_path(uri: str, *, Path, os, unquote, urlparse) -> Path:
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


def _path_is_within_any_root(candidate: Path, roots: List[Path], *, _path_is_within_root) -> bool:
    return any(_path_is_within_root(candidate, root) for root in roots)


def _first_resolution_root(roots: List[Path]) -> Path:
    first = roots[0]
    return first if first.is_dir() else first.parent


def _ensure_extension_allowed(
    tool_name: str, candidate: Path, *, EXTENSION_ALLOWLISTS
) -> Optional[str]:
    allowlist = EXTENSION_ALLOWLISTS.get(tool_name)
    if not allowlist:
        return None
    if candidate.suffix.lower() not in allowlist:
        allowed = ", ".join(sorted(allowlist))
        return f"File extension is not allowed for {tool_name}. Allowed: {allowed}."
    return None


def _ensure_size_within_limit(tool_name: str, candidate: Path, *, MAX_FILE_BYTES) -> Optional[str]:
    max_bytes = MAX_FILE_BYTES.get(tool_name)
    if not max_bytes:
        return None
    size = candidate.stat().st_size
    if size > max_bytes:
        return f"File is too large for {tool_name}: {size} bytes " f"(limit: {max_bytes} bytes)."
    return None


async def _get_effective_allowed_roots(
    ctx: Optional[Context], *, _get_effective_allowed_roots_with_status
) -> List[Path]:
    roots, _status = await _get_effective_allowed_roots_with_status(ctx)
    return roots


def _is_roots_unsupported_error(
    error: Exception, *, McpError, ROOTS_UNSUPPORTED_ERROR_CODES
) -> bool:
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


def _coerce_paths_from_list_roots_validation_error(
    error: Exception, *, List, Path, _dedupe_paths
) -> List[Path]:
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
    errors_fn = getattr(error, "errors", None)
    if not callable(errors_fn):
        return []

    try:
        details = errors_fn()
    except Exception:
        return []

    recovered: List[Path] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("url_parsing", "url_scheme"):
            continue
        value = item.get("input")
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not (candidate.startswith("/") or (len(candidate) > 2 and candidate[1] == ":")):
            # Unix absolute path, or Windows drive path like C:\...
            continue
        try:
            recovered.append(Path(candidate).expanduser().resolve())
        except Exception:
            continue
    return _dedupe_paths(recovered)


def _server_roots_fallback_enabled(value: Optional[str], *, _parse_bool_env, os) -> bool:
    """Whether server CLI roots may replace unusable/empty client Roots.

    Opt-in via the ``TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK`` environment variable.
    Applies when the client returns an empty roots list, or when ``list_roots``
    fails with an unexpected error (after any recoverable client paths are tried).
    Defaults to ``False`` to preserve the safe deny-all behavior.
    """
    raw_value = os.getenv("TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK") if value is None else value
    return _parse_bool_env(raw_value, False)


def _server_roots_only_enabled(value: Optional[str], *, _parse_bool_env, os) -> bool:
    """TELEGRAM_SERVER_ROOTS_ONLY: use server roots and skip roots/list (default off)."""
    raw_value = os.getenv("TELEGRAM_SERVER_ROOTS_ONLY") if value is None else value
    return _parse_bool_env(raw_value, False)


def _roots_request_timeout(
    value: Optional[str], *, ROOTS_REQUEST_TIMEOUT_DEFAULT, os
) -> Optional[float]:
    """Seconds to wait for the client's ``roots/list`` reply.

    Override with ``TELEGRAM_ROOTS_TIMEOUT_SECONDS``; ``0`` or a negative value
    waits forever (the pre-timeout behavior).
    """
    raw_value = os.getenv("TELEGRAM_ROOTS_TIMEOUT_SECONDS") if value is None else value
    if raw_value is None or not str(raw_value).strip():
        return ROOTS_REQUEST_TIMEOUT_DEFAULT
    try:
        timeout = float(raw_value)
    except (TypeError, ValueError):
        return ROOTS_REQUEST_TIMEOUT_DEFAULT
    return timeout if timeout > 0 else None


async def _get_effective_allowed_roots_with_status(
    ctx: Optional[Context],
    *,
    List,
    Path,
    ROOTS_STATUS_CLIENT_DENY_ALL,
    ROOTS_STATUS_ERROR,
    ROOTS_STATUS_NOT_CONFIGURED,
    ROOTS_STATUS_READY,
    ROOTS_STATUS_SERVER_FALLBACK,
    ROOTS_STATUS_SERVER_ONLY,
    ROOTS_STATUS_TIMEOUT,
    ROOTS_STATUS_UNSUPPORTED_FALLBACK,
    SERVER_ALLOWED_ROOTS,
    _coerce_paths_from_list_roots_validation_error,
    _coerce_root_uri_to_path,
    _dedupe_paths,
    _is_roots_unsupported_error,
    _roots_request_timeout,
    _server_roots_fallback_enabled,
    _server_roots_only_enabled,
    asyncio,
    logger,
) -> tuple[List[Path], str]:
    fallback_roots = list(SERVER_ALLOWED_ROOTS)
    if ctx is None:
        if fallback_roots:
            return fallback_roots, ROOTS_STATUS_READY
        return [], ROOTS_STATUS_NOT_CONFIGURED
    if fallback_roots and _server_roots_only_enabled():
        return fallback_roots, ROOTS_STATUS_SERVER_ONLY

    try:
        timeout = _roots_request_timeout()
        if timeout is None:
            list_roots_result = await ctx.session.list_roots()
        else:
            list_roots_result = await asyncio.wait_for(ctx.session.list_roots(), timeout)
    except asyncio.TimeoutError:
        if fallback_roots and _server_roots_fallback_enabled():
            logger.warning(
                "MCP client did not answer roots/list before the configured timeout "
                "(TELEGRAM_ROOTS_TIMEOUT_SECONDS); falling back to server CLI roots "
                "(TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK)."
            )
            return fallback_roots, ROOTS_STATUS_SERVER_FALLBACK
        logger.error(
            "MCP client did not answer roots/list before the configured timeout "
            "(TELEGRAM_ROOTS_TIMEOUT_SECONDS); disabling file-path tools instead "
            "of hanging."
        )
        return [], ROOTS_STATUS_TIMEOUT
    except Exception as error:
        recovered_roots = _coerce_paths_from_list_roots_validation_error(error)
        if recovered_roots:
            logger.warning("MCP client returned non-URI roots; recovered validated paths.")
            return recovered_roots, ROOTS_STATUS_READY
        if _is_roots_unsupported_error(error):
            if fallback_roots:
                return fallback_roots, ROOTS_STATUS_UNSUPPORTED_FALLBACK
            return [], ROOTS_STATUS_NOT_CONFIGURED
        # Unexpected list_roots failures (e.g. malformed client payloads that we
        # could not recover). Match empty-list behavior: opt-in server fallback.
        if fallback_roots and _server_roots_fallback_enabled():
            logger.warning(
                "MCP roots request failed; falling back to server CLI roots "
                "(TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK).",
            )
            return fallback_roots, ROOTS_STATUS_SERVER_FALLBACK
        logger.error("MCP roots request failed; disabling file-path tools for safety.")
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

    # Roots API succeeded but returned an empty list. By default this is an
    # explicit deny-all. Some clients (e.g. ones that implement the Roots
    # capability but expose no roots) advertise an empty list even though the
    # operator configured server-side CLI roots; for those, an opt-in lets the
    # server-side roots take effect instead of disabling file tools entirely.
    if fallback_roots and _server_roots_fallback_enabled():
        return fallback_roots, ROOTS_STATUS_SERVER_FALLBACK
    return [], ROOTS_STATUS_CLIENT_DENY_ALL


async def _ensure_allowed_roots(
    ctx: Optional[Context],
    tool_name: str,
    *,
    ROOTS_STATUS_CLIENT_DENY_ALL,
    ROOTS_STATUS_ERROR,
    ROOTS_STATUS_TIMEOUT,
    _get_effective_allowed_roots_with_status,
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
        if status == ROOTS_STATUS_TIMEOUT:
            return (
                [],
                (
                    f"{tool_name} is disabled because the MCP client never answered the "
                    "roots/list request. Configure server CLI roots and set "
                    "TELEGRAM_SERVER_ROOTS_ONLY=1 (skip roots/list) or "
                    "TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK=1, or raise "
                    "TELEGRAM_ROOTS_TIMEOUT_SECONDS."
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
    Path,
    _contains_forbidden_path_patterns,
    _ensure_allowed_roots,
    _ensure_extension_allowed,
    _ensure_size_within_limit,
    _first_resolution_root,
    _path_is_within_any_root,
    os,
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
    DEFAULT_DOWNLOAD_SUBDIR,
    Path,
    _contains_forbidden_path_patterns,
    _ensure_allowed_roots,
    _ensure_extension_allowed,
    _first_resolution_root,
    _path_is_within_any_root,
    os,
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


def _parse_allowed_roots_env(value: Optional[str], *, re) -> List[str]:
    """Parse a delimiter-separated list of paths from an environment variable string.

    Supports semicolon (;) and comma (,) across all platforms, as well as colon (:)
    when not part of a Windows drive letter prefix (e.g. C:\\path).
    """
    if not value or not value.strip():
        return []
    raw = value.strip()
    tokens = re.split(r"[;,]|(?<!\b[a-zA-Z]):", raw)
    return [part.strip("\"' \t\r\n") for part in tokens if part.strip("\"' \t\r\n")]


def _configure_allowed_roots_from_cli(
    argv: Optional[List[str]], *, List, Path, _dedupe_paths, _parse_allowed_roots_env, argparse, os
) -> tuple[list[Path], str, Optional[str], Optional[int]]:
    parser = argparse.ArgumentParser(
        prog="telegram-mcp",
        add_help=False,
        description=(
            "Optional positional arguments define server-side allowed roots "
            "for file-path tools. Also accepts --transport, --host, and --port CLI flags."
        ),
    )
    parser.add_argument("allowed_roots", nargs="*")
    parser.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parsed, _unknown = parser.parse_known_args(argv or [])

    raw_roots: List[str] = list(parsed.allowed_roots)
    env_roots = os.getenv("TELEGRAM_ALLOWED_ROOTS", "")
    if env_roots:
        raw_roots.extend(_parse_allowed_roots_env(env_roots))

    resolved_roots: List[Path] = []
    for raw_root in raw_roots:
        root = Path(raw_root).expanduser()
        if not root.exists():
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise SystemExit(f"Allowed root does not exist: {root}")
        resolved = root.resolve(strict=True)
        resolved_roots.append(resolved)

    SERVER_ALLOWED_ROOTS = _dedupe_paths(resolved_roots)
    _CLI_TRANSPORT = parsed.transport
    _CLI_HOST = parsed.host
    _CLI_PORT = parsed.port
    return SERVER_ALLOWED_ROOTS, _CLI_TRANSPORT, _CLI_HOST, _CLI_PORT
