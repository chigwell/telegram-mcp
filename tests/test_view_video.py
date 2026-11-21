"""view_video: frame picking, the timeline, the tool's refusals and zoom.

ffmpeg itself is faked in the tool tests; the helpers run against real ffmpeg only
when it is installed (TestRealFfmpeg).
"""

import asyncio
import io
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import Image
from PIL import Image as PillowImage

from telegram_mcp import runtime, transcription
from telegram_mcp import video as video_tools
from telegram_mcp.tools import media


def _jpeg(colour):
    out = io.BytesIO()
    PillowImage.new("RGB", (64, 48), colour).save(out, format="JPEG")
    return out.getvalue()


# --- helpers -------------------------------------------------------------------------


def test_pick_times_spreads_evenly_without_cuts():
    times = video_tools.pick_times(0, 80, [], 8)
    assert len(times) == 8
    assert times == sorted(times)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert min(gaps) >= 80 / 16


def test_pick_times_prefers_cuts_and_keeps_the_opening():
    assert video_tools.pick_times(0, 12, [4.0, 8.0], 3) == [0.5, 4.0, 8.0]


def test_pick_times_never_picks_the_same_moment_twice():
    times = video_tools.pick_times(0, 10, [5.0, 5.1, 5.2], 4)
    near_five = [t for t in times if 4.9 <= t <= 5.3]
    assert len(near_five) == 1


def test_pick_times_honours_the_window_and_one_frame():
    assert video_tools.pick_times(30, 40, [12.0, 35.0, 55.0], 2) == [30.5, 35.0]
    assert video_tools.pick_times(0, 60, [10.0], 1) == [0.5]


def test_scene_times_are_shifted_back_to_file_time():
    stderr = "[Parsed_showinfo_3] n:0 pts:4 pts_time:1 ...\n... pts_time:5.04 pos:..."
    assert video_tools.parse_scene_times(stderr, 3.0) == [4.0, 8.04]


def test_parse_probe():
    info = video_tools.parse_probe(
        {
            "streams": [
                {"codec_type": "video", "width": 640, "height": 640, "duration": "43.1"},
                {"codec_type": "audio", "duration": "43.14"},
            ],
            "format": {"duration": "43.139"},
        }
    )
    assert (info.duration, info.width, info.height) == (43.139, 640, 640)
    assert info.has_video and info.has_audio
    silent = video_tools.parse_probe({"streams": [{"codec_type": "video", "duration": "2.5"}]})
    assert silent.duration == 2.5 and not silent.has_audio


def test_clock():
    assert [video_tools.clock(s) for s in (0, 9.9, 75, 3725)] == [
        "0:00",
        "0:09",
        "1:15",
        "1:02:05",
    ]


def test_timeline_merges_frames_and_speech_in_time_order():
    lines = video_tools.timeline(
        [0.5, 4.0],
        [{"start": 0.0, "end": 3.2, "text": "hello"}, {"start": 4.0, "end": 6.0, "text": "two"}],
    )
    assert lines == ["0:00-0:03 hello", "0:00 [frame 1]", "0:04 [frame 2]", "0:04-0:06 two"]


def test_drop_duplicates_keeps_changes_only():
    frames = [(0.5, _jpeg((200, 0, 0))), (2.0, _jpeg((200, 0, 0))), (4.0, _jpeg((0, 0, 200)))]
    assert [at for at, _ in video_tools.drop_duplicates(frames)] == [0.5, 4.0]


def test_missing_ffmpeg_is_reported(monkeypatch):
    monkeypatch.setattr(video_tools.shutil, "which", lambda name: None)
    with pytest.raises(video_tools.VideoToolUnavailable, match="ffmpeg"):
        video_tools.binaries()


# --- the tool --------------------------------------------------------------------------


def _video_msg(**overrides):
    base = dict(
        id=77,
        video=object(),
        video_note=None,
        gif=None,
        document=object(),
        file=SimpleNamespace(ext=".mp4", mime_type="video/mp4", size=1000, duration=20),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def tool(monkeypatch, tmp_path):
    """Fake client and ffmpeg; records what each step was asked for."""
    state = SimpleNamespace(msg=_video_msg(), calls=[], downloads=0, speech_delay=0.0)

    class _Client:
        async def get_messages(self, entity, ids=None):
            return state.msg

        async def download_media(self, msg, file=None):
            state.downloads += 1
            with open(file, "wb") as out:
                out.write(b"video")
            return file

    client = _Client()
    monkeypatch.setattr(media, "get_client", lambda account=None: client)
    monkeypatch.setattr(media, "clients", {"default": client})

    async def _resolve(chat_id, cl):
        return object()

    monkeypatch.setattr(media, "resolve_entity", _resolve)
    monkeypatch.setattr(media, "get_marked_id", lambda entity: -100123)
    monkeypatch.setattr(media, "get_allowed_topics", lambda chat_id, entity: None)
    monkeypatch.setattr(video_tools, "binaries", lambda: ("ffmpeg", "ffprobe"))

    async def _probe(path, timeout=None):
        return video_tools.VideoInfo(20.0, 640, 360, True, True)

    async def _scenes(path, start, end, timeout):
        state.calls.append(("scenes", start, end))
        return [t for t in (4.0, 12.0) if start < t < end]

    async def _frame(path, at, timeout, not_before=0.0):
        state.calls.append(("frame", at))
        return at, _jpeg((int(at * 10) % 256, 100, 255 - int(at * 10) % 256))

    async def _audio(path, start, end, timeout, max_bytes=None):
        state.calls.append(("audio", start, end))
        return b"fLaC"

    async def _transcribe(data, engine, **kwargs):
        state.calls.append(("transcribe", engine))
        await asyncio.sleep(state.speech_delay)
        return {
            "status": "ok",
            "text": "one two",
            "segments": [
                {"start": 0.2, "end": 2.0, "text": "one"},
                {"start": 3.0, "end": 4.5, "text": "two"},
            ],
        }

    monkeypatch.setattr(video_tools, "probe", _probe)
    monkeypatch.setattr(video_tools, "scene_times", _scenes)
    monkeypatch.setattr(video_tools, "grab_frame", _frame)
    monkeypatch.setattr(video_tools, "extract_audio", _audio)
    monkeypatch.setattr(transcription, "transcribe_audio", _transcribe)
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE", "on-demand")
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_ENGINE", "openai")
    return state


def _split(result):
    assert isinstance(result, list)
    text, rest = result[0], result[1:]
    labels, images = rest[0::2], rest[1::2]
    assert all(isinstance(image, Image) for image in images)
    return text, labels, images


@pytest.mark.asyncio
async def test_view_video_returns_labelled_frames_and_a_timeline(tool):
    text, labels, images = _split(await media.view_video(-100123, 77))
    assert len(images) == len(labels) >= 3
    assert labels[0] == "frame 1 at 0:00"
    assert "Video 77: 0:20 long, 640x360. Looking at 0:00-0:20." in text
    assert "Timeline:" in text and "0:04 [frame" in text
    assert "0:00-0:02 one" in text and "0:03-0:04 two" in text
    assert "at picture changes" in text
    assert ("audio", 0.0, 20.0) in tool.calls


@pytest.mark.asyncio
async def test_view_video_zoom_shifts_speech_onto_the_video_clock(tool):
    text, labels, images = _split(await media.view_video(-100123, 77, start=10, end=16))
    assert ("scenes", 10.0, 16.0) in tool.calls and ("audio", 10.0, 16.0) in tool.calls
    assert all(10 <= at <= 16 for kind, *rest in tool.calls if kind == "frame" for at in rest)
    assert "Looking at 0:10-0:16" in text
    assert "0:10-0:12 one" in text and "0:13-0:14 two" in text


@pytest.mark.asyncio
async def test_view_video_without_transcript(tool):
    text, _, images = _split(await media.view_video(-100123, 77, transcribe=False, max_frames=2))
    assert "Speech: not requested." in text
    assert len(images) == 2
    assert not [c for c in tool.calls if c[0] in ("audio", "transcribe")]


@pytest.mark.asyncio
async def test_view_video_caps_frames(tool):
    _, _, images = _split(await media.view_video(-100123, 77, max_frames=500))
    assert len(images) <= video_tools.MAX_FRAMES


@pytest.mark.asyncio
async def test_view_video_slow_speech_still_returns_frames(tool, monkeypatch):
    tool.speech_delay = 30
    monkeypatch.setattr(media, "VIDEO_TIME_MARGIN_SECONDS", 0)
    monkeypatch.setattr(runtime, "_tool_timeout_seconds", lambda value=None: 3.0)
    text, _, images = _split(await media.view_video(-100123, 77))
    assert images
    assert "did not finish within the tool time limit" in text


@pytest.mark.asyncio
async def test_view_video_telegram_engine_only_for_whole_video_notes(tool, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TRANSCRIBE_ENGINE", "telegram")
    text, _, _ = _split(await media.view_video(-100123, 77))
    assert "transcribes only whole video notes" in text
    assert not [c for c in tool.calls if c[0] == "transcribe"]


@pytest.mark.asyncio
async def test_view_video_refusals(tool, monkeypatch):
    tool.msg = _video_msg(video=None, gif=None, file=SimpleNamespace(mime_type="image/jpeg"))
    assert "has no video" in await media.view_video(-100123, 77)

    tool.msg = _video_msg(file=SimpleNamespace(ext=".mp4", mime_type="video/mp4", size=10**10))
    refused = await media.view_video(-100123, 77)
    assert "TELEGRAM_VIDEO_MAX_MB" in refused and tool.downloads == 0

    tool.msg = _video_msg()
    assert "Empty window" in await media.view_video(-100123, 77, start=15, end=15)

    monkeypatch.setattr(media, "get_allowed_topics", lambda chat_id, entity: frozenset({5}))
    monkeypatch.setattr(media, "in_allowed_topics", lambda msg, topics: False)
    assert "only forum topic(s) 5" in await media.view_video(-100123, 77)
    assert tool.downloads == 1  # only the empty-window call downloaded


@pytest.mark.asyncio
async def test_view_video_without_ffmpeg(tool, monkeypatch):
    def _missing():
        raise video_tools.VideoToolUnavailable("view_video needs ffmpeg")

    monkeypatch.setattr(video_tools, "binaries", _missing)
    assert "needs ffmpeg" in await media.view_video(-100123, 77)
    assert tool.downloads == 0


def test_view_video_is_registered_read_only():
    registered = {t.name: t for t in runtime.mcp._tool_manager.list_tools()}
    assert registered["view_video"].annotations.readOnlyHint is True


# --- real ffmpeg ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def three_scenes(tmp_path_factory):
    """12 s: three different pictures of 4 s each, with a tone."""
    path = tmp_path_factory.mktemp("video") / "scenes.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "smptebars=size=320x240:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=320x240:rate=25:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=300:duration=12",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-map",
            "3:a",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return str(path)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="no ffmpeg")
class TestRealFfmpeg:
    @pytest.mark.asyncio
    async def test_cuts_frames_and_audio(self, three_scenes):
        info = await video_tools.probe(three_scenes)
        assert info.has_video and info.has_audio and 11.5 < info.duration < 12.5
        cuts = await video_tools.scene_times(three_scenes, 0, info.duration, 60)
        assert [round(t) for t in cuts] == [4, 8]
        assert [round(t) for t in await video_tools.scene_times(three_scenes, 3, 9, 60)] == [4, 8]
        shown, frame = await video_tools.grab_frame(three_scenes, info.duration, 60)
        assert frame[:2] == b"\xff\xd8" and shown <= info.duration
        # The tail retry steps back half a second, but never out of the window.
        tail = await video_tools.grab_frame(
            three_scenes, info.duration, 60, not_before=info.duration - 0.1
        )
        assert tail is None or tail[0] >= info.duration - 0.1
        audio = await video_tools.extract_audio(three_scenes, 3, 9, 60)
        assert audio[:4] == b"fLaC"


@pytest.mark.asyncio
async def test_view_video_engine_error_keeps_frames_and_hides_details(tool, monkeypatch):
    async def _broken(data, engine, **kwargs):
        return {"status": "error", "error": "secret upstream detail /private/path"}

    monkeypatch.setattr(transcription, "transcribe_audio", _broken)
    text, _, images = _split(await media.view_video(-100123, 77))
    assert images
    assert "Speech: transcription failed" in text and "code:" in text
    assert "secret" not in text and "/private/path" not in text


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="no ffmpeg")
@pytest.mark.asyncio
async def test_streamed_webm_without_duration_is_measured(tmp_path):
    path = tmp_path / "stream.webm"
    with open(path, "wb") as out:
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x120:rate=10:duration=3",
                "-c:v",
                "libvpx",
                "-f",
                "webm",
                "pipe:1",
            ],
            stdout=out,
            check=True,
        )
    info = await video_tools.probe(str(path))
    assert info.duration is not None and 2.5 < info.duration <= 3.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="no ffmpeg")
@pytest.mark.asyncio
async def test_output_cap_kills_the_process():
    args = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i"]
    args += ["sine=duration=600", "-f", "s16le", "pipe:1"]
    with pytest.raises(video_tools.OutputTooLarge):
        await video_tools._run(args, 60, max_stdout=100_000)


@pytest.mark.asyncio
async def test_view_video_refuses_unknown_length_and_huge_frames(tool, monkeypatch):
    tool.msg = _video_msg(file=SimpleNamespace(ext=".webm", mime_type="video/webm", size=10))

    async def _unknown(path, timeout=None):
        return video_tools.VideoInfo(None, 640, 360, True, False)

    monkeypatch.setattr(video_tools, "probe", _unknown)
    assert "length is unknown" in await media.view_video(-100123, 77)
    text, _, images = _split(await media.view_video(-100123, 77, end=6))
    assert images and "Looking at 0:00-0:06" in text

    async def _huge(path, timeout=None):
        return video_tools.VideoInfo(10.0, 20000, 20000, True, False)

    monkeypatch.setattr(video_tools, "probe", _huge)
    assert "pixels per frame" in await media.view_video(-100123, 77)


@pytest.mark.asyncio
async def test_view_video_scene_timeout_still_gives_even_frames(tool, monkeypatch):
    async def _slow(path, start, end, timeout):
        raise asyncio.TimeoutError

    monkeypatch.setattr(video_tools, "scene_times", _slow)
    text, _, images = _split(await media.view_video(-100123, 77, transcribe=False))
    assert len(images) >= 2 and "frames are evenly spaced" in text


@pytest.mark.asyncio
async def test_cancelled_call_joins_speech_before_cleanup(tool, monkeypatch):
    """The speech task (and its ffmpeg child) ends before the temp dir and lock go."""
    seen = {}

    async def _hanging(data, engine, **kwargs):
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            seen["dir_at_cancel"] = os.path.isdir(seen["dir"])
            seen["lock_held"] = media._VIDEO_SEMAPHORE.locked()
            raise

    async def _audio(path, start, end, timeout, max_bytes=None):
        seen["dir"] = os.path.dirname(path)
        return b"fLaC"

    monkeypatch.setattr(video_tools, "extract_audio", _audio)
    monkeypatch.setattr(transcription, "transcribe_audio", _hanging)
    task = asyncio.ensure_future(media.view_video(-100123, 77))
    while "dir" not in seen:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert seen["dir_at_cancel"] and seen["lock_held"], "speech outlived the temp dir or lock"
    assert not os.path.exists(seen["dir"])
    assert not media._VIDEO_SEMAPHORE.locked()


@pytest.mark.asyncio
async def test_time_budget_counts_from_the_call_not_after_telegram(tool, monkeypatch):
    """Slow entity/message lookups eat into the same server-wide tool timeout."""
    tool.speech_delay = 30

    async def _slow_resolve(chat_id, cl):
        await asyncio.sleep(1.0)
        return object()

    monkeypatch.setattr(media, "resolve_entity", _slow_resolve)
    monkeypatch.setattr(media, "VIDEO_TIME_MARGIN_SECONDS", 0)
    monkeypatch.setattr(runtime, "_tool_timeout_seconds", lambda value=None: 2.5)
    started = asyncio.get_running_loop().time()
    text, _, images = _split(await media.view_video(-100123, 77))
    assert asyncio.get_running_loop().time() - started < 3.0  # 3.5 s if counted after lookups
    assert images and "did not finish within the tool time limit" in text


def test_packet_span_includes_the_last_frame():
    assert (
        video_tools.packet_span("0.000000,0.500000\n0.500000,0.500000\n1.000000,0.500000") == 1.5
    )
    assert video_tools.packet_span("10.0,1.0\n11.0,1.0") == 2.0  # timestamps need not start at 0
    assert video_tools.packet_span("N/A,N/A\n") is None


@pytest.mark.asyncio
async def test_run_deadline_covers_process_exit():
    """A child that closes its pipes and lingers is still killed at the deadline."""
    import sys

    code = "import os, time; os.close(1); os.close(2); time.sleep(5)"
    started = asyncio.get_running_loop().time()
    with pytest.raises(asyncio.TimeoutError):
        await video_tools._run([sys.executable, "-c", code], 0.3)
    assert asyncio.get_running_loop().time() - started < 2


@pytest.mark.asyncio
async def test_view_video_scene_overflow_still_gives_even_frames(tool, monkeypatch):
    async def _flood(path, start, end, timeout):
        raise video_tools.OutputTooLarge("too much")

    monkeypatch.setattr(video_tools, "scene_times", _flood)
    text, _, images = _split(await media.view_video(-100123, 77, transcribe=False))
    assert len(images) >= 2 and "frames are evenly spaced" in text
