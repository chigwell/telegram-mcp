"""format_date on send_file and send_album captions, without Telegram connections.

parse_mode and custom_emojis on captions are covered in test_send_file_caption_format.
"""

import pytest
from telethon.tl import types

from telegram_mcp import runtime
from telegram_mcp.tools import media

WINE_ID = 5368324170671202286
WINE = {"emoji": "🍷", "id": str(WINE_ID)}


def _utf16(text):
    return len(text.encode("utf-16-le")) // 2


class _RecordingClient:
    def __init__(self):
        self.calls = []

    async def send_file(self, entity, file, **kwargs):
        self.calls.append({"entity": entity, "file": file, **kwargs})


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    for name in ("one.png", "two.png"):
        (root / name).write_bytes(b"png")
    cl = _RecordingClient()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": cl})
    monkeypatch.setattr(media, "get_client", lambda account=None: cl)

    async def _resolve_entity(chat_id, _cl):
        return f"entity:{chat_id}"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    return cl


# Keyword arguments, as MCP passes them: validate_id only checks keyword chat_id.
async def _send_file(**kw):
    return await media.send_file(chat_id=42, file_path="one.png", **kw)


async def _send_file_group(**kw):
    return await media.send_file(chat_id=42, file_path=["one.png", "two.png"], **kw)


async def _send_album(**kw):
    return await media.send_album(chat_id=42, file_paths=["one.png", "two.png"], **kw)


TOOLS = {"send_file": _send_file, "send_file_group": _send_file_group, "send_album": _send_album}


@pytest.fixture(params=list(TOOLS))
def send(request):
    return TOOLS[request.param]


@pytest.mark.asyncio
async def test_format_date_becomes_a_caption_entity(client, send):
    caption = "Tasting 13/09 17:00"
    await send(caption=caption, format_date="13/09 17:00")

    call = client.calls[0]
    assert call["caption"] == caption
    assert "parse_mode" not in call
    [chip] = call["formatting_entities"]
    assert isinstance(chip, types.MessageEntityFormattedDate)
    assert (chip.offset, chip.length) == (_utf16("Tasting "), _utf16("13/09 17:00"))


@pytest.mark.asyncio
async def test_format_date_combines_with_custom_emojis(client, send):
    caption = "🍷 tasting 13/09 🍷"
    await send(caption=caption, format_date="13/09", custom_emojis=[WINE])

    entities = client.calls[0]["formatting_entities"]
    assert [(type(e), e.offset) for e in entities] == [
        (types.MessageEntityCustomEmoji, 0),
        (types.MessageEntityFormattedDate, _utf16("🍷 tasting ")),
        (types.MessageEntityCustomEmoji, _utf16("🍷 tasting 13/09 ")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (
            {"caption": "13/09", "format_date": "13/09", "parse_mode": "html"},
            "format_date needs plain-text messages (leave parse_mode unset).",
        ),
        (
            {"caption": "Tasting", "format_date": "13/09"},
            "format_date '13/09' was not found in the message text.",
        ),
        ({"format_date": "13/09"}, "format_date needs a caption."),
        ({"caption": "", "format_date": "13/09"}, "format_date needs a caption."),
    ],
)
async def test_invalid_format_date_sends_nothing(client, send, kwargs, expected):
    assert await send(**kwargs) == expected
    assert client.calls == []
