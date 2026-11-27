"""Signed, expiring URLs that move files between the server and a remote client.

File-path tools read and write the server's disk, which a client on another
machine cannot reach. With TELEGRAM_MEDIA_BASE_URL set, download_media also
returns a URL that serves the downloaded file, and prepare_upload returns a URL
that accepts one file, which send_file and the other sending tools can then
read by path. Both are served by this server's own HTTP transport, carry an
HMAC over the path and an expiry, and stop working when they expire or the
server restarts. Both stay inside the first server allowed root.
"""

import hashlib
import hmac
import logging
import os
import secrets
import shutil
import stat
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode

MEDIA_ROUTE_PREFIX = "/media/"
UPLOAD_ROUTE_PREFIX = "/upload/"
UPLOAD_SUBDIR = "uploads"
URL_TTL_SECONDS = 3600

# Per process: a restart invalidates every URL handed out before it.
_SECRET = secrets.token_bytes(32)

logger = logging.getLogger(__name__)


def base_url() -> Optional[str]:
    """Public URL under which clients reach this server, or None if unset."""
    return os.getenv("TELEGRAM_MEDIA_BASE_URL", "").strip().rstrip("/") or None


def _now(now: Optional[float]) -> float:
    return now if now is not None else time.time()


def _signature(kind: str, rel_path: str, expires: int) -> str:
    message = f"{kind}\n{rel_path}\n{expires}".encode()
    return hmac.new(_SECRET, message, hashlib.sha256).hexdigest()


def _signed(kind: str, prefix: str, rel: str, now: Optional[float]) -> str:
    expires = int(_now(now)) + URL_TTL_SECONDS
    query = urlencode({"expires": expires, "sig": _signature(kind, rel, expires)})
    return f"{base_url()}{prefix}{quote(rel)}?{query}"


def _is_link(path: Path) -> bool:
    """Return True if *path* is a symlink or a Windows directory junction."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        return bool(is_junction())
    mount_point_tag = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", None)
    if mount_point_tag is None:
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return getattr(st, "st_reparse_tag", 0) == mount_point_tag


def _verified(kind: str, rel_path: str, expires: str, sig: str, now: Optional[float]) -> bool:
    if not isinstance(sig, str):
        return False
    try:
        expires_at = int(expires)
    except (TypeError, ValueError):
        return False
    if expires_at < _now(now):
        return False
    expected = _signature(kind, rel_path, expires_at).encode("ascii")
    return hmac.compare_digest(sig.encode("utf-8", "surrogatepass"), expected)


def download_url(
    file_path: Path, root: Optional[Path], now: Optional[float] = None
) -> Optional[str]:
    """URL serving a file inside root; None without a base URL or root, or outside root."""
    if base_url() is None or root is None:
        return None
    try:
        rel = Path(file_path).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    return _signed("media", MEDIA_ROUTE_PREFIX, rel, now)


def resolve_download(
    rel_path: str, expires: str, sig: str, root: Optional[Path], now: Optional[float] = None
) -> Optional[Path]:
    """The file a download URL names, or None if it is invalid, expired or gone."""
    if root is None or "\x00" in rel_path or not _verified("media", rel_path, expires, sig, now):
        return None
    try:
        root = root.resolve()
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _clean_slot_entry(entry: Path) -> None:
    try:
        if _is_link(entry):
            if entry.is_symlink():
                entry.unlink(missing_ok=True)
            else:
                entry.rmdir()
        elif entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except PermissionError as e:
        logger.warning("Permission denied removing upload entry %s: %s", entry, e)
    except OSError as e:
        logger.warning("Failed to remove upload entry %s: %s", entry, e)


def _clean_slot(slot: Path) -> None:
    try:
        for entry in slot.iterdir():
            _clean_slot_entry(entry)
        slot.rmdir()
    except FileNotFoundError:
        pass
    except PermissionError as e:
        logger.warning("Permission denied removing upload slot %s: %s", slot, e)
    except OSError as e:
        logger.warning("Failed to remove upload slot %s: %s", slot, e)


def sweep_expired_uploads(root: Path, now: Optional[float] = None) -> None:
    """Delete upload directories modified more than two URL lifetimes ago."""
    uploads = root / UPLOAD_SUBDIR
    if _is_link(uploads) or not uploads.is_dir():
        return
    cutoff = _now(now) - 2 * URL_TTL_SECONDS
    try:
        slots = uploads.iterdir()
    except OSError as e:
        logger.warning("Could not read uploads directory %s: %s", uploads, e)
        return

    for slot in slots:
        try:
            if _is_link(slot) or not slot.is_dir() or slot.stat().st_mtime >= cutoff:
                continue
        except FileNotFoundError:
            continue
        except OSError as e:
            logger.warning("Could not inspect upload slot %s: %s", slot, e)
            continue

        _clean_slot(slot)


def upload_slot(
    filename: str, root: Optional[Path], now: Optional[float] = None
) -> Optional[dict]:
    """{url, file_path} for one upload under root, or None without a base URL, root or name."""
    if not isinstance(filename, str):
        return None
    name = Path(filename.replace("\\", "/")).name.strip()
    if (
        base_url() is None
        or root is None
        or name in ("", ".", "..")
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        return None
    sweep_expired_uploads(root, now)
    rel = f"{secrets.token_hex(16)}/{name}"
    return {
        "url": _signed("upload", UPLOAD_ROUTE_PREFIX, rel, now),
        "file_path": str(root.resolve() / UPLOAD_SUBDIR / rel),
    }


def resolve_upload(
    rel_path: str, expires: str, sig: str, root: Optional[Path], now: Optional[float] = None
) -> Optional[Path]:
    """Where an upload URL writes, or None if it is invalid, expired or already used."""
    if root is None or "\x00" in rel_path or not _verified("upload", rel_path, expires, sig, now):
        return None
    try:
        uploads = (root / UPLOAD_SUBDIR).resolve()
        target = (uploads / rel_path).resolve()
        if not target.is_relative_to(uploads) or target.exists():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target
