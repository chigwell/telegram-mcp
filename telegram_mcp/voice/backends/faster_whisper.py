"""faster-whisper backend (universal CPU + NVIDIA CUDA)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import VoiceTranscriptionUnavailable, WhisperBackend


class FasterWhisperBackend(WhisperBackend):
    """CTranslate2-powered Whisper. Works on CPU everywhere; CUDA on NVIDIA."""

    name = "faster_whisper"

    def __init__(self, *, model: str, device: str, cache_dir: Path) -> None:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise VoiceTranscriptionUnavailable(
                "faster-whisper is not installed. Install [voice] extras."
            ) from exc

        compute_type = "float16" if device == "cuda" else "int8"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self.device = device
        self._model: Any = WhisperModel(
            model,
            device=device,
            compute_type=compute_type,
            download_root=str(cache_dir),
        )

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        segments, _info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
