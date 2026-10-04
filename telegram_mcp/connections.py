"""Private connections implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from typing import Any
from telethon import TelegramClient
from typing import Union


async def _force_reconnect(
    cl: TelegramClient,
    *,
    AuthKeyDuplicatedError,
    _RECONNECT_TIMEOUT,
    _last_conn_verified,
    asyncio,
    logging,
    time,
):
    """Force disconnect + reconnect regardless of is_connected() state."""
    reconnect_logger = logging.getLogger("telegram_mcp")
    reconnect_logger.warning("Forcing reconnect...")
    try:
        await cl.disconnect()
    except Exception:
        pass
    try:
        await asyncio.wait_for(cl.connect(), timeout=_RECONNECT_TIMEOUT)
    except AuthKeyDuplicatedError as exc:
        # Telegram permanently invalidates an auth key used from two IPs at
        # once, so retrying here can never succeed — surface it instead of
        # letting the caller sit in a reconnect loop.
        raise RuntimeError(
            "Telegram session is no longer usable: the same session string was "
            "used by another client at the same time (AuthKeyDuplicatedError). "
            "Give each concurrent client its own session via "
            "TELEGRAM_SESSION_STRINGS or TELEGRAM_SESSION_STRING_<LABEL>, then "
            "regenerate the burned session with `uv run session_string_generator.py`."
        ) from exc
    except asyncio.TimeoutError as exc:
        raise RuntimeError(
            f"Reconnecting to Telegram timed out after {_RECONNECT_TIMEOUT:.0f}s."
        ) from exc
    if not await cl.is_user_authorized():
        # cl.start() would prompt via blocking input(): it stalls the event loop
        # and, over stdio, reads protocol frames as a phone number.
        try:
            await cl.disconnect()
        except Exception:
            pass
        raise RuntimeError(
            "Telegram session is not authorized after reconnect. Re-authorize it "
            "outside the server (session_string_generator.py) and restart."
        )
    _last_conn_verified[id(cl)] = time.time()
    reconnect_logger.warning("Forced reconnect successful")


async def ensure_connected(
    cl: TelegramClient,
    *,
    _CONN_VERIFY_INTERVAL,
    _force_reconnect,
    _last_conn_verified,
    asyncio,
    functions,
    get_client,
    time,
):
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
    except Exception:
        await _force_reconnect(cl)


def _marked_id_candidates(identifier: Union[int, str]) -> list[int]:
    """Return marked chat/channel ID variants for a bare positive integer ID."""
    if not isinstance(identifier, int) or identifier <= 0:
        return []

    return [
        -1000000000000 - identifier,
        -identifier,
    ]


async def _get_with_cache_warming(get, identifier, client):
    """Retry a cold entity lookup once after a best-effort dialog warm."""
    try:
        return await get(identifier)
    except ValueError:
        try:
            await client.get_dialogs()
        except Exception:
            pass
        return await get(identifier)


async def _resolve_with_retries(
    getter: str,
    identifier: Union[int, str],
    client,
    label: str,
    try_marked: bool,
    *,
    _marked_id_candidates,
    ensure_connected,
):
    """Cache warming, reconnect, and marked-ID fallback shared by both resolvers.

    StringSession has no persistent entity cache, so a cold lookup raises ValueError;
    warming via get_dialogs() and retrying fixes it. A bare positive ID may also need
    Telethon's marked chat/channel variants.
    """
    await ensure_connected(client)
    get = getattr(client, getter)
    last_error = None
    try:
        return await _get_with_cache_warming(get, identifier, client)
    except ValueError as error:
        last_error = error
    except ConnectionError:
        await ensure_connected(client)
        try:
            return await _get_with_cache_warming(get, identifier, client)
        except ValueError as error:
            last_error = error

    if try_marked:
        for candidate in _marked_id_candidates(identifier):
            try:
                return await get(candidate)
            except ValueError as error:
                last_error = error

    raise ValueError(
        f"Could not resolve {label} for {identifier!r}, "
        f"including marked variants {_marked_id_candidates(identifier)}"
    ) from last_error


async def _resolve(
    getter: str,
    identifier: Union[int, str],
    client,
    label: str,
    *,
    _PEER_ERRORS,
    _resolve_with_retries,
    alias_failure,
    apply_alias,
    get_client,
) -> Any:
    """Resolve an identifier, turning a failed free-text reference into a question.

    A saved alias resolves here as well as in @validate_id, so tools without that
    decorator understand nicknames too.
    """
    original = identifier
    identifier = apply_alias(identifier)
    if client is None:
        client = get_client()
    try:
        # An id that came from a saved alias is exact; guessing marked variants of
        # it could deliver to a completely unrelated chat.
        from_alias = identifier is not original
        return await _resolve_with_retries(
            getter, identifier, client, label, try_marked=not from_alias
        )
    except (ValueError, *_PEER_ERRORS) as error:
        # An unknown or stale nickname is a question for the user, not a dead end:
        # report the wording they used, never the opaque stored id.
        needs_user = alias_failure(original, identifier)
        if needs_user:
            raise needs_user from error
        raise


async def resolve_entity(identifier: Union[int, str], client, *, _resolve) -> Any:
    """Resolve an entity, warming the cache and retrying as needed.

    Accepts IDs, usernames, phone numbers, and saved contact aliases.
    """
    return await _resolve("get_entity", identifier, client, "entity")


async def resolve_input_entity(identifier: Union[int, str], client, *, _resolve) -> Any:
    """Like resolve_entity() but returns an InputPeer."""
    return await _resolve("get_input_entity", identifier, client, "input entity")
