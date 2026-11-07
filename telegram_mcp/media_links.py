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
import os
import secrets
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


def _verified(kind: str, rel_path: str, expires: str, sig: str, now: Optional[float]) -> bool:
    try:
        expires_at = int(expires)
    except ValueError:
        return False
    if expires_at < _now(now):
        return False
    return hmac.compare_digest(sig, _signature(kind, rel_path, expires_at))


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
    if root is None or not _verified("media", rel_path, expires, sig, now):
        return None
    root = root.resolve()
    target = (root / rel_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def sweep_expired_uploads(root: Path, now: Optional[float] = None) -> None:
    """Delete upload directories created more than two URL lifetimes ago."""
    uploads = root / UPLOAD_SUBDIR
    if not uploads.is_dir():
        return
    cutoff = _now(now) - 2 * URL_TTL_SECONDS
    for slot in uploads.iterdir():
        if slot.is_dir() and slot.stat().st_mtime < cutoff:
            for entry in slot.iterdir():
                entry.unlink(missing_ok=True)
            slot.rmdir()


def upload_slot(
    filename: str, root: Optional[Path], now: Optional[float] = None
) -> Optional[dict]:
    """{url, file_path} for one upload under root, or None without a base URL, root or name."""
    name = Path(filename.replace("\\", "/")).name.strip()
    if base_url() is None or root is None or name in ("", ".", ".."):
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
    if root is None or not _verified("upload", rel_path, expires, sig, now):
        return None
    uploads = (root / UPLOAD_SUBDIR).resolve()
    target = (uploads / rel_path).resolve()
    if not target.is_relative_to(uploads) or target.exists():
        return None
    return target
