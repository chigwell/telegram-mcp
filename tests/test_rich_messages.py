"""Tests for rich message sending (Telegram Premium gated)."""

import json
from datetime import datetime, timezone
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

    assert result == {"sent": True, "rich": True, "message_id": None}
    (req,) = cl.requests
    assert isinstance(req.rich_message, runtime.types.InputRichMessageMarkdown)
    assert req.reply_to.reply_to_msg_id == 5


@pytest.mark.asyncio
async def test_send_rich_names_the_sent_id():
    class _SentClient(_FakeClient):
        async def __call__(self, request):
            await super().__call__(request)
            return messages.types.UpdateShortSentMessage(
                id=77, pts=1, pts_count=1, date=datetime(2026, 10, 10, tzinfo=timezone.utc)
            )

    result = json.loads(await messages._send_rich(_SentClient(), "peer", "| a |", "rich"))

    assert result == {"sent": True, "rich": True, "message_id": 77}


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
    assert d["custom_emojis"] == [{"emoji": "🧠", "id": "5927026418616636353"}]


def test_format_message_line_falls_back_to_rich_message():
    line = messages.format_message_line(_msg(rich_message=_post_like_agents_lab_224()))

    assert "Message: [empty]" not in line
    assert "Личная CRM 🧠\\n\\nПервый абзац." in line
    assert "rich" in line
    assert 'custom_emojis: [{"emoji": "🧠", "id": "5927026418616636353"}]' in line


def test_plain_message_text_still_wins_over_rich_message():
    msg = _msg(message="обычный текст", rich_message=_post_like_agents_lab_224())

    assert messages.message_to_dict(msg)["text"] == "обычный текст"
    assert "обычный текст" in messages.format_message_line(msg)


# --- rich posts keep their links and embedded media reachable ---------------
# Regression for chigwell/telegram-mcp#370: a block-format post keeps text links
# as TextUrl/TextEmail/TextPhone nodes and PageBlockEmbed iframes inside
# rich_message, and its photos/videos as ids pointing into rich_message.photos /
# rich_message.documents. Reading only msg.entities and msg.media loses them all.


def _document(document_id, size=None):
    return types.Document(
        id=document_id,
        access_hash=1,
        file_reference=b"\x01",
        date=datetime(2026, 9, 3, tzinfo=timezone.utc),
        mime_type="video/mp4",
        size=size if size is not None else 1048576,
        dc_id=2,
        attributes=[types.DocumentAttributeVideo(duration=95, w=1280, h=720)],
    )


def _photo(photo_id):
    return types.Photo(
        id=photo_id,
        access_hash=1,
        file_reference=b"\x01",
        date=datetime(2026, 9, 3, tzinfo=timezone.utc),
        dc_id=2,
        sizes=[types.PhotoSize(type="x", w=1280, h=720, size=200000)],
    )


def _caption(text=""):
    return types.PageCaption(
        text=_plain(text) if text else types.TextEmpty(), credit=types.TextEmpty()
    )


def _post_like_issue_370():
    """A digest post: a paragraph with a text link, then an embedded video."""
    return _rich(
        types.PageBlockParagraph(
            text=types.TextConcat(
                texts=[
                    _plain("Read "),
                    types.TextUrl(
                        text=_plain("the report"), url="https://example.com/report", webpage_id=0
                    ),
                ]
            )
        ),
        types.PageBlockVideo(video_id=7001, caption=_caption("demo video")),
    )


def test_rich_link_urls_collect_texturl_email_phone_and_embed_targets():
    rich = _rich(
        types.PageBlockParagraph(
            text=types.TextConcat(
                texts=[
                    types.TextUrl(text=_plain("site"), url="https://a.example/x", webpage_id=0),
                    types.TextUrl(text=_plain("again"), url="https://a.example/x", webpage_id=0),
                    types.TextEmail(text=_plain("mail"), email="hi@a.example"),
                    types.TextPhone(text=_plain("call"), phone="+15550001111"),
                ]
            )
        ),
        # an iframe embed carries its src in .url, and the walk reaches blocks
        # nested inside collapsible details too
        types.PageBlockEmbed(caption=_caption(), url="https://youtube.com/watch?v=1"),
        types.PageBlockDetails(
            blocks=[
                types.PageBlockParagraph(
                    text=types.TextUrl(
                        text=_plain("hidden"), url="https://a.example/hidden", webpage_id=0
                    )
                )
            ],
            title=_plain("more"),
            open=False,
        ),
    )

    assert runtime.rich_message_link_urls(_msg(rich_message=rich)) == [
        "https://a.example/x",
        "hi@a.example",
        "+15550001111",
        "https://youtube.com/watch?v=1",
        "https://a.example/hidden",
    ]


def test_rich_link_urls_empty_without_targets():
    assert runtime.rich_message_link_urls(_msg()) == []
    assert runtime.rich_message_link_urls(_msg(rich_message=_rich(_cell("no links")))) == []


def test_link_urls_merges_entity_and_rich_urls_without_duplicates():
    msg = _msg(
        rich_message=_post_like_issue_370(),
        entities=[
            types.MessageEntityTextUrl(offset=0, length=4, url="https://example.com/report"),
            types.MessageEntityTextUrl(offset=5, length=4, url="https://example.com/entity"),
        ],
    )

    # the entity URL comes first, the rich TextUrl target is the same and is
    # reported once
    assert messages._link_urls(msg) == ["https://example.com/report", "https://example.com/entity"]


def test_rich_media_resolves_block_ids_against_photos_and_documents():
    doc = _document(7001)
    photo = _photo(5197)
    rich = types.RichMessage(
        blocks=[
            types.PageBlockVideo(video_id=7001, caption=_caption("demo")),
            types.PageBlockPhoto(photo_id=5197, caption=_caption(), spoiler=False),
            types.PageBlockVideo(video_id=9999, caption=_caption()),  # id not in the payload
        ],
        photos=[photo],
        documents=[doc],
    )

    entries = runtime.rich_message_media(_msg(rich_message=rich))

    assert [(e["type"], e["id"], e["media"]) for e in entries] == [
        ("video", 7001, doc),
        ("photo", 5197, photo),
        ("video", 9999, None),
    ]
    assert runtime.rich_message_media(_msg()) == []


def test_message_to_dict_reports_rich_links_and_media():
    d = messages.message_to_dict(_msg(rich_message=_post_like_issue_370()))

    assert d["link_urls"] == ["https://example.com/report"]
    assert d["rich_media"] == [{"type": "video", "id": 7001}]
    assert d["media"] == "video"  # the embedded video reads as media in listings


def test_format_message_line_flags_rich_media_when_no_attachment():
    line = messages.format_message_line(_msg(rich_message=_post_like_issue_370()))
    assert "📎 video" in line

    # the agents-lab post embeds an uncaptioned photo — now it reads as media
    with_photo = messages.format_message_line(_msg(rich_message=_post_like_agents_lab_224()))
    assert "📎 photo" in with_photo

    text_only = messages.format_message_line(
        _msg(rich_message=_rich(types.PageBlockParagraph(text=_plain("просто текст"))))
    )
    assert "📎" not in text_only


# --- media tools reach the embedded media -----------------------------------

import inspect  # noqa: E402
from pathlib import Path  # noqa: E402

from telegram_mcp.tools import media  # noqa: E402

_get_media_info = inspect.unwrap(media.get_media_info)
_download_media = inspect.unwrap(media.download_media)


def _video_post(document, video_id=7001):
    return _msg(
        media=None,
        rich_message=types.RichMessage(
            blocks=[types.PageBlockVideo(video_id=video_id, caption=_caption("demo video"))],
            photos=[],
            documents=[document],
        ),
    )


class _MediaClient:
    def __init__(self, message):
        self.message = message
        self.downloaded = []

    async def get_messages(self, entity, ids):
        return self.message

    async def download_media(self, media_obj, file, **kwargs):
        self.downloaded.append(media_obj)
        Path(f"{file}.mp4").write_bytes(b"data")
        return f"{file}.mp4"


def _patch_media_client(monkeypatch, client):
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        return "entity"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)


@pytest.mark.asyncio
async def test_get_media_info_reports_embedded_rich_video(monkeypatch):
    doc = _document(7001)
    monkeypatch.setattr(media, "get_client", lambda account=None: _MediaClient(_video_post(doc)))

    async def _resolve_entity(chat_id, cl):
        return "entity"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await _get_media_info(123, 225)

    assert "7001" in result
    assert "video/mp4" in result


@pytest.mark.asyncio
async def test_get_media_info_still_reports_no_media_without_rich_media(monkeypatch):
    monkeypatch.setattr(
        media,
        "get_client",
        lambda account=None: _MediaClient(
            _msg(media=None, rich_message=_post_like_agents_lab_224())
        ),
    )

    async def _resolve_entity(chat_id, cl):
        return "entity"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    result = await _get_media_info(123, 224)

    assert "No media found" in result


@pytest.mark.asyncio
async def test_download_media_fetches_embedded_rich_video(tmp_path, monkeypatch):
    doc = _document(7001)
    client = _MediaClient(_video_post(doc))
    _patch_media_client(monkeypatch, client)

    async def _resolve_path(**kwargs):
        return tmp_path / kwargs["default_filename"], None

    monkeypatch.setattr(media, "_resolve_writable_file_path", _resolve_path)

    result = await _download_media(123, 225)

    assert client.downloaded == [doc]  # the rich document, not the bare message
    assert result.startswith("Media downloaded to")


@pytest.mark.asyncio
async def test_download_media_media_id_picks_among_rich_blocks(tmp_path, monkeypatch):
    doc = _document(7001)
    other = _document(7002)
    msg = _msg(
        media=None,
        rich_message=types.RichMessage(
            blocks=[
                types.PageBlockVideo(video_id=7001, caption=_caption("first")),
                types.PageBlockVideo(video_id=7002, caption=_caption("second")),
            ],
            photos=[],
            documents=[doc, other],
        ),
    )
    client = _MediaClient(msg)
    _patch_media_client(monkeypatch, client)

    async def _resolve_path(**kwargs):
        return tmp_path / kwargs["default_filename"], None

    monkeypatch.setattr(media, "_resolve_writable_file_path", _resolve_path)

    await _download_media(123, 225, media_id=7002)
    assert [d.id for d in client.downloaded] == [7002]

    result = await _download_media(123, 225, media_id=9999)
    assert "No rich media with id 9999" in result
    assert [d.id for d in client.downloaded] == [7002]


@pytest.mark.asyncio
async def test_download_media_enforces_size_limit_on_rich_document(tmp_path, monkeypatch):
    doc = _document(7001, size=10)
    client = _MediaClient(_video_post(doc))
    _patch_media_client(monkeypatch, client)
    monkeypatch.setitem(media.MAX_FILE_BYTES, "download_media", 4)

    async def _fail_download(media_obj, file, **kwargs):
        raise AssertionError("download must not start")

    client.download_media = _fail_download

    result = await _download_media(123, 225)

    assert "too large" in result.lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_download_media_rejects_media_id_for_regular_media(monkeypatch):
    client = _MediaClient(SimpleNamespace(id=9, media=object()))
    _patch_media_client(monkeypatch, client)

    result = await _download_media(123, 9, media_id=7001)

    assert "media_id picks among a rich post's embedded media" in result
    assert client.downloaded == []
