"""Installation provenance checks for the Telegram MCP server.

The PyPI distribution name ``telegram-mcp`` is currently occupied by an
unrelated project. This guard can only protect executions that reach this
repository's code; it cannot run when a user launches the third-party package.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from urllib.parse import unquote, urlparse

DISTRIBUTION_NAME = "telegram-mcp"


class UnsafeInstallationError(RuntimeError):
    """Raised when installed package metadata points at the wrong project."""


@dataclass(frozen=True)
class DistributionIdentity:
    """Small, testable view of Python package metadata."""

    name: str
    version: str
    authors: tuple[str, ...] = ()
    maintainers: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    summary: str = ""
    direct_url: str = ""

    @classmethod
    def from_distribution(cls, dist: metadata.Distribution) -> "DistributionIdentity":
        package_metadata = dist.metadata
        urls = tuple(package_metadata.get_all("Project-URL") or ())
        homepage = package_metadata.get("Home-page")
        if homepage:
            urls += (homepage,)

        authors = tuple(
            value
            for value in (
                package_metadata.get("Author"),
                package_metadata.get("Author-email"),
            )
            if value
        )
        maintainers = tuple(
            value
            for value in (
                package_metadata.get("Maintainer"),
                package_metadata.get("Maintainer-email"),
            )
            if value
        )

        read_text = getattr(dist, "read_text", None)
        direct_url = read_text("direct_url.json") if callable(read_text) else ""

        return cls(
            name=package_metadata.get("Name") or getattr(dist, "name", DISTRIBUTION_NAME),
            version=package_metadata.get("Version") or dist.version,
            authors=authors,
            maintainers=maintainers,
            urls=urls,
            summary=package_metadata.get("Summary", ""),
            direct_url=direct_url,
        )


def _project_root_declares_distribution_name(path: Path) -> bool:
    pyproject_path = path / "pyproject.toml"
    if not pyproject_path.is_file():
        return False

    try:
        pyproject_text = pyproject_path.read_text(encoding="utf-8")
    except OSError:
        return False

    return f'name = "{DISTRIBUTION_NAME}"' in pyproject_text


def _direct_url_json(direct_url: str) -> dict:
    if not direct_url:
        return {}

    try:
        direct_url_data = json.loads(direct_url)
    except json.JSONDecodeError:
        return {}

    return direct_url_data if isinstance(direct_url_data, dict) else {}


def _direct_url_is_explicit_source_install(direct_url: str) -> bool:
    direct_url_data = _direct_url_json(direct_url)
    if not direct_url_data:
        return False

    raw_url = str(direct_url_data.get("url", "")).strip()
    if not raw_url:
        return False

    parsed_url = urlparse(raw_url)

    if parsed_url.scheme == "file":
        source_path = Path(unquote(parsed_url.path)).resolve()
        return _project_root_declares_distribution_name(source_path)

    vcs_info = direct_url_data.get("vcs_info")
    return isinstance(vcs_info, dict) and bool(vcs_info.get("vcs"))


def _looks_like_explicit_source_install(identity: DistributionIdentity) -> bool:
    return _direct_url_is_explicit_source_install(identity.direct_url)


def _format_unsafe_installation_message(identity: DistributionIdentity) -> str:
    authors = ", ".join(identity.authors) or "unknown"
    maintainers = ", ".join(identity.maintainers) or "unknown"
    urls = "; ".join(identity.urls) or "unknown"

    return (
        "Refusing to start: the installed 'telegram-mcp' distribution was not "
        "installed from an explicit source checkout.\n"
        f"Detected distribution: name={identity.name!r}, version={identity.version!r}, "
        f"authors={authors!r}, maintainers={maintainers!r}, urls={urls!r}.\n"
        "The 'telegram-mcp' name on PyPI is currently owned by a different project. "
        "This guard requires a source checkout or installer-recorded direct "
        "git/file URL. Run this server from a cloned checkout or install a "
        "trusted repository explicitly with: "
        'pip install "git+https://github.com/chigwell/telegram-mcp.git"'
    )


def assert_safe_distribution(distribution_name: str = DISTRIBUTION_NAME) -> None:
    """Abort when the installed distribution metadata is not this project.

    Source checkouts that have not been installed as a distribution are allowed:
    ``uv --directory /path/to/telegram-mcp run main.py`` can run from source
    without package metadata. If metadata for ``telegram-mcp`` is present, it
    must come from an explicit git or file install recorded by the installer.
    """

    try:
        dist = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return

    identity = DistributionIdentity.from_distribution(dist)
    if _looks_like_explicit_source_install(identity):
        return

    raise UnsafeInstallationError(_format_unsafe_installation_message(identity))
