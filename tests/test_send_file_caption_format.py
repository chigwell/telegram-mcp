"""parse_mode and custom_emojis on send_file and send_album captions, without Telegram."""

from datetime import datetime, timedelta, timezone

import pytest
from telethon.tl import types

from telegram_mcp import runtime
from telegram_mcp.tools import media

WINE_ID = 5368324170671202286
COFFEE_ID = 5368324170671202287
WINE = {"emoji": "🍷", "id": str(WINE_ID)}
CONFLICT = "custom_emojis needs plain-text messages (leave parse_mode unset)."
NOT_FOUND = "was not found as a standalone emoji"


class _RecordingClient:
    def __init__(self):
        self.calls = []

    async def send_file(self, entity, file, **kwargs):
        self.calls.append({"entity": entity, "file": file, **kwargs})

    async def upload_file(self, path):
        return f"handle:{path}"


@pytest.fixture
def client_and_root(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    for name in ("shot.png", "one.png", "two.png"):
        (root / name).write_bytes(b"png")

    client = _RecordingClient()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": client})
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        return f"entity:{chat_id}"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    return client


# Keyword arguments, as MCP passes them: validate_id only checks keyword chat_id.
async def _send_single(**kw):
    return await media.send_file(chat_id=42, file_path="shot.png", **kw)


async def _send_group_via_send_file(**kw):
    return await media.send_file(chat_id=42, file_path=["one.png", "two.png"], **kw)


async def _send_group_via_send_album(**kw):
    return await media.send_album(chat_id=42, file_paths=["one.png", "two.png"], **kw)


TOOLS = {
    "send_file": _send_single,
    "send_file_group": _send_group_via_send_file,
    "send_album": _send_group_via_send_album,
}


@pytest.fixture(params=list(TOOLS))
def send(request):
    return TOOLS[request.param]


def _spans(entities):
    return [(e.offset, e.length, getattr(e, "document_id", None)) for e in entities]


@pytest.mark.asyncio
async def test_caption_format_is_not_passed_by_default(client_and_root, send):
    # Without parse_mode and custom_emojis Telethon still parses the caption with
    # its default (Markdown), exactly as before.
    await send(caption="**cap** 🍷")

    assert "parse_mode" not in client_and_root.calls[0]
    assert "formatting_entities" not in client_and_root.calls[0]
    assert client_and_root.calls[0]["caption"] == "**cap** 🍷"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["html", "md", "markdown", "HTML"])
async def test_parse_mode_is_forwarded(client_and_root, send, mode):
    caption = '<b>News</b> <tg-emoji emoji-id="5368324170671202286">🍷</tg-emoji>'

    result = await send(caption=caption, parse_mode=mode)

    assert "sent to chat 42" in result
    assert client_and_root.calls[0]["parse_mode"] == mode
    assert client_and_root.calls[0]["caption"] == caption
    assert "formatting_entities" not in client_and_root.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["plain", "PLAIN"])
async def test_plain_disables_parsing(client_and_root, send, mode):
    result = await send(caption="2 * 3 = 6, 4 * 5 = 20", parse_mode=mode)

    assert "sent to chat 42" in result
    assert "parse_mode" in client_and_root.calls[0]
    assert client_and_root.calls[0]["parse_mode"] is None


@pytest.mark.asyncio
async def test_custom_emojis_are_sent_as_entities_over_the_raw_caption(client_and_root, send):
    caption = "🍷 List<int> && &copy; **x** ☕ 🍷"
    coffee = {"emoji": "☕", "id": COFFEE_ID}

    result = await send(caption=caption, custom_emojis=[WINE, coffee])

    assert "sent to chat 42" in result
    call = client_and_root.calls[0]
    assert call["caption"] == caption
    assert all(isinstance(e, types.MessageEntityCustomEmoji) for e in call["formatting_entities"])
    assert _spans(call["formatting_entities"]) == [
        (0, 2, WINE_ID),
        (len("🍷 List<int> && &copy; **x** ".encode("utf-16-le")) // 2, 1, COFFEE_ID),
        (len("🍷 List<int> && &copy; **x** ☕ ".encode("utf-16-le")) // 2, 2, WINE_ID),
    ]
    assert "parse_mode" not in call


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["html", "md", "plain", "rich", "rich_html"])
async def test_custom_emojis_refuse_parse_mode(client_and_root, send, mode):
    result = await send(caption="🍷", custom_emojis=[WINE], parse_mode=mode)

    assert result == CONFLICT
    assert client_and_root.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rich", "rich_md", "rich_markdown", "RICH_HTML"])
async def test_rich_modes_are_refused_for_captions(client_and_root, send, mode):
    result = await send(caption="# title", parse_mode=mode)

    assert result == (
        f"parse_mode='{mode}' is not supported for file captions. Use 'md', 'html' or 'plain'."
    )
    assert client_and_root.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caption, custom_emojis, expected",
    [
        (None, [WINE], "custom_emojis needs a caption."),
        ("", [WINE], "custom_emojis needs a caption."),
        ("no emoji here", [WINE], NOT_FOUND),
        ("🍷", [{"emoji": "🍷"}], "Each custom_emojis entry must be"),
        ("🍷", [{"emoji": "🍷", "id": 0}], "must be a positive 64-bit integer"),
        ("🍷", WINE, "custom_emojis must be a list of"),
    ],
)
async def test_invalid_custom_emojis_send_nothing(
    client_and_root, send, caption, custom_emojis, expected
):
    result = await send(caption=caption, custom_emojis=custom_emojis)

    assert expected in result
    assert client_and_root.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("send_kind", ["send_file", "send_file_group"])
async def test_caption_format_works_with_force_document_topic_and_schedule(
    client_and_root, send_kind
):
    when = datetime.now(timezone.utc) + timedelta(hours=1)

    result = await TOOLS[send_kind](
        caption="🍷 news",
        custom_emojis=[WINE],
        topic_id=5,
        schedule_date=when.isoformat(),
        force_document=True,
    )

    assert "scheduled for" in result
    call = client_and_root.calls[0]
    assert _spans(call["formatting_entities"]) == [(0, 2, WINE_ID)]
    assert call["reply_to"] == 5
    assert call["schedule"] == when
    assert call["force_document"] is True


@pytest.mark.asyncio
async def test_album_caption_format_works_with_topic_and_schedule(client_and_root):
    when = datetime.now(timezone.utc) + timedelta(hours=1)

    result = await media.send_album(
        chat_id=42,
        file_paths=["one.png", "two.png"],
        caption="<i>soon</i>",
        parse_mode="html",
        topic_id=9,
        schedule_date=when.isoformat(),
    )

    assert result.startswith("Album of 2 files scheduled for ")
    call = client_and_root.calls[0]
    assert call["parse_mode"] == "html"
    assert call["reply_to"] == 9
    assert call["schedule"] == when


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "caption_args, expected",
    [
        ({"caption": "<b>clip</b>", "parse_mode": "html"}, {"parse_mode": "html"}),
        (
            {"caption": "clip 🍷", "custom_emojis": [WINE]},
            {"formatting_entities": [(5, 2, WINE_ID)]},
        ),
    ],
)
async def test_video_album_keeps_caption_format(
    client_and_root, monkeypatch, caption_args, expected
):
    # An album with a video goes out as per-file InputMedia instead of paths;
    # the caption format must reach Telethon on that path too.
    (runtime.SERVER_ALLOWED_ROOTS[0] / "clip.mp4").write_bytes(b"mp4")
    video = types.DocumentAttributeVideo(duration=3, w=1080, h=1920)
    monkeypatch.setattr(
        media, "_video_send_attributes", lambda path: video if path.endswith(".mp4") else None
    )

    result = await media.send_album(chat_id=42, file_paths=["clip.mp4", "one.png"], **caption_args)

    assert "sent to chat 42" in result
    call = client_and_root.calls[0]
    assert all(not isinstance(item, str) for item in call["file"])
    assert call["caption"] == caption_args["caption"]
    if "parse_mode" in expected:
        assert call["parse_mode"] == expected["parse_mode"]
    else:
        assert _spans(call["formatting_entities"]) == expected["formatting_entities"]


@pytest.mark.asyncio
async def test_failure_does_not_expose_exception_text(client_and_root, send):
    async def _fail(entity, file, **kwargs):
        raise RuntimeError("caption-secret")

    client_and_root.send_file = _fail

    result = await send(caption="<b>cap</b>", parse_mode="html")

    assert result.startswith("An error occurred (code: ")
    assert "caption-secret" not in result
