"""Media classification and download, laid out the way Telegram Desktop does it."""

from pathlib import Path
from typing import Any, Optional

from .util import human_size, log, safe_name

# kind -> subdirectory, mirroring a Telegram Desktop export so the folders are
# familiar to anyone who has ever exported a chat from the client.
_DIRS = {
    "photo": "photos",
    "video": "video_files",
    "voice": "voice_messages",
    "round": "round_video_messages",
    "sticker": "stickers",
    "animation": "animations",
    "audio": "audio_files",
    "document": "files",
    "contact": "files",
    "other": "files",
}


def classify(message: Any) -> Optional[str]:
    """The message's media kind, or None for a text-only message."""
    if getattr(message, "media", None) is None:
        return None
    # A link preview is the sender's link, not the chat's media, and Telegram
    # Desktop does not save it either. This has to be tested before anything
    # else: Telethon exposes a preview's own photo and document through
    # `.photo` and `.document`, so checked further down it never matches - a
    # preview of a video link comes out classified as a photo and the export
    # downloads the whole linked video under a `.jpg` name.
    if getattr(message, "web_preview", None) is not None:
        return None
    if getattr(message, "photo", None) is not None:
        return "photo"
    if getattr(message, "voice", None) is not None:
        return "voice"
    if getattr(message, "video_note", None) is not None:
        return "round"
    if getattr(message, "sticker", None) is not None:
        return "sticker"
    if getattr(message, "gif", None) is not None:
        return "animation"
    if getattr(message, "video", None) is not None:
        return "video"
    if getattr(message, "audio", None) is not None:
        return "audio"
    if getattr(message, "contact", None) is not None:
        return "contact"
    if getattr(message, "document", None) is not None:
        return "document"
    return "other"


def describe(message: Any, kind: str) -> dict:
    """Media metadata that survives even when the bytes are not downloaded."""
    file_attr = getattr(message, "file", None)
    info: dict[str, Any] = {
        "kind": kind,
        "mime": getattr(file_attr, "mime_type", None),
        "size": getattr(file_attr, "size", None),
        "name": getattr(file_attr, "name", None),
        "ext": getattr(file_attr, "ext", None),
        "duration": getattr(file_attr, "duration", None),
        "width": getattr(file_attr, "width", None),
        "height": getattr(file_attr, "height", None),
        "file": None,
        "skipped": None,
    }
    sticker_emoji = getattr(getattr(message, "sticker", None), "id", None)
    if sticker_emoji is not None:
        info["sticker_id"] = str(sticker_emoji)
    return info


def target_path(root: Path, message: Any, info: dict) -> Path:
    subdir = _DIRS.get(info["kind"], "files")
    ext = info.get("ext") or ""
    name = info.get("name")
    if name:
        stem = safe_name(Path(name).stem, limit=40)
        ext = Path(name).suffix or ext
    else:
        stem = info["kind"]
    return root / subdir / f"{message.id:08d}_{stem}{ext}"


class _TooLarge(Exception):
    """Raised from the download's progress callback to stop it mid-flight."""

    def __init__(self, received: int) -> None:
        super().__init__(received)
        self.received = received


def _discard(path: Path) -> None:
    """Remove a partial or oversized download. Left on disk it is worse than
    nothing: the resume path sees a non-empty file and takes it for the media.
    """
    try:
        path.unlink()
    except OSError:
        pass


async def download(
    client: Any, message: Any, info: dict, root: Path, max_bytes: Optional[int]
) -> dict:
    """Download the message's media into ``root``; annotate ``info`` in place."""
    size = info.get("size")
    if max_bytes and size and size > max_bytes:
        info["skipped"] = f"larger than limit ({human_size(size)})"
        return info
    path = target_path(root, message, info)
    if path.exists() and path.stat().st_size > 0:
        info["file"] = str(path.relative_to(root.parent))
        return info
    path.parent.mkdir(parents=True, exist_ok=True)

    # The declared size is a hint, not a promise - a photo can report one of
    # its thumbnails and still pull tens of megabytes. So the limit is enforced
    # again on the bytes as they arrive, and once more on the finished file for
    # the downloads that never report progress. Without that, the limit is
    # advisory and one message can spend the whole export's disk budget.
    def _guard(received: int, total: int) -> None:
        if received > max_bytes:
            raise _TooLarge(received)

    try:
        saved = await client.download_media(
            message, file=str(path), progress_callback=_guard if max_bytes else None
        )
    except _TooLarge as exc:
        _discard(path)
        info["skipped"] = f"larger than limit (over {human_size(exc.received)})"
        return info
    except Exception as exc:  # network, revoked file reference, deleted media
        _discard(path)
        info["skipped"] = f"download failed: {exc}"
        log(f"  ! media for message {message.id}: {exc}")
        return info
    if not saved:
        info["skipped"] = "nothing to download"
        return info
    saved_path = Path(saved)
    if max_bytes:
        try:
            actual = saved_path.stat().st_size
        except OSError:
            actual = 0
        if actual > max_bytes:
            _discard(saved_path)
            info["skipped"] = f"larger than limit ({human_size(actual)})"
            return info
    info["file"] = str(saved_path.relative_to(root.parent))
    return info
