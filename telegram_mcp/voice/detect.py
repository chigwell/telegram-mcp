"""Auto-detect best available whisper backend + device for the current host."""

from __future__ import annotations

import importlib.util
import logging
import platform
from typing import List, Tuple

from .backends.base import VoiceTranscriptionUnavailable
from .config import WhisperConfig

logger = logging.getLogger(__name__)


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine().lower() in ("arm64", "aarch64")


def _detect_cuda() -> bool:
    """Return True iff CUDA is usable for faster-whisper."""
    if _has("torch"):
        try:
            import torch  # type: ignore

            return bool(torch.cuda.is_available())
        except Exception:
            pass
    if _has("ctranslate2"):
        try:
            import ctranslate2  # type: ignore

            count = getattr(ctranslate2, "get_cuda_device_count", lambda: 0)()
            return count > 0
        except Exception:
            pass
    return False


def _detect_openvino_gpus() -> List[str]:
    """Return OpenVINO GPU device names like ['GPU.0', 'GPU.1'], or []."""
    if not _has("openvino"):
        return []
    try:
        import openvino as ov  # type: ignore

        core = ov.Core()
        return [d for d in core.available_devices if d.upper().startswith("GPU")]
    except Exception as exc:  # pragma: no cover — driver/runtime issues
        logger.debug("openvino_device_probe_failed", extra={"err": str(exc)})
        return []


def _pick_openvino_device(gpus: List[str], requested: str) -> str:
    """Resolve user-requested device against the available OpenVINO GPUs."""
    req = requested.upper()
    if req in {d.upper() for d in gpus}:
        for d in gpus:
            if d.upper() == req:
                return d
    if req == "GPU" and gpus:
        # Prefer the highest-indexed GPU (typically discrete on dual-GPU systems).
        return sorted(gpus, key=lambda d: int(d.split(".")[-1]) if "." in d else 0)[-1]
    if req == "CPU":
        return "CPU"
    if gpus:
        return sorted(gpus, key=lambda d: int(d.split(".")[-1]) if "." in d else 0)[-1]
    return "CPU"


def resolve_backend(cfg: WhisperConfig) -> Tuple[str, str]:
    """Decide (backend_name, device_name) for the active config.

    Honors explicit cfg.backend / cfg.device when set, otherwise auto-detects.
    Raises VoiceTranscriptionUnavailable if nothing usable is installed.
    """
    backend = cfg.backend
    device = cfg.device

    # Explicit backend pin.
    if backend == "mlx":
        if not _has("mlx_whisper"):
            raise VoiceTranscriptionUnavailable(
                "WHISPER_BACKEND=mlx but 'mlx_whisper' is not installed. "
                "Install [voice-mlx] extras (Apple Silicon only)."
            )
        return "mlx", "mps"

    if backend == "openvino":
        if not _has("openvino_genai"):
            raise VoiceTranscriptionUnavailable(
                "WHISPER_BACKEND=openvino but 'openvino_genai' is not installed. "
                "Install [voice-openvino] extras."
            )
        gpus = _detect_openvino_gpus()
        chosen = _pick_openvino_device(gpus, device if device != "auto" else "GPU")
        return "openvino", chosen

    if backend == "faster_whisper":
        if not _has("faster_whisper"):
            raise VoiceTranscriptionUnavailable(
                "WHISPER_BACKEND=faster_whisper but 'faster_whisper' is not installed. "
                "Install [voice] extras."
            )
        if device == "auto":
            device = "cuda" if _detect_cuda() else "cpu"
        return "faster_whisper", device

    # backend == "auto" — pick the best available accelerator.
    if _is_apple_silicon() and _has("mlx_whisper"):
        return "mlx", "mps"

    if _has("faster_whisper") and (device in ("auto", "cuda") and _detect_cuda()):
        return "faster_whisper", "cuda"

    if _has("openvino_genai"):
        gpus = _detect_openvino_gpus()
        if gpus and device != "cpu":
            chosen = _pick_openvino_device(gpus, device if device != "auto" else "GPU")
            return "openvino", chosen

    if _has("faster_whisper"):
        return "faster_whisper", "cpu"

    if _has("openvino_genai"):
        # OpenVINO without a GPU is still a fine CPU backend.
        return "openvino", "CPU"

    raise VoiceTranscriptionUnavailable(
        "No whisper backend installed. Install one of: "
        "[voice] (faster-whisper), [voice-openvino] (Intel), [voice-mlx] (Apple Silicon)."
    )
