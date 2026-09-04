"""Tests for rich message sending (Telegram Premium gated)."""

import json
from types import SimpleNamespace

import pytest
import telethon

from telegram_mcp import runtime
from telegram_mcp.tools import messages


class _FakeClient:
    def __init__(self, premium=True, rpc_error=None):
        self._premium = premium
        self._rpc_error = rpc_error
        self.requests = []

    async def get_me(self):
        return SimpleNamespace(premium=self._premium)

    async def __call__(self, request):
        self.requests.append(request)
        if self._rpc_error is not None:
            raise self._rpc_error
        return SimpleNamespace()


def test_make_rich_input_routes_by_mode():
    md = runtime.make_rich_input("rich_markdown", "| a | b |")
    assert isinstance(md, runtime.types.InputRichMessageMarkdown)
    assert md.markdown == "| a | b |"

    html = runtime.make_rich_input("rich_html", "<table></table>")
    assert isinstance(html, runtime.types.InputRichMessageHTML)
    assert html.html == "<table></table>"


@pytest.mark.asyncio
async def test_send_rich_without_premium_sends_nothing():
    cl = _FakeClient(premium=False)
    result = json.loads(await messages._send_rich(cl, "peer", "| a |", "rich"))

    assert result["sent"] is False
    assert result["reason"] == "telegram_premium_required"
    assert cl.requests == []  # nothing hit the network


@pytest.mark.asyncio
async def test_send_rich_with_premium_sends_rich_request():
    cl = _FakeClient(premium=True)
    result = json.loads(await messages._send_rich(cl, "peer", "| a |", "rich", reply_to=5))

    assert result == {"sent": True, "rich": True}
    (req,) = cl.requests
    assert isinstance(req.rich_message, runtime.types.InputRichMessageMarkdown)
    assert req.reply_to.reply_to_msg_id == 5


@pytest.mark.asyncio
async def test_send_rich_premium_lapsed_midflight():
    err = telethon.errors.RPCError(None, "PREMIUM_ACCOUNT_REQUIRED", 403)
    cl = _FakeClient(premium=True, rpc_error=err)
    result = json.loads(await messages._send_rich(cl, "peer", "x", "rich"))

    assert result["sent"] is False
    assert result["reason"] == "telegram_premium_required"


@pytest.mark.asyncio
async def test_send_rich_other_rpc_error_propagates():
    err = telethon.errors.RPCError(None, "FLOOD_WAIT", 420)
    cl = _FakeClient(premium=True, rpc_error=err)
    with pytest.raises(telethon.errors.RPCError):
        await messages._send_rich(cl, "peer", "x", "rich")


class _EditRecorder:
    """Records how edit_message forwards parse_mode to Telethon."""

    def __init__(self):
        self.calls = []

    async def edit_message(self, entity, message_id, text, **kwargs):
        self.calls.append(kwargs)


@pytest.mark.asyncio
async def test_edit_message_omits_parse_mode_when_not_given(monkeypatch):
    # Telethon treats an explicit None as "disable parsing" while an omitted
    # argument uses its default parser, so callers who never passed parse_mode
    # must keep getting formatted edits.
    cl = _EditRecorder()
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)

    async def fake_resolve(chat_id, client=None):
        return "entity"

    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)

    await messages.edit_message(chat_id=1, message_id=2, new_text="**bold**")
    assert cl.calls == [{}]

    await messages.edit_message(chat_id=1, message_id=2, new_text="x", parse_mode="html")
    assert cl.calls[-1] == {"parse_mode": "html"}


@pytest.mark.asyncio
async def test_edit_rich_both_premium_cases():
    ok = _FakeClient(premium=True)
    result = json.loads(await messages._edit_rich(ok, "peer", 7, "new", "rich_html"))
    assert result["sent"] is True and result["edited_message_id"] == 7
    (req,) = ok.requests
    assert isinstance(req.rich_message, runtime.types.InputRichMessageHTML)

    no = _FakeClient(premium=False)
    result = json.loads(await messages._edit_rich(no, "peer", 7, "new", "rich"))
    assert result["reason"] == "telegram_premium_required"
    assert no.requests == []


# --- reading rich messages -------------------------------------------------
# A channel posting in the block format leaves msg.message empty and puts every
# word into msg.rich_message, so such a post used to read back as "[empty]".

types = runtime.types


def _msg(**overrides):
    base = dict(
        id=224,
        sender=None,
        sender_id=42,
        date="2026-09-03",
        message="",
        reply_to=None,
        rich_message=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _plain(text):
    return types.TextPlain(text=text)


def _rich(*blocks):
    return types.RichMessage(blocks=list(blocks), photos=[], documents=[])


def _cell(text):
    return types.PageTableCell(
        header=False,
        align_center=False,
        align_right=False,
        valign_middle=False,
        valign_bottom=False,
        text=_plain(text),
        colspan=None,
        rowspan=None,
    )


def _post_like_agents_lab_224():
    """The shape a real block-format post arrives in: a heading carrying a
    custom emoji, an uncaptioned photo, then paragraphs."""
    return _rich(
        types.PageBlockHeading1(
            text=types.TextConcat(
                texts=[
                    _plain("Личная CRM "),
                    types.TextCustomEmoji(document_id=5927026418616636353, alt="🧠"),
                ]
            )
        ),
        types.PageBlockPhoto(
            photo_id=5197653938998550234,
            caption=types.PageCaption(text=types.TextEmpty(), credit=types.TextEmpty()),
            spoiler=False,
            url=None,
            webpage_id=None,
        ),
        types.PageBlockParagraph(text=_plain("Первый абзац.")),
        types.PageBlockParagraph(text=_plain("Второй абзац.")),
    )


def test_rich_text_flattens_every_nesting_shape():
    node = types.TextConcat(
        texts=[
            _plain("plain "),
            types.TextBold(text=_plain("bold ")),
            types.TextUrl(text=_plain("link"), url="https://example.com", webpage_id=0),
            types.TextCustomEmoji(document_id=1, alt="🧠"),
            types.TextEmpty(),
        ]
    )

    assert runtime.rich_text_to_str(node) == "plain bold link🧠"


def test_rich_text_ignores_nodes_that_carry_no_text():
    assert runtime.rich_text_to_str(types.TextEmpty()) == ""
    assert runtime.rich_text_to_str(types.TextImage(document_id=1, w=10, h=10)) == ""
    assert runtime.rich_text_to_str(None) == ""


def test_rich_message_text_walks_blocks():
    text = runtime.rich_message_text(_msg(rich_message=_post_like_agents_lab_224()))

    # One paragraph per block; the empty photo caption contributes nothing.
    assert text == "Личная CRM 🧠\n\nПервый абзац.\n\nВторой абзац."


def test_rich_message_text_reaches_captions_lists_tables_and_details():
    rich = _rich(
        types.PageBlockVideo(
            video_id=1,
            caption=types.PageCaption(text=_plain("под видео"), credit=types.TextEmpty()),
            autoplay=False,
            loop=False,
            spoiler=False,
        ),
        types.PageBlockList(
            items=[
                types.PageListItemText(text=_plain("первый"), checkbox=None, checked=None),
                types.PageListItemBlocks(
                    blocks=[types.PageBlockParagraph(text=_plain("второй"))],
                    checkbox=None,
                    checked=None,
                ),
            ]
        ),
        types.PageBlockTable(
            title=types.TextEmpty(),
            rows=[types.PageTableRow(cells=[_cell("A"), _cell("B")])],
            bordered=False,
            striped=False,
        ),
        types.PageBlockDetails(
            blocks=[types.PageBlockParagraph(text=_plain("скрытое"))],
            title=_plain("подробнее"),
            open=False,
        ),
    )

    assert runtime.rich_message_text(_msg(rich_message=rich)) == (
        "под видео\n\nпервый\nвторой\n\nA | B\n\nподробнее\nскрытое"
    )


def test_rich_message_text_skips_unknown_blocks_instead_of_failing():
    rich = _rich(
        SimpleNamespace(),  # a block type Telegram adds after this telethon build
        types.PageBlockParagraph(text=_plain("уцелевший абзац")),
    )

    assert runtime.rich_message_text(_msg(rich_message=rich)) == "уцелевший абзац"


def test_rich_message_text_is_empty_without_a_rich_message():
    assert runtime.rich_message_text(_msg()) == ""
    assert runtime.rich_message_text(SimpleNamespace()) == ""


def test_message_to_dict_falls_back_to_rich_message():
    d = messages.message_to_dict(_msg(rich_message=_post_like_agents_lab_224()))

    assert d["text"].startswith("Личная CRM 🧠")
    assert d["rich"] is True


def test_format_message_line_falls_back_to_rich_message():
    line = messages.format_message_line(_msg(rich_message=_post_like_agents_lab_224()))

    assert "Message: [empty]" not in line
    assert "Личная CRM 🧠\\n\\nПервый абзац." in line
    assert "rich" in line


def test_plain_message_text_still_wins_over_rich_message():
    msg = _msg(message="обычный текст", rich_message=_post_like_agents_lab_224())

    assert messages.message_to_dict(msg)["text"] == "обычный текст"
    assert "обычный текст" in messages.format_message_line(msg)
