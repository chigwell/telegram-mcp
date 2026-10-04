"""Private mcp_policy implementations; current bindings come from runtime adapters."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from typing import Optional


def _tool_timeout_seconds(
    value: Optional[str], *, TOOL_TIMEOUT_SECONDS_DEFAULT, os
) -> Optional[float]:
    """Return the server-side ceiling for one MCP tool call.

    The default stays just above the two event-wait tools' 50-second defaults,
    while ensuring a wedged Telethon request becomes an explicit MCP error
    before common client-side one-minute timeouts. Set the value to ``0`` or a
    negative number only for a deliberately unbounded operator session.
    """
    raw_value = os.getenv("TELEGRAM_TOOL_TIMEOUT_SECONDS") if value is None else value
    if not raw_value:
        return TOOL_TIMEOUT_SECONDS_DEFAULT
    try:
        timeout = float(raw_value)
    except ValueError:
        return TOOL_TIMEOUT_SECONDS_DEFAULT
    return timeout if timeout > 0 else None


def _split_exposed_tools_mode(
    mode: str, *, _EXPOSED_TOOLS_ALLOW_SEPARATOR
) -> tuple[str, list[str]]:
    """Split a normalised exposure mode into its base mode and write allowlist."""
    base, separator, raw_allowlist = mode.partition(_EXPOSED_TOOLS_ALLOW_SEPARATOR)
    if not separator:
        return base, []
    return base, [name.strip() for name in raw_allowlist.split(",") if name.strip()]


def _get_exposed_tools_mode(
    value: Optional[str],
    *,
    _EXPOSED_TOOLS_ALLOW_SEPARATOR,
    _EXPOSED_TOOLS_MODES,
    _split_exposed_tools_mode,
    os,
) -> str:
    """Return the configured MCP tool exposure mode.

    ``TELEGRAM_EXPOSED_TOOLS=read-only`` keeps only tools annotated with
    ``readOnlyHint=True``. ``read-only+send_message,reply_to_message`` keeps
    those plus the named write tools. The default is ``all`` for backward
    compatibility.
    """
    raw_value = os.getenv("TELEGRAM_EXPOSED_TOOLS", "all") if value is None else value
    mode = raw_value.strip().lower()
    base_mode, allowlist = _split_exposed_tools_mode(mode)
    if base_mode not in _EXPOSED_TOOLS_MODES:
        accepted = ", ".join(sorted(_EXPOSED_TOOLS_MODES))
        raise SystemExit(
            f"Invalid TELEGRAM_EXPOSED_TOOLS '{raw_value}'. Expected one of: {accepted}."
        )
    if _EXPOSED_TOOLS_ALLOW_SEPARATOR not in mode:
        return base_mode
    if base_mode != "read-only":
        raise SystemExit(
            f"Invalid TELEGRAM_EXPOSED_TOOLS '{raw_value}'. The "
            f"'{_EXPOSED_TOOLS_ALLOW_SEPARATOR}tool,tool' allowlist is only valid "
            "with read-only."
        )
    if not allowlist:
        raise SystemExit(
            f"Invalid TELEGRAM_EXPOSED_TOOLS '{raw_value}'. The "
            f"'{_EXPOSED_TOOLS_ALLOW_SEPARATOR}' allowlist must name at least one tool."
        )
    return f"{base_mode}{_EXPOSED_TOOLS_ALLOW_SEPARATOR}{','.join(allowlist)}"


def _apply_exposed_tools_mode(
    server: FastMCP, mode: Optional[str], *, _get_exposed_tools_mode, _split_exposed_tools_mode
) -> list[str]:
    """Prune registered MCP tools according to the configured exposure mode."""
    selected_mode = _get_exposed_tools_mode() if mode is None else _get_exposed_tools_mode(mode)
    base_mode, allowlist = _split_exposed_tools_mode(selected_mode)
    if base_mode == "all":
        return []

    registered = {tool.name for tool in server._tool_manager.list_tools()}
    unknown = sorted(set(allowlist) - registered)
    if unknown:
        # Fail loudly: a typo must not silently degrade into a narrower allowlist
        # that looks like it worked.
        raise SystemExit(
            f"Invalid TELEGRAM_EXPOSED_TOOLS allowlist: unknown tool(s) {', '.join(unknown)}."
        )

    allowed = set(allowlist)
    removed: list[str] = []
    for tool in list(server._tool_manager.list_tools()):
        if tool.name in allowed:
            continue
        annotations = getattr(tool, "annotations", None)
        if not getattr(annotations, "readOnlyHint", False):
            server._tool_manager.remove_tool(tool.name)
            removed.append(tool.name)
    return removed


async def call_with_timeout(
    req, original_handler, *, timeout, asyncio, ServerResult, CallToolResult, TextContent
):
    """Keep the existing timeout error and unknown-write-completion warning."""
    if timeout is None:
        response = await original_handler(req)
    else:
        try:
            response = await asyncio.wait_for(original_handler(req), timeout=timeout)
        except asyncio.TimeoutError:
            response = ServerResult(
                CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=(
                                "Telegram MCP tool timed out after "
                                f"{timeout:g}s (code: GEN-TIMEOUT). "
                                "Completion is unknown; a write may already have "
                                "succeeded. Check destination state before retrying "
                                "non-idempotent operations."
                            ),
                        )
                    ],
                    isError=True,
                )
            )
    return response


def annotate_result(
    response, *, audience, ServerResult, CallToolResult, TextContent, ImageContent
):
    """Apply the existing audience boundary without replacing explicit annotations."""
    if isinstance(response, ServerResult) and isinstance(response.root, CallToolResult):
        content = response.root.content
        if content:
            response.root.content = [
                (
                    block.model_copy(update={"annotations": audience})
                    if isinstance(block, (TextContent, ImageContent)) and block.annotations is None
                    else block
                )
                for block in content
            ]
    return response
