"""MCPL capability advertisement.

Returns the dict that goes under `experimental.mcpl` in the server's
`initialize` response. Phase 1: the values reflect what we *will* support
once Phases 2–6 land. Until then the host sees the capability flags but
calls to those methods will return JSON-RPC "method not found" — that's
fine, MCPL is fail-open.
"""

from __future__ import annotations

from typing import Any

from . import MCPL_VERSION


def build_mcpl_capabilities() -> dict[str, Any]:
    """Return the dict to inject as `experimental.mcpl` on `initialize`."""
    return {
        "version": MCPL_VERSION,
        "pushEvents": True,
        "channels": {
            "register": True,
            "publish": True,
            "observe": False,
            "lifecycle": True,
            "streaming": False,
        },
    }


def build_experimental_capabilities() -> dict[str, dict[str, Any]]:
    """Wrap our capabilities for `Server.create_initialization_options`.

    The mcp SDK takes `experimental_capabilities: dict[str, dict[str, Any]]`
    where the top-level keys are namespaces. Ours is `mcpl`.
    """
    return {"mcpl": build_mcpl_capabilities()}
