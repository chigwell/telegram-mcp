"""Safeguards that must survive tools returning image content, not just text."""

import asyncio

import pytest
from mcp.server.fastmcp import Image
from mcp.types import (
    Annotations,
    CallToolRequest,
    CallToolResult,
    ImageContent,
    ServerResult,
    TextContent,
)

from telegram_mcp import runtime


@pytest.fixture
def two_accounts(monkeypatch):
    monkeypatch.setattr(runtime, "clients", {"personal": object(), "work": object()})


@pytest.mark.asyncio
async def test_text_only_fan_out_keeps_the_joined_string(two_accounts):
    @runtime.with_account(readonly=True)
    async def describe(account=None):
        return f"described by {account}"

    result = await describe()

    assert result == "[personal]\ndescribed by personal\n\n[work]\ndescribed by work"


@pytest.mark.asyncio
async def test_image_fan_out_returns_content_blocks_instead_of_stringifying(two_accounts):
    @runtime.with_account(readonly=True)
    async def render(account=None):
        return Image(data=b"jpeg-bytes-" + account.encode(), format="jpeg")

    result = await render()

    assert isinstance(result, list)
    assert result[0] == "[personal]"
    assert isinstance(result[1], Image)
    assert result[2] == "[work]"
    assert isinstance(result[3], Image)


@pytest.mark.asyncio
async def test_mixed_text_and_image_fan_out_is_flattened(two_accounts):
    @runtime.with_account(readonly=True)
    async def overview(account=None):
        return [f"index for {account}", Image(data=b"sheet", format="jpeg")]

    result = await overview()

    assert result[0] == "[personal]"
    assert result[1] == "index for personal"
    assert isinstance(result[2], Image)
    assert result[3] == "[work]"


@pytest.mark.asyncio
async def test_image_results_are_annotated_as_user_audience():
    async def original_handler(req):
        return ServerResult(
            CallToolResult(
                content=[
                    TextContent(type="text", text="caption"),
                    ImageContent(type="image", data="Zm9v", mimeType="image/jpeg"),
                ]
            )
        )

    from mcp.types import CallToolRequest

    handlers = runtime.mcp._mcp_server.request_handlers
    installed_handler = handlers[CallToolRequest]
    handlers[CallToolRequest] = original_handler
    try:
        runtime._install_annotation_hook()
        response = await handlers[CallToolRequest](None)
    finally:
        handlers[CallToolRequest] = installed_handler

    text_block, image_block = response.root.content
    assert text_block.annotations.audience == ["user"]
    assert image_block.annotations.audience == ["user"]


@pytest.mark.asyncio
async def test_call_tool_timeout_returns_an_explicit_annotated_error(monkeypatch):
    async def original_handler(req):
        await asyncio.Event().wait()

    from mcp.types import CallToolRequest

    handlers = runtime.mcp._mcp_server.request_handlers
    installed_handler = handlers[CallToolRequest]
    handlers[CallToolRequest] = original_handler
    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.01")
    try:
        runtime._install_annotation_hook()
        response = await handlers[CallToolRequest](None)
    finally:
        handlers[CallToolRequest] = installed_handler

    assert response.root.isError is True
    assert response.root.content[0].text == (
        "Telegram MCP tool timed out after 0.01s (code: GEN-TIMEOUT). "
        "Completion is unknown; a write may already have succeeded. "
        "Check destination state before retrying non-idempotent operations."
    )
    assert response.root.content[0].annotations.audience == ["user"]


@pytest.mark.asyncio
async def test_timeout_after_accepted_write_reports_unknown_completion_once(monkeypatch):
    marker = "synthetic-write-marker-4f1c"
    accepted_writes = []

    async def original_handler(req):
        accepted_writes.append(marker)  # the write landed, then the call stalled
        await asyncio.Event().wait()

    from mcp.types import CallToolRequest

    handlers = runtime.mcp._mcp_server.request_handlers
    installed_handler = handlers[CallToolRequest]
    handlers[CallToolRequest] = original_handler
    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0.01")
    try:
        runtime._install_annotation_hook()
        response = await handlers[CallToolRequest](None)
    finally:
        handlers[CallToolRequest] = installed_handler

    assert accepted_writes == [marker]  # dispatched exactly once, never retried
    assert response.root.isError is True
    assert len(response.root.content) == 1
    text = response.root.content[0].text
    assert "code: GEN-TIMEOUT" in text
    assert "Completion is unknown" in text
    assert "a write may already have succeeded" in text
    assert "before retrying" in text
    assert marker not in text
    assert response.root.content[0].annotations.audience == ["user"]


@pytest.mark.parametrize(
    "value, expected",
    [(None, 55.0), ("", 55.0), ("garbage", 55.0), ("3.5", 3.5), ("0", None)],
)
def test_tool_timeout_parsing(monkeypatch, value, expected):
    monkeypatch.delenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", raising=False)
    assert runtime._tool_timeout_seconds(value) == expected


@pytest.mark.asyncio
async def test_disabled_tool_timeout_does_not_relabel_handler_timeout(monkeypatch):
    async def original_handler(req):
        raise asyncio.TimeoutError("tool-specific timeout")

    from mcp.types import CallToolRequest

    handlers = runtime.mcp._mcp_server.request_handlers
    installed_handler = handlers[CallToolRequest]
    handlers[CallToolRequest] = original_handler
    monkeypatch.setenv("TELEGRAM_TOOL_TIMEOUT_SECONDS", "0")
    try:
        runtime._install_annotation_hook()
        with pytest.raises(asyncio.TimeoutError, match="tool-specific timeout"):
            await handlers[CallToolRequest](None)
    finally:
        handlers[CallToolRequest] = installed_handler


@pytest.mark.asyncio
async def test_annotation_hook_preserves_explicit_text_and_image_annotations(monkeypatch):
    explicit = Annotations(audience=["assistant"], priority=0.5)
    response = ServerResult(
        CallToolResult(
            content=[
                TextContent(type="text", text="caption", annotations=explicit),
                ImageContent(
                    type="image", data="Zm9v", mimeType="image/jpeg", annotations=explicit
                ),
            ]
        )
    )
    original_blocks = tuple(response.root.content)

    async def original_handler(req):
        return response

    handlers = runtime.mcp._mcp_server.request_handlers
    monkeypatch.setitem(handlers, CallToolRequest, original_handler)
    monkeypatch.setattr(runtime, "_tool_timeout_seconds", lambda: None)
    runtime._install_annotation_hook()

    actual = await handlers[CallToolRequest](None)

    assert actual is response
    assert len(actual.root.content) == len(original_blocks)
    for block, original in zip(actual.root.content, original_blocks):
        assert block is original
        assert block.annotations == explicit


@pytest.mark.asyncio
async def test_annotation_hook_reads_current_audience_after_handler_returns(monkeypatch):
    entered = asyncio.Event()
    resume = asyncio.Event()
    response = ServerResult(
        CallToolResult(
            content=[
                TextContent(type="text", text="caption"),
                ImageContent(type="image", data="Zm9v", mimeType="image/jpeg"),
            ]
        )
    )

    async def original_handler(req):
        entered.set()
        await resume.wait()
        return response

    handlers = runtime.mcp._mcp_server.request_handlers
    monkeypatch.setitem(handlers, CallToolRequest, original_handler)
    monkeypatch.setattr(runtime, "_tool_timeout_seconds", lambda: None)
    monkeypatch.setattr(runtime, "_USER_AUDIENCE", Annotations(audience=["user"]))
    runtime._install_annotation_hook()
    task = asyncio.create_task(handlers[CallToolRequest](None))
    replacement = Annotations(audience=["assistant"], priority=0.75)
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        monkeypatch.setattr(runtime, "_USER_AUDIENCE", replacement)
        resume.set()
        actual = await asyncio.wait_for(task, timeout=1)
    finally:
        resume.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert actual is response
    assert len(actual.root.content) == 2
    assert all(block.annotations == replacement for block in actual.root.content)
