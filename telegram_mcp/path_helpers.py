"""Filesystem normalization helpers; roots selection stays in runtime."""

import os
from pathlib import Path
from typing import List
from urllib.parse import unquote, urlparse


def _dedupe_paths(paths: List[Path]) -> List[Path]:
    seen: set[str] = set()
    result: List[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _coerce_root_uri_to_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Unsupported root URI scheme: {parsed.scheme}")

    decoded_path = unquote(parsed.path or "")
    if parsed.netloc and parsed.netloc not in ("", "localhost"):
        decoded_path = f"//{parsed.netloc}{decoded_path}"
    if os.name == "nt" and decoded_path.startswith("/") and len(decoded_path) > 2:
        # file:///C:/tmp -> C:/tmp on Windows
        if decoded_path[2] == ":":
            decoded_path = decoded_path[1:]
    return Path(decoded_path).resolve(strict=True)


def _path_is_within_root(candidate: Path, root: Path) -> bool:
    root = root.resolve()
    if root.is_file():
        return candidate == root
    return candidate == root or root in candidate.parents


def _path_is_within_any_root(candidate: Path, roots: List[Path]) -> bool:
    return any(_path_is_within_root(candidate, root) for root in roots)


def _first_resolution_root(roots: List[Path]) -> Path:
    first = roots[0]
    return first if first.is_dir() else first.parent


def _coerce_paths_from_list_roots_validation_error(error: Exception) -> List[Path]:
    """Recover absolute filesystem roots when a client sends bare paths.

    Some MCP clients (notably Cursor) return workspace roots as plain absolute
    paths instead of ``file://`` URIs. The MCP SDK then fails pydantic validation
    of ``ListRootsResult`` even though the roots themselves are usable. Extract
    those paths from the validation error payload so file-path tools keep working.

    Which error pydantic reports depends on the path's shape. A POSIX path like
    ``/home/dev/ws`` has no scheme at all and yields ``url_parsing``, but on a
    Windows path like ``C:\\Users\\dev\\ws`` the drive letter parses as a scheme,
    so pydantic gets far enough to reject it as ``url_scheme`` instead. Accept
    both, or the Windows branch below is unreachable.
    """
    errors_fn = getattr(error, "errors", None)
    if not callable(errors_fn):
        return []

    try:
        details = errors_fn()
    except Exception:
        return []

    recovered: List[Path] = []
    for item in details:
        if not isinstance(item, dict):
            continue
        if item.get("type") not in ("url_parsing", "url_scheme"):
            continue
        value = item.get("input")
        if not isinstance(value, str):
            continue
        candidate = value.strip()
        if not (candidate.startswith("/") or (len(candidate) > 2 and candidate[1] == ":")):
            # Unix absolute path, or Windows drive path like C:\...
            continue
        try:
            recovered.append(Path(candidate).expanduser().resolve())
        except Exception:
            continue
    return _dedupe_paths(recovered)
