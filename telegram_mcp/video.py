"""Look at a video without a video model: still frames plus a timestamped transcript.

Frames are taken where the picture changes (ffmpeg's scene score) and, between those, at
even spacing, so a screencast or a slide deck shows every new screen while a talking head
still gets a few evenly spread frames. Near-identical frames are dropped. The sound track
goes to the configured transcription engine; when the engine returns segments, speech and
frames are merged into one timeline so the reader can tell what was said while what was
on screen.

Needs the ffmpeg and ffprobe binaries on PATH. Everything here works on a local file; the
caller downloads the video and removes it.
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import shutil
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

DEFAULT_FRAMES = 8
MAX_FRAMES = 16
FRAME_EDGE_PIXELS = 768
JPEG_QSCALE = "4"  # ffmpeg mjpeg quality, 2 (best) .. 31
SCENE_THRESHOLD = 0.3  # ffmpeg scene score that counts as a cut
SCENE_SAMPLE_FPS = 4  # frames per second the scene detector looks at
DUPLICATE_DIFFERENCE = 2.5  # mean absolute difference (0..255) of 16x16 greyscale thumbnails
AUDIO_SAMPLE_RATE = 16000
STEP_TIMEOUT_SECONDS = 60.0
PACKET_SCAN_SECONDS = 15.0  # at most this long measuring a file that states no duration
MAX_STDOUT_BYTES = 64 * 1048576  # a frame is ~100 KB; audio has its own, smaller cap
MAX_STDERR_BYTES = 16 * 1048576  # scene detection logs one showinfo line per cut
MAX_PIXELS = 8192 * 4320  # larger frames are refused before anything is decoded

_PTS_TIME = re.compile(r"pts_time:\s*([0-9]+(?:\.[0-9]+)?)")


FFMPEG_MISSING = (
    "view_video needs ffmpeg and ffprobe on the server's PATH (Debian/Ubuntu: apt install "
    "ffmpeg; Alpine: apk add ffmpeg; macOS: brew install ffmpeg)."
)


class VideoToolUnavailable(RuntimeError):
    """ffmpeg/ffprobe are missing, so callers can say how to get them."""


class OutputTooLarge(RuntimeError):
    """A child process wrote more than its cap; it has been killed."""


@dataclass(frozen=True)
class VideoInfo:
    duration: Optional[float]
    width: Optional[int]
    height: Optional[int]
    has_video: bool
    has_audio: bool


def binaries() -> Tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise VideoToolUnavailable(FFMPEG_MISSING)
    return ffmpeg, ffprobe


async def _drain(stream, limit: int, chunks: list) -> None:
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        total += len(chunk)
        if total > limit:
            raise OutputTooLarge(f"output above {limit} bytes")
        chunks.append(chunk)


async def _run(
    args: Sequence[str],
    timeout: float,
    max_stdout: int = MAX_STDOUT_BYTES,
    max_stderr: int = MAX_STDERR_BYTES,
) -> Tuple[int, bytes, bytes]:
    """Run one binary without a shell, reading at most max_stdout/max_stderr bytes.

    On timeout (asyncio.TimeoutError), overflow (OutputTooLarge) or cancellation the
    process is killed and reaped before the exception propagates.
    """
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out: list = []
    err: list = []
    readers = [
        asyncio.ensure_future(_drain(proc.stdout, max_stdout, out)),
        asyncio.ensure_future(_drain(proc.stderr, max_stderr, err)),
    ]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout  # one deadline for the pipes and the exit
    try:
        await asyncio.wait_for(asyncio.gather(*readers), timeout=timeout)
        code = await asyncio.wait_for(proc.wait(), timeout=max(0.0, deadline - loop.time()))
    except BaseException:
        for reader in readers:
            reader.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await asyncio.gather(*readers, return_exceptions=True)
        raise
    return code, b"".join(out), b"".join(err)


def _number(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_probe(payload: dict) -> VideoInfo:
    streams = payload.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    duration = _float((payload.get("format") or {}).get("duration"))
    if duration is None:
        duration = max(
            (d for d in (_float(s.get("duration")) for s in streams) if d is not None),
            default=None,
        )
    return VideoInfo(
        duration=duration,
        width=(video or {}).get("width"),
        height=(video or {}).get("height"),
        has_video=video is not None,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


async def probe(path: str, timeout: float = STEP_TIMEOUT_SECONDS) -> VideoInfo:
    _, ffprobe = binaries()
    code, out, err = await _run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,duration",
            "-of",
            "json",
            path,
        ],
        timeout,
    )
    if code != 0:
        raise ValueError(f"ffprobe could not read the file: {err.decode(errors='replace')[-300:]}")
    info = parse_probe(json.loads(out or b"{}"))
    if info.duration is None and info.has_video:
        # Streamed WebM and similar files carry no duration: measure it from packet times
        # (demuxing only, no decoding), within part of the budget. If that fails too, the
        # duration stays unknown and the caller asks for an explicit end.
        try:
            code, out, _ = await _run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "packet=pts_time,duration_time",
                    "-of",
                    "csv=p=0",
                    path,
                ],
                min(timeout * 0.5, PACKET_SCAN_SECONDS),
                max_stdout=16 * 1048576,
            )
        except (asyncio.TimeoutError, OutputTooLarge):
            code, out = 1, b""
        measured = packet_span(out.decode(errors="replace")) if code == 0 else None
        if measured is not None:
            info = VideoInfo(measured, info.width, info.height, info.has_video, info.has_audio)
    return info


def packet_span(csv: str) -> Optional[float]:
    """Length covered by "pts_time,duration_time" packet lines: from the first packet's
    start to the end of the last one (the last frame lasts too)."""
    starts, ends = [], []
    for line in csv.split():
        fields = line.split(",")
        start = _number(fields[0])
        if start is None:
            continue
        length = _number(fields[1]) if len(fields) > 1 else None
        starts.append(start)
        ends.append(start + (length or 0.0))
    if not starts:
        return None
    span = max(ends) - min(starts)
    return span if span > 0 else None


def parse_scene_times(stderr: str, offset: float) -> List[float]:
    """showinfo prints pts_time relative to the seek point; shift back to file time."""
    return sorted({round(float(m) + offset, 2) for m in _PTS_TIME.findall(stderr)})


async def scene_times(path: str, start: float, end: float, timeout: float) -> List[float]:
    """Times of picture cuts in [start, end). Decodes the whole window (keyframes alone
    miss most cuts) but scores only a few small frames per second."""
    ffmpeg, _ = binaries()
    code, _, err = await _run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            path,
            "-an",
            "-sn",
            "-vf",
            f"fps={SCENE_SAMPLE_FPS},scale=160:-2,"
            f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
            "-f",
            "null",
            "-",
        ],
        timeout,
    )
    if code != 0:
        return []
    return [t for t in parse_scene_times(err.decode(errors="replace"), start) if start < t < end]


def too_big(info: VideoInfo) -> bool:
    return bool(info.width and info.height and info.width * info.height > MAX_PIXELS)


def pick_times(start: float, end: float, cuts: Sequence[float], max_frames: int) -> List[float]:
    """Frame times: the opening, then cuts, then even spacing, each time taking the
    candidate farthest from everything already picked, never two closer than half an
    even step (those would show the same moment)."""
    max_frames = max(1, max_frames)
    span = end - start
    first = start + min(0.5, span * 0.05)
    picks = [first]
    if span <= 0 or max_frames == 1:
        return picks
    min_gap = span / (max_frames * 2)
    grid = [start + (i + 0.5) * span / max_frames for i in range(max_frames)]
    for pool in ([t for t in cuts if start <= t < end], grid):
        pool = list(pool)
        while pool and len(picks) < max_frames:
            best = max(pool, key=lambda c: min(abs(c - p) for p in picks))
            if min(abs(best - p) for p in picks) < min_gap:
                break
            picks.append(best)
            pool.remove(best)
    return sorted(picks)


async def grab_frame(
    path: str, at: float, timeout: float, not_before: float = 0.0
) -> Optional[Tuple[float, bytes]]:
    """(time actually shown, JPEG no larger than FRAME_EDGE_PIXELS on its long side), or
    None. The last instant of a file can hold no frame: then half a second earlier, but
    never before not_before (the start of the window being looked at)."""
    ffmpeg, _ = binaries()
    edge = FRAME_EDGE_PIXELS
    seeks = [at] + ([at - 0.5] if at - 0.5 >= not_before else [])
    for seek in seeks:
        code, out, _ = await _run(
            [
                ffmpeg,
                "-nostdin",
                "-loglevel",
                "error",
                "-ss",
                f"{seek:.3f}",
                "-i",
                path,
                "-an",
                "-frames:v",
                "1",
                "-vf",
                f"scale='min({edge},iw)':'min({edge},ih)':force_original_aspect_ratio=decrease",
                "-q:v",
                JPEG_QSCALE,
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "pipe:1",
            ],
            timeout,
        )
        if code == 0 and out:
            return seek, out
    return None


def _fingerprint(jpeg: bytes):
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as image:
        return image.convert("L").resize((16, 16)).tobytes()


def drop_duplicates(frames: List[Tuple[float, bytes]]) -> List[Tuple[float, bytes]]:
    """Drop frames that look like an earlier kept one (static scenes, slides held long)."""
    kept: List[Tuple[float, bytes]] = []
    prints: list = []
    for at, jpeg in frames:
        try:
            current = _fingerprint(jpeg)
        except Exception:
            kept.append((at, jpeg))
            continue
        if any(
            sum(abs(a - b) for a, b in zip(current, other)) / len(current) < DUPLICATE_DIFFERENCE
            for other in prints
        ):
            continue
        kept.append((at, jpeg))
        prints.append(current)
    return kept


async def extract_audio(
    path: str, start: float, end: float, timeout: float, max_bytes: int = MAX_STDOUT_BYTES
) -> bytes:
    """The window's sound as 16 kHz mono FLAC (lossless, accepted by every engine).
    Raises OutputTooLarge as soon as it would exceed max_bytes."""
    ffmpeg, _ = binaries()
    code, out, err = await _run(
        [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{end - start:.3f}",
            "-i",
            path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-c:a",
            "flac",
            "-f",
            "flac",
            "pipe:1",
        ],
        timeout,
        max_stdout=max_bytes,
    )
    if code != 0 or not out:
        raise ValueError(f"could not extract audio: {err.decode(errors='replace')[-300:]}")
    return out


def clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def timeline(frame_times: Sequence[float], segments: Sequence[dict]) -> List[str]:
    """Frames and speech on one clock, in time order (a frame before speech at the same time)."""
    rows = [(at, 0, f"{clock(at)} [frame {i}]") for i, at in enumerate(frame_times, 1)]
    for segment in segments:
        rows.append(
            (
                segment["start"],
                1,
                f"{clock(segment['start'])}-{clock(segment['end'])} {segment['text']}",
            )
        )
    return [text for _, _, text in sorted(rows, key=lambda row: (row[0], row[1]))]
