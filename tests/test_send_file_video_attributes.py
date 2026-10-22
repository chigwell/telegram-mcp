import json
import subprocess

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import media


class _RecordingClient:
    def __init__(self):
        self.calls = []

    async def send_file(self, entity, file, **kwargs):
        self.calls.append({"file": file, **kwargs})


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    for name in ("clip.mp4", "clip.mkv", "shot.png"):
        (root / name).write_bytes(b"data")

    recording_client = _RecordingClient()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": recording_client})
    monkeypatch.setattr(media, "get_client", lambda account=None: recording_client)

    async def _resolve_entity(chat_id, cl):
        return f"entity:{chat_id}"

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)
    return recording_client


def _fake_ffmpeg(monkeypatch, stream, format_info=None):
    def run(command, **kwargs):
        if command[0] == "ffprobe":
            output = json.dumps({"streams": [stream], "format": format_info or {}})
            return subprocess.CompletedProcess(command, 0, stdout=output)
        return subprocess.CompletedProcess(command, 0, stdout=b"jpeg")

    monkeypatch.setattr(media.subprocess, "run", run)


@pytest.mark.asyncio
async def test_video_is_sent_with_its_dimensions_duration_and_thumbnail(client, monkeypatch):
    _fake_ffmpeg(monkeypatch, {"width": 1080, "height": 1920, "duration": "2.6"})

    await media.send_file("Chat", "clip.mp4")

    (video,) = client.calls[0]["attributes"]
    assert (video.w, video.h, video.duration, video.supports_streaming) == (1080, 1920, 2.6, True)
    assert client.calls[0]["thumb"] == b"jpeg"


@pytest.mark.asyncio
async def test_rotated_phone_video_is_sent_with_its_displayed_orientation(client, monkeypatch):
    stream = {"width": 1920, "height": 1080, "side_data_list": [{"rotation": -90}]}
    _fake_ffmpeg(monkeypatch, stream, {"duration": "4.0"})

    await media.send_file("Chat", "clip.mp4")

    (video,) = client.calls[0]["attributes"]
    assert (video.w, video.h, video.duration) == (1080, 1920, 4.0)


@pytest.mark.asyncio
async def test_video_without_ffmpeg_is_sent_as_before(client, tmp_path, monkeypatch):
    def run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(media.subprocess, "run", run)

    result = await media.send_file("Chat", "clip.mp4")

    assert result == f"File sent to chat Chat from {tmp_path.resolve() / 'root' / 'clip.mp4'}."
    assert "attributes" not in client.calls[0]
    assert "thumb" not in client.calls[0]


@pytest.mark.asyncio
async def test_force_document_and_non_inline_containers_are_not_probed(client, monkeypatch):
    probed = []
    monkeypatch.setattr(media.subprocess, "run", lambda command, **kwargs: probed.append(command))

    await media.send_file("Chat", "clip.mp4", force_document=True)
    await media.send_file("Chat", "clip.mkv")
    await media.send_file("Chat", "shot.png")

    assert probed == []
    assert all("attributes" not in call for call in client.calls)


def test_unreadable_ffprobe_output_is_ignored(tmp_path, monkeypatch):
    _fake_ffmpeg(monkeypatch, {"width": 1080, "height": 1920, "tags": {"rotate": "sideways"}})

    assert media._probe_video(tmp_path / "clip.mp4") is None
