"""Tests for reading Telegram Rich Messages and for streaming rich drafts.

The gap these pin: a rich message keeps its content in ``rich_message`` page
blocks, and ``Message.message`` is empty. Before this, every reading tool
reported such a message as an empty line - the failure is silent, which is why
``test_rich_message_without_plain_text_is_not_empty`` asserts on the text a
reader actually gets rather than on the renderer alone.
"""

import json
from types import SimpleNamespace

import pytest
from telethon.tl import types

from telegram_mcp import rich_messages
from telegram_mcp.tools import messages


def _plain(text):
    return types.TextPlain(text=text)


def _rich(blocks, **kwargs):
    return types.RichMessage(blocks=blocks, photos=[], documents=[], **kwargs)


# --- rich text ---------------------------------------------------------------


def test_rich_text_nesting_and_links():
    node = types.TextConcat(
        texts=[
            _plain("see "),
            types.TextBold(
                text=types.TextUrl(text=_plain("the docs"), url="https://x.dev", webpage_id=0)
            ),
            _plain(" now"),
        ]
    )
    assert rich_messages.rich_text_to_markdown(node) == "see **[the docs](https://x.dev)** now"


def test_rich_text_empty_and_unknown_degrade_quietly():
    assert rich_messages.rich_text_to_markdown(types.TextEmpty()) == ""
    assert rich_messages.rich_text_to_markdown(None) == ""
    # An anchor contributes its inner text, not its decoration.
    anchor = types.TextAnchor(text=_plain("jump"), name="s1")
    assert rich_messages.rich_text_to_markdown(anchor) == "jump"


# --- blocks ------------------------------------------------------------------


def test_headings_lists_and_code():
    blocks = [
        types.PageBlockHeader(text=_plain("Report")),
        types.PageBlockParagraph(text=_plain("Body text.")),
        types.PageBlockOrderedList(
            items=[
                types.PageListOrderedItemText(num="1", text=_plain("first")),
                types.PageListOrderedItemText(num="2", text=_plain("second")),
            ]
        ),
        types.PageBlockPreformatted(text=_plain("print(1)"), language="python"),
        types.PageBlockDivider(),
    ]
    out = rich_messages.rich_message_to_markdown(_rich(blocks))
    assert "# Report" in out
    assert "Body text." in out
    assert "1. first" in out and "2. second" in out
    assert "```python\nprint(1)\n```" in out
    assert "---" in out


def test_table_uses_the_header_row():
    rows = [
        types.PageTableRow(
            cells=[
                types.PageTableCell(header=True, text=_plain("name")),
                types.PageTableCell(header=True, text=_plain("value")),
            ]
        ),
        types.PageTableRow(
            cells=[
                types.PageTableCell(text=_plain("alpha")),
                types.PageTableCell(text=_plain("1")),
            ]
        ),
    ]
    block = types.PageBlockTable(title=_plain("Numbers"), rows=rows)
    out = rich_messages.page_block_to_markdown(block)
    assert "| name | value |" in out
    assert "| alpha | 1 |" in out
    assert out.index("| name | value |") < out.index("| alpha | 1 |")


def test_table_without_header_keeps_every_data_row():
    """A header-less table must not lose its first row to the header slot."""
    rows = [
        types.PageTableRow(cells=[types.PageTableCell(text=_plain("a"))]),
        types.PageTableRow(cells=[types.PageTableCell(text=_plain("b"))]),
    ]
    out = rich_messages.page_block_to_markdown(
        types.PageBlockTable(title=types.TextEmpty(), rows=rows)
    )
    assert "| a |" in out and "| b |" in out


def test_collapsible_section_keeps_its_state():
    block = types.PageBlockDetails(
        blocks=[types.PageBlockParagraph(text=_plain("hidden detail"))],
        title=_plain("More"),
        open=False,
    )
    out = rich_messages.page_block_to_markdown(block)
    assert "**More** (collapsed)" in out
    assert "hidden detail" in out


def test_unknown_block_is_labelled_not_dropped():
    class PageBlockSomethingNew:
        text = None

    assert rich_messages.page_block_to_markdown(PageBlockSomethingNew()) == "[somethingnew]"


# --- the read path -----------------------------------------------------------


def _message(rich=None, text=None):
    return SimpleNamespace(
        id=7,
        message=text,
        date=None,
        media=None,
        rich_message=rich,
        sender=None,
        sender_id=None,
        reply_to=None,
        out=False,
    )


def test_rich_message_without_plain_text_is_not_empty():
    """The actual defect: such a message used to read as an empty line."""
    rich = _rich(
        [
            types.PageBlockHeader(text=_plain("Q3")),
            types.PageBlockParagraph(text=_plain("revenue up")),
        ]
    )
    d = messages.message_to_dict(_message(rich=rich))
    assert "# Q3" in d["text"]
    assert "revenue up" in d["text"]
    assert d["rich_message"] == {}  # no photos, documents or flags to report


def test_rich_markdown_is_not_duplicated_when_plain_text_exists():
    rich = _rich([types.PageBlockParagraph(text=_plain("rich body"))])
    d = messages.message_to_dict(_message(rich=rich, text="fallback text"))
    assert d["text"] == "fallback text"
    assert d["rich_message"]["markdown"] == "rich body"


def test_partial_rich_message_is_flagged():
    rich = _rich([types.PageBlockParagraph(text=_plain("half a th"))], part=True)
    d = messages.message_to_dict(_message(rich=rich))
    assert d["rich_message"]["partial"] is True


def test_plain_message_gains_no_rich_key():
    d = messages.message_to_dict(_message(text="hello"))
    assert "rich_message" not in d


# --- streaming drafts --------------------------------------------------------


class _FakeClient:
    def __init__(self, premium=True):
        self._premium = premium
        self.requests = []

    async def get_me(self):
        return SimpleNamespace(premium=self._premium)

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace()


@pytest.mark.asyncio
async def test_rich_draft_is_a_typing_action_not_a_message():
    cl = _FakeClient()
    result = json.loads(await messages._send_rich_draft(cl, "peer", "# partial", "rich_md", None))

    assert result["draft"] is True
    (req,) = cl.requests
    assert isinstance(req, messages.functions.messages.SetTypingRequest)
    assert isinstance(req.action, types.InputSendMessageRichMessageDraftAction)
    assert isinstance(req.action.rich_message, types.InputRichMessageMarkdown)


@pytest.mark.asyncio
async def test_rich_draft_id_is_reused_so_updates_replace_each_other():
    cl = _FakeClient()
    first = json.loads(await messages._send_rich_draft(cl, "peer", "one", "rich_md", None))
    second = json.loads(
        await messages._send_rich_draft(cl, "peer", "one two", "rich_md", first["draft_id"])
    )

    assert second["draft_id"] == first["draft_id"]
    assert [r.action.random_id for r in cl.requests] == [first["draft_id"]] * 2


@pytest.mark.asyncio
async def test_rich_draft_without_premium_sends_nothing():
    cl = _FakeClient(premium=False)
    result = json.loads(await messages._send_rich_draft(cl, "peer", "x", "rich_md", None))

    assert result["sent"] is False
    assert result["reason"] == "telegram_premium_required"
    assert cl.requests == []
