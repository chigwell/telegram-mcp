import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import media


class _DummyClient:
    def __init__(self):
        self.sent = None

    async def send_file(self, entity, file_paths, caption=None):
        self.sent = {
            "entity": entity,
            "file_paths": file_paths,
            "caption": caption,
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["send_album", "send_file"])
async def test_album_mode_sends_multiple_files_as_one_media_group(
    tmp_path, monkeypatch, tool_name
):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    first = root / "one.png"
    second = root / "two.png"
    first.write_bytes(b"png-one")
    second.write_bytes(b"png-two")

    client = _DummyClient()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": client})
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        assert chat_id == "AgenticAIChat"
        assert cl is client
        return "entity:AgenticAIChat"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)

    tool = getattr(media, tool_name)
    result = await tool(
        "AgenticAIChat",
        ["one.png", str(second)],
        caption="pick one",
    )

    assert result == "Album sent to chat AgenticAIChat with 2 files."
    assert client.sent == {
        "entity": "entity:AgenticAIChat",
        "file_paths": [str(first), str(second)],
        "caption": "pick one",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file_paths", "expected"),
    [
        ("not-a-list", "file_paths must be a list of file paths."),
        (["one.png"], "Albums must contain between 2 and 10 files."),
        ([f"{index}.png" for index in range(11)], "Albums must contain between 2 and 10 files."),
    ],
)
async def test_send_album_validates_album_file_count(file_paths, expected, monkeypatch):
    monkeypatch.setattr(media, "clients", {"default": _DummyClient()})

    result = await media.send_album("AgenticAIChat", file_paths)

    assert result == expected


@pytest.mark.asyncio
async def test_send_album_reuses_readable_path_security(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    outside = (tmp_path / "outside").resolve()
    root.mkdir()
    outside.mkdir()
    (root / "one.png").write_bytes(b"png-one")
    outside_file = outside / "two.png"
    outside_file.write_bytes(b"png-two")

    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": _DummyClient()})

    result = await media.send_album("AgenticAIChat", ["one.png", str(outside_file)])

    assert result == "Path is outside allowed roots."
