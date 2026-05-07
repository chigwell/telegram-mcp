"""MCPL method dispatcher.

Routes inbound MCPL JSON-RPC frames to registered handlers, manages
outbound request/response correlation, and is the single source of truth
for which method names this server claims to handle.

Phase 2 ships the routing infrastructure with zero handlers — methods
hit by the host return JSON-RPC -32601 (method not found) until later
phases register real implementations. Notifications without a handler
are dropped silently per JSON-RPC convention.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .transport import McplTransport

log = logging.getLogger("telegram_mcp.mcpl")

# Method names this server handles or emits. The intercepting transport
# checks `method in MCPL_METHODS` before forwarding to FastMCP, so any
# method we want to capture must be listed here.
MCPL_METHODS: frozenset[str] = frozenset(
    {
        # Server → Host (we emit; listed for symmetry, never received inbound)
        "channels/register",
        "channels/changed",
        "channels/incoming",
        "push/event",
        # Host → Server (we receive)
        "channels/list",
        "channels/open",
        "channels/close",
        "channels/publish",
        "channels/typing",
        "featureSets/update",
        "state/rollback",
        "context/beforeInference",
        "context/afterInference",
    }
)


class McplError(Exception):
    """Raised when an outbound MCPL request receives a JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"MCPL error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


HandlerFn = Callable[[dict[str, Any]], Awaitable[Any]]


class McplDispatcher:
    """Routes MCPL frames between the transport and the per-method handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, HandlerFn] = {}
        self._pending_outbound: dict[int, asyncio.Future[Any]] = {}
        self._next_id: int = 1

    # -- handler registration -------------------------------------------------

    def register(self, method: str, handler: HandlerFn) -> None:
        """Register an async handler for an inbound MCPL request method."""
        self._handlers[method] = handler

    # -- inbound dispatch -----------------------------------------------------

    async def handle_inbound(
        self,
        raw: dict[str, Any],
        transport: McplTransport,
    ) -> None:
        """Dispatch an inbound MCPL frame.

        Requests (`id` present) get a response (success or method-not-found).
        Notifications (`id` absent) get no response; missing handlers are
        dropped silently with a warning log.
        """
        method = raw.get("method")
        msg_id = raw.get("id")
        params = raw.get("params") or {}

        handler = self._handlers.get(method) if method else None
        if handler is None:
            if msg_id is not None:
                await transport.send_error(
                    msg_id,
                    code=-32601,
                    message=f"Method not found: {method}",
                )
            else:
                log.debug("MCPL notification dropped (no handler): %s", method)
            return

        try:
            result = await handler(params)
        except Exception as exc:  # noqa: BLE001 — JSON-RPC requires we capture all
            log.exception("MCPL handler %s raised", method)
            if msg_id is not None:
                await transport.send_error(
                    msg_id,
                    code=-32603,
                    message=f"Internal error: {exc}",
                )
            return

        if msg_id is not None:
            await transport.send_response(msg_id, result if result is not None else {})

    # -- outbound request/response correlation -------------------------------

    def is_pending_outbound(self, msg_id: Any) -> bool:
        return isinstance(msg_id, int) and msg_id in self._pending_outbound

    def resolve_outbound(self, msg_id: int, raw: dict[str, Any]) -> None:
        fut = self._pending_outbound.pop(msg_id, None)
        if fut is None or fut.done():
            return
        if "error" in raw:
            err = raw["error"]
            fut.set_exception(
                McplError(
                    code=err.get("code", -32000),
                    message=err.get("message", "Unknown error"),
                    data=err.get("data"),
                )
            )
        else:
            fut.set_result(raw.get("result"))

    async def send_request(
        self,
        method: str,
        params: dict[str, Any],
        transport: McplTransport,
    ) -> Any:
        """Send a request to the host and await its response.

        Caller awaits the result. On JSON-RPC error response, raises McplError.
        """
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future[Any] = asyncio.get_event_loop().create_future()
        self._pending_outbound[msg_id] = fut
        try:
            await transport.send_request(msg_id, method, params)
        except Exception:
            self._pending_outbound.pop(msg_id, None)
            raise
        return await fut

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None,
        transport: McplTransport,
    ) -> None:
        """Send a notification (fire-and-forget) to the host."""
        await transport.send_notification(method, params)
