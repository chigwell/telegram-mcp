"""send_file / send_album: real video dimensions and duration, no hachoir needed."""

import struct

import pytest
from telethon import types

from telegram_mcp import runtime
from telegram_mcp.tools import media


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + box_type + payload


def _mvhd(duration: int, timescale: int = 1000, version: int = 0) -> bytes:
    if version == 1:
        payload = struct.pack(">B3xQQIQ", 1, 0, 0, timescale, duration)
    else:
        payload = struct.pack(">B3xIIII", 0, 0, 0, timescale, duration)
    return _box(b"mvhd", payload)


def _tkhd(width: int, height: int, rotated: bool = False) -> bytes:
    matrix = (0, 0x40000000, 0, 0) if rotated else (0x40000000, 0, 0, 0x40000000)
    payload = struct.pack(">B3x", 0)  # version 0 + flags
    payload += struct.pack(">IIIII", 0, 0, 1, 0, 0)  # creation, modification, id, res, duration
    payload += struct.pack(">8x")  # reserved
    payload += struct.pack(">HHHH", 0, 0, 0, 0)  # layer, alt group, volume, reserved
    payload += struct.pack(  # display matrix; third column is 0/0/0x40000000
        ">9i", matrix[0], matrix[1], 0, matrix[2], matrix[3], 0, 0, 0, 0x40000000
    )
    payload += struct.pack(">II", width << 16, height << 16)  # 16.16 fixed point
    return _box(b"tkhd", payload)


def _mp4(*children: bytes) -> bytes:
    return _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + _box(b"moov", b"".join(children))


def _trak(*children: bytes) -> bytes:
    return _box(b"trak", b"".join(children))


# --- the parser ---


def test_reads_duration_and_swaps_rotated_dimensions(tmp_path):
    path = tmp_path / "clip.mp4"
    # A portrait phone clip: stored 1920x1080 with a 90-degree display matrix.
    path.write_bytes(_mp4(_mvhd(42000), _trak(_tkhd(0, 0)), _trak(_tkhd(1920, 1080, True))))

    attributes = media._mp4_video_attributes(str(path))

    assert (attributes.w, attributes.h) == (1080, 1920)
    assert attributes.duration == 42
    assert attributes.supports_streaming is True


def test_identity_matrix_keeps_dimensions(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(_mp4(_mvhd(5000), _trak(_tkhd(3840, 2160))))

    attributes = media._mp4_video_attributes(str(path))

    assert (attributes.w, attributes.h, attributes.duration) == (3840, 2160, 5)


def test_reads_64bit_mvhd(tmp_path):
    path = tmp_path / "clip.mov"
    # 2**33 does not fit the 32-bit duration field a buggy parser would read.
    path.write_bytes(_mp4(_mvhd(2**33, timescale=1, version=1), _trak(_tkhd(640, 360))))

    attributes = media._mp4_video_attributes(str(path))

    assert (attributes.w, attributes.h, attributes.duration) == (640, 360, 2**33)


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # empty file
        b"\x00\x00\x00\x1cftypjunk",  # no moov
        _mp4(_mvhd(42)),  # moov without a video track
        _mp4(_box(b"mvhd", b"\x00\x00\x00\x00\x00\x00\x00\x00\x00"), _trak(_tkhd(2, 2))),
        # mvhd v1 truncated inside the 64-bit duration
        _mp4(_box(b"mvhd", struct.pack(">B3xQQI", 1, 0, 0, 1000)), _trak(_tkhd(2, 2))),
        struct.pack(">I", 8) + b"mdat",  # size below the header
    ],
)
def test_malformed_containers_return_none(tmp_path, payload):
    path = tmp_path / "broken.mp4"
    path.write_bytes(payload)

    assert media._mp4_video_attributes(str(path)) is None


# --- the tools ---


class _SingleClient:
    def __init__(self):
        self.kwargs = None

    async def send_file(self, entity, file, **kwargs):
        self.kwargs = kwargs


class _AlbumClient:
    def __init__(self):
        self.media_items = None
        self.paths = None
        self.kwargs = None

    async def upload_file(self, path):
        return f"handle:{path}"

    async def send_file(self, entity, file, **kwargs):
        self.kwargs = kwargs
        if isinstance(file, list) and not isinstance(file[0], str):
            self.media_items = file
        else:
            self.paths = file


def _wire(monkeypatch, client, root, resolve_to="entity:chat"):
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    monkeypatch.setattr(media, "clients", {"default": client})
    monkeypatch.setattr(media, "get_client", lambda account=None: client)

    async def _resolve_entity(chat_id, cl):
        return resolve_to

    monkeypatch.setattr(media, "resolve_entity", _resolve_entity)


def _write_video(root):
    path = root / "clip.mp4"
    path.write_bytes(_mp4(_mvhd(42000), _trak(_tkhd(1920, 1080, True))))
    return path


@pytest.mark.asyncio
async def test_send_file_passes_video_attributes(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    path = _write_video(root)
    client = _SingleClient()
    _wire(monkeypatch, client, root)

    await media.send_file("chat", "clip.mp4")

    (attributes,) = client.kwargs["attributes"]
    assert (attributes.w, attributes.h, attributes.duration) == (1080, 1920, 42)


@pytest.mark.asyncio
async def test_send_file_leaves_non_videos_alone(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    (root / "doc.pdf").write_bytes(b"%PDF")
    client = _SingleClient()
    _wire(monkeypatch, client, root)

    await media.send_file("chat", "doc.pdf")

    assert "attributes" not in client.kwargs
    assert "force_document" not in client.kwargs


@pytest.mark.asyncio
async def test_send_file_forced_document_keeps_the_old_path(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    _write_video(root)
    client = _SingleClient()
    _wire(monkeypatch, client, root)

    await media.send_file("chat", "clip.mp4", force_document=True)

    assert "attributes" not in client.kwargs
    assert client.kwargs["force_document"] is True


@pytest.mark.asyncio
async def test_album_with_video_builds_per_file_media(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    video = _write_video(root)
    picture = root / "pic.png"
    picture.write_bytes(b"png-bytes")
    client = _AlbumClient()
    _wire(monkeypatch, client, root)

    await media.send_file("chat", ["clip.mp4", "pic.png"], caption="cap")

    video_media, photo_media = client.media_items
    assert isinstance(video_media, types.InputMediaUploadedDocument)
    assert video_media.file == f"handle:{video}"
    assert video_media.mime_type == "video/mp4"
    assert video_media.attributes[0].file_name == "clip.mp4"
    (attributes,) = video_media.attributes[1:]
    assert (attributes.w, attributes.h) == (1080, 1920)
    assert video_media.nosound_video is True
    assert isinstance(photo_media, types.InputMediaUploadedPhoto)
    assert photo_media.file == f"handle:{picture}"
    assert client.kwargs["caption"] == "cap"


@pytest.mark.asyncio
async def test_album_non_image_file_keeps_filename(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    _write_video(root)
    doc = root / "doc.pdf"
    doc.write_bytes(b"%PDF")
    client = _AlbumClient()
    _wire(monkeypatch, client, root)

    await media.send_file("chat", ["clip.mp4", "doc.pdf"])

    _video_media, doc_media = client.media_items
    assert isinstance(doc_media, types.InputMediaUploadedDocument)
    assert doc_media.attributes[0].file_name == "doc.pdf"


@pytest.mark.asyncio
async def test_image_only_album_keeps_telethon_path(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    first = root / "one.png"
    second = root / "two.png"
    first.write_bytes(b"png-one")
    second.write_bytes(b"png-two")
    client = _AlbumClient()
    _wire(monkeypatch, client, root)

    await media.send_album("chat", ["one.png", "two.png"])

    assert client.media_items is None
    assert client.paths == [str(first), str(second)]


@pytest.mark.asyncio
async def test_video_album_forced_document_keeps_telethon_path(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    _write_video(root)
    (root / "pic.png").write_bytes(b"png")
    client = _AlbumClient()
    _wire(monkeypatch, client, root)

    # send_album has no force_document parameter; the list form of send_file
    # drives the same _send_album branch.
    await media.send_file("chat", ["clip.mp4", "pic.png"], force_document=True)

    assert client.media_items is None
    assert len(client.paths) == 2
