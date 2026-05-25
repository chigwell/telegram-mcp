"""Whisper config from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _str_env(name: str, default: str) -> str:
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


def _opt_str_env(name: str) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw or raw.lower() in ("auto", "none", ""):
        return None
    return raw


def _default_cache_dir() -> Path:
    explicit = os.getenv("WHISPER_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = os.getenv("XDG_CACHE_HOME")
    if base:
        return Path(base).expanduser().resolve() / "telegram-mcp" / "whisper"
    return Path.home() / ".cache" / "telegram-mcp" / "whisper"


@dataclass(frozen=True)
class WhisperConfig:
    """Resolved Whisper settings from env vars."""

    enabled: bool
    backend: str  # "auto" | "faster_whisper" | "openvino" | "mlx"
    device: str  # "auto" | "cpu" | "cuda" | "gpu" | "gpu.0" | "gpu.1" | ...
    model: str  # "tiny" | "base" | "small" | "medium" | "large-v3" | "large-v3-turbo" | ...
    language: Optional[str]  # ISO 639-1 ("ru", "en", ...) or None for auto-detect
    warn_cpu: bool
    cache_dir: Path


_VALID_BACKENDS = frozenset({"auto", "faster_whisper", "openvino", "mlx"})


def get_config() -> WhisperConfig:
    backend = _str_env("WHISPER_BACKEND", "auto").lower()
    if backend not in _VALID_BACKENDS:
        backend = "auto"
    return WhisperConfig(
        enabled=_bool_env("WHISPER_ENABLED", True),
        backend=backend,
        device=_str_env("WHISPER_DEVICE", "auto").lower(),
        model=_str_env("WHISPER_MODEL", "base"),
        language=_opt_str_env("WHISPER_LANGUAGE"),
        warn_cpu=_bool_env("WHISPER_WARN_CPU", True),
        cache_dir=_default_cache_dir(),
    )
