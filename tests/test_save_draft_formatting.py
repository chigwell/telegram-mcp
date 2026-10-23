"""Drafts keep formatting as real entities, without Telegram connections."""

import json
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


def _drafts_update(draft, user_id=42):
    return types.Updates(
        updates=[types.UpdateDraftMessage(peer=types.PeerUser(user_id), draft=draft)],
        users=[],
        chats=[],
        date=None,
        seq=0,
    )


@pytest.fixture
def drafts_client(monkeypatch):
    cl = AsyncMock()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    return cl


@pytest.mark.asyncio
async def test_get_drafts_returns_html_that_round_trips_the_formatting(drafts_client):
    from telethon.extensions import html as _html

    source = (
        '<b>01 CASE</b>\n<u>Ждём</u> — <a href="https://example.com/a?b=1&amp;c=2">гайд</a> 👍'
    )
    text, entities = _html.parse(source)
    drafts_client.return_value = _drafts_update(
        types.DraftMessage(message=text, date=None, entities=entities)
    )

    drafts = json.loads(await messages.get_drafts(account="test"))["drafts"]

    assert drafts[0]["message"] == "01 CASE\nЖдём — гайд 👍"
    reparsed = _html.parse(drafts[0]["message_html"])
    assert reparsed == (text, entities)


@pytest.mark.asyncio
async def test_get_drafts_plain_draft_has_no_html(drafts_client):
    drafts_client.return_value = _drafts_update(
        types.DraftMessage(message="**not bold**", date=None)
    )

    drafts = json.loads(await messages.get_drafts(account="test"))["drafts"]

    assert drafts[0]["message"] == "**not bold**"
    assert "message_html" not in drafts[0]
