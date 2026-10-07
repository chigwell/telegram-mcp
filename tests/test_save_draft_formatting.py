"""Drafts keep formatting as real entities, without Telegram connections."""

from unittest.mock import AsyncMock

import pytest
from telethon.tl import functions, types

from telegram_mcp.tools import messages


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(
        messages, "resolve_input_entity", AsyncMock(return_value=types.InputPeerUser(42, 0))
    )
    return cl


def _request(cl):
    req = cl.await_args.args[0]
    assert isinstance(req, functions.messages.SaveDraftRequest)
    return req


@pytest.mark.asyncio
async def test_plain_draft_is_saved_verbatim(client):
    await messages.save_draft(42, "**not bold**", account="test")
    req = _request(client)
    assert req.message == "**not bold**"
    assert not req.entities


@pytest.mark.asyncio
async def test_html_draft_carries_bold_and_underline(client):
    result = await messages.save_draft(
        42, "<b>01 CASE</b>\n<u>Ждём</u> — ок", parse_mode="html", account="test"
    )
    assert "Draft saved" in result
    req = _request(client)
    assert req.message == "01 CASE\nЖдём — ок"
    kinds = {type(e) for e in req.entities}
    assert kinds == {types.MessageEntityBold, types.MessageEntityUnderline}
    underline = next(e for e in req.entities if isinstance(e, types.MessageEntityUnderline))
    assert (underline.offset, underline.length) == (8, 4)


@pytest.mark.asyncio
async def test_markdown_draft_carries_bold(client):
    await messages.save_draft(42, "**01 CASE** rest", parse_mode="md", account="test")
    req = _request(client)
    assert req.message == "01 CASE rest"
    assert [type(e) for e in req.entities] == [types.MessageEntityBold]


@pytest.mark.asyncio
async def test_unknown_parse_mode_is_rejected_without_a_request(client):
    result = await messages.save_draft(42, "x", parse_mode="rich", account="test")
    assert "not supported" in result
    assert client.await_args is None
