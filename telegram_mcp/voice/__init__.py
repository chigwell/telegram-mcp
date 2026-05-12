"""Local Whisper voice transcription with pluggable backends.

Public API:
    transcribe(audio_path, language=None) -> str
    get_backend_info() -> dict   (for introspection)
    VoiceTranscriptionUnavailable

Backend selection is driven by env vars (see config.py) and runtime hardware
detection (see detect.py). Backends are loaded lazily so users only pay for
the deps they have installed.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .backends.base import VoiceTranscriptionUnavailable, WhisperBackend
from .config import WhisperConfig, get_config
from .detect import resolve_backend

logger = logging.getLogger(__name__)

_backend: Optional[WhisperBackend] = None
_backend_lock = threading.Lock()
_warned_cpu = False


def _build_backend(name: str, model: str, device: str, cache_dir: Path) -> WhisperBackend:
    if name == "faster_whisper":
        from .backends.faster_whisper import FasterWhisperBackend

        return FasterWhisperBackend(model=model, device=device, cache_dir=cache_dir)
    if name == "openvino":
        from .backends.openvino import OpenVINOBackend

        return OpenVINOBackend(model=model, device=device, cache_dir=cache_dir)
    if name == "mlx":
        from .backends.mlx import MLXBackend

        return MLXBackend(model=model, device=device, cache_dir=cache_dir)
    raise VoiceTranscriptionUnavailable(f"Unknown whisper backend: {name}")


def _maybe_warn_cpu(cfg: WhisperConfig, device: str) -> None:
    global _warned_cpu
    if _warned_cpu:
        return
    if device.lower() != "cpu":
        return
    if not cfg.warn_cpu:
        return
    logger.warning(
        "whisper_cpu_only: транскрипция работает на CPU без GPU-ускорения — будет долго. "
        "Поставь optional extras для своего железа: "
        "[voice-openvino] (Intel CPU/iGPU/Arc), [voice] с CUDA (NVIDIA), [voice-mlx] (Apple Silicon). "
        "Отключить варнинг: WHISPER_WARN_CPU=false. Отключить транскрипцию: WHISPER_ENABLED=false."
    )
    _warned_cpu = True


def get_backend() -> WhisperBackend:
    """Return the active backend, building it on first access."""
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is not None:
            return _backend
        cfg = get_config()
        if not cfg.enabled:
            raise VoiceTranscriptionUnavailable(
                "Voice transcription is disabled (WHISPER_ENABLED=false)."
            )
        backend_name, device = resolve_backend(cfg)
        _maybe_warn_cpu(cfg, device)
        logger.info(
            "whisper_backend_selected", extra={"backend": backend_name, "device": device, "model": cfg.model}
        )
        _backend = _build_backend(backend_name, cfg.model, device, cfg.cache_dir)
        return _backend


def transcribe(audio_path: Path, language: Optional[str] = None) -> str:
    """Transcribe an audio file. Raises VoiceTranscriptionUnavailable if disabled/unsupported."""
    cfg = get_config()
    lang = language if language is not None else cfg.language
    return get_backend().transcribe(Path(audio_path), language=lang)


def get_backend_info() -> Dict[str, Any]:
    """Return info about the active or to-be-built backend, without forcing instantiation if not yet built."""
    cfg = get_config()
    info: Dict[str, Any] = {
        "enabled": cfg.enabled,
        "backend": cfg.backend,
        "device": cfg.device,
        "model": cfg.model,
        "language": cfg.language,
        "warn_cpu": cfg.warn_cpu,
        "cache_dir": str(cfg.cache_dir),
    }
    if _backend is not None:
        info["active_backend"] = _backend.name
        info["active_device"] = _backend.device
    return info


__all__ = [
    "transcribe",
    "get_backend",
    "get_backend_info",
    "VoiceTranscriptionUnavailable",
]
