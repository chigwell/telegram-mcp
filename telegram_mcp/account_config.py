"""Private account_config implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Optional
from telethon import TelegramClient


def _get_proxy_env(name: str, label: str, *, os) -> Optional[str]:
    """Resolve a TELEGRAM_PROXY_* env var with optional ``_<LABEL>`` suffix.

    Per-account values override the unsuffixed defaults so a global proxy can
    coexist with per-label overrides.
    """
    suffixed = os.getenv(f"TELEGRAM_PROXY_{name}_{label.upper()}")
    if suffixed:
        return suffixed
    return os.getenv(f"TELEGRAM_PROXY_{name}") or None


def _parse_bool_env(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _build_proxy_for_label(
    label: str, *, Any, ValidationError, _PROXY_TYPES_ALL, _get_proxy_env, _parse_bool_env
) -> tuple[Optional[Any], Optional[Any]]:
    """Return ``(proxy, connection)`` kwargs for ``TelegramClient`` for a label.

    Reads ``TELEGRAM_PROXY_*`` env vars (with optional ``_<LABEL>`` suffix).
    Returns ``(None, None)`` when no proxy is configured. Raises
    :class:`ValidationError` for malformed configuration so the server fails
    fast instead of silently bypassing the proxy.
    """
    proxy_type = _get_proxy_env("TYPE", label)
    if not proxy_type:
        return None, None

    proxy_type = proxy_type.strip().lower()
    if proxy_type not in _PROXY_TYPES_ALL:
        raise ValidationError(
            f"Invalid TELEGRAM_PROXY_TYPE '{proxy_type}'. "
            f"Expected one of: {', '.join(sorted(_PROXY_TYPES_ALL))}."
        )

    host = _get_proxy_env("HOST", label)
    port_raw = _get_proxy_env("PORT", label)
    if not host or not port_raw:
        raise ValidationError(
            "TELEGRAM_PROXY_HOST and TELEGRAM_PROXY_PORT are required when "
            "TELEGRAM_PROXY_TYPE is set."
        )
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValidationError(
            f"TELEGRAM_PROXY_PORT must be an integer, got '{port_raw}'."
        ) from exc

    if proxy_type == "mtproxy":
        secret = _get_proxy_env("SECRET", label)
        if not secret:
            raise ValidationError("TELEGRAM_PROXY_SECRET is required for mtproxy.")
        try:
            from telethon.network import ConnectionTcpMTProxyRandomizedIntermediate
        except ImportError as exc:  # pragma: no cover - defensive guard
            raise ValidationError(
                "Telethon MTProxy connection class is unavailable; upgrade telethon."
            ) from exc
        return (host, port, secret), ConnectionTcpMTProxyRandomizedIntermediate

    # SOCKS4/SOCKS5/HTTP via python-socks (Telethon's optional dependency).
    try:
        import python_socks  # noqa: F401
    except ImportError as exc:
        raise ValidationError(
            f"Proxy type '{proxy_type}' requires the 'python-socks' package. "
            "Install it with `pip install python-socks` or `uv sync --extra proxy`."
        ) from exc

    proxy: dict[str, Any] = {
        "proxy_type": proxy_type,
        "addr": host,
        "port": port,
        "rdns": _parse_bool_env(_get_proxy_env("RDNS", label), default=True),
    }
    username = _get_proxy_env("USERNAME", label)
    password = _get_proxy_env("PASSWORD", label)
    if username:
        proxy["username"] = username
    if password:
        proxy["password"] = password
    return proxy, None


def _resolve_session_path(session_name: str, *, PROJECT_ROOT, find_dotenv, os) -> str:
    """Resolve a relative session name against the project root.

    When TELEGRAM_SESSION_NAME is a relative path/name (e.g. 'my_session' or
    'sessions/main'), running from a different working directory makes Telethon
    search os.getcwd() and fail to find the existing .session file, triggering
    an interactive login prompt.
    This resolves the relative path against the repository/project root (or the
    directory where .env was found) if the file exists there, or if running
    from a subdirectory of the project root.
    """
    if not session_name or os.path.isabs(session_name) or session_name == ":memory:":
        return session_name

    candidate_roots = [PROJECT_ROOT]
    try:
        env_file = find_dotenv()
        if env_file:
            env_dir = os.path.dirname(os.path.abspath(env_file))
            if env_dir not in candidate_roots:
                candidate_roots.append(env_dir)
    except Exception:
        pass

    for root in candidate_roots:
        target = os.path.join(root, session_name)
        target_session = target if target.endswith(".session") else f"{target}.session"
        if os.path.exists(target) or os.path.exists(target_session):
            return target

    # If running from a subdirectory of project root, resolve to project root
    # so subdirectories don't lose the session file
    cwd = os.path.abspath(os.getcwd())
    try:
        if os.path.commonpath([cwd, PROJECT_ROOT]) == PROJECT_ROOT and cwd != PROJECT_ROOT:
            return os.path.join(PROJECT_ROOT, session_name)
    except ValueError:
        pass

    return session_name


def _build_client(
    session: Any,
    label: str,
    *,
    Any,
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TelegramClient,
    _build_proxy_for_label,
    _get_flood_sleep_threshold,
    _resolve_session_path,
    client_identity_kwargs,
) -> TelegramClient:
    """Construct a ``TelegramClient`` honoring per-label proxy and flood sleep configuration."""
    if isinstance(session, str):
        session = _resolve_session_path(session)
    proxy, connection = _build_proxy_for_label(label)
    kwargs: dict[str, Any] = {}
    if proxy is not None:
        kwargs["proxy"] = proxy
    if connection is not None:
        kwargs["connection"] = connection
    # Read flood sleep threshold dynamically so runtime env changes take effect
    kwargs["flood_sleep_threshold"] = _get_flood_sleep_threshold()
    kwargs.update(client_identity_kwargs())
    return TelegramClient(session, TELEGRAM_API_ID, TELEGRAM_API_HASH, **kwargs)


def _parse_session_pool(*, List, os, re) -> List[str]:
    """Parse TELEGRAM_SESSION_STRINGS into a de-duplicated list of sessions."""
    raw = os.getenv("TELEGRAM_SESSION_STRINGS")
    if not raw:
        return []
    pool: List[str] = []
    for tok in re.split(r"[\s,;]+", raw.strip()):
        if tok and tok not in pool:
            pool.append(tok)
    return pool


def _acquire_session(
    pool: List[str], *, _SESSION_LOCKS, hashlib, os, sys, tempfile, try_lock_exclusive
) -> str:
    """Claim the first free session in the pool via an advisory file lock."""
    lock_dir = os.path.join(tempfile.gettempdir(), "telegram-mcp-session-locks")
    try:
        os.makedirs(lock_dir, exist_ok=True)
    except OSError:
        lock_dir = tempfile.gettempdir()
    for idx, session in enumerate(pool):
        digest = hashlib.sha1(session.encode("utf-8")).hexdigest()[:16]
        lock_path = os.path.join(lock_dir, f"session-{digest}.lock")
        try:
            # "a+", not "w": on Windows the lock covers the first byte, and
            # truncating a file another live client holds is refused.
            fh = open(lock_path, "a+")
        except OSError:
            continue
        if not try_lock_exclusive(fh):
            # Locked by another live client — try the next session.
            try:
                fh.close()
            except Exception:
                pass
            continue
        _SESSION_LOCKS.append(fh)
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"pid={os.getpid()}\n")
            fh.flush()
        except OSError:
            pass
        print(f"Using Telegram session slot {idx + 1}/{len(pool)}.", file=sys.stderr)
        return session
    # Handing out an already-claimed session here would make Telegram burn it
    # with AuthKeyDuplicatedError — losing the slot for the client that owns it
    # too. Refusing to start is recoverable; a burned session is not.
    raise RuntimeError(
        f"All {len(pool)} pooled Telegram session(s) are already claimed by other "
        "live clients, so this one has no session to use. Add another session to "
        "TELEGRAM_SESSION_STRINGS (generate it with "
        "`uv run session_string_generator.py`) — one slot per concurrent client — "
        "or stop one of the other clients."
    )


def _discover_accounts(
    *,
    StringSession,
    TelegramClient,
    _acquire_session,
    _build_client,
    _parse_session_pool,
    _resolve_session_path,
    os,
    sys,
) -> dict[str, TelegramClient]:
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
    accounts: dict[str, TelegramClient] = {}

    prefix_str = "TELEGRAM_SESSION_STRING_"
    prefix_name = "TELEGRAM_SESSION_NAME_"

    for key, value in os.environ.items():
        if key.startswith(prefix_str) and value:
            label = key[len(prefix_str) :].lower()
            accounts[label] = _build_client(StringSession(value), label)
        elif key.startswith(prefix_name) and value:
            label = key[len(prefix_name) :].lower()
            accounts[label] = _build_client(_resolve_session_path(value), label)

    # Backward-compatible unsuffixed variables. A pool (TELEGRAM_SESSION_STRINGS)
    # takes precedence for the default account and claims a free session slot.
    session_pool = _parse_session_pool()
    session_string = os.getenv("TELEGRAM_SESSION_STRING")
    session_name = os.getenv("TELEGRAM_SESSION_NAME")

    if "default" not in accounts:
        if session_pool:
            accounts["default"] = _build_client(
                StringSession(_acquire_session(session_pool)), "default"
            )
        elif session_string:
            accounts["default"] = _build_client(StringSession(session_string), "default")
        elif session_name:
            accounts["default"] = _build_client(_resolve_session_path(session_name), "default")

    if not accounts:
        print(
            "Error: No Telegram session configured. "
            "Set TELEGRAM_SESSION_STRING or TELEGRAM_SESSION_STRING_<LABEL> in .env",
            file=sys.stderr,
        )
        sys.exit(1)

    return accounts


def get_client(account: str, *, clients) -> TelegramClient:
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


def is_multi_mode(*, clients) -> bool:
    """Return True when more than one account is configured."""
    return len(clients) > 1
