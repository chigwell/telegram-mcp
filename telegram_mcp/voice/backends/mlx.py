"""mlx-whisper backend (Apple Silicon)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import VoiceTranscriptionUnavailable, WhisperBackend


class MLXBackend(WhisperBackend):
    name = "mlx"

    def __init__(self, *, model: str, device: str, cache_dir: Path) -> None:
        try:
            import mlx_whisper  # type: ignore  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise VoiceTranscriptionUnavailable(
                "mlx-whisper is not installed. Install [voice-mlx] extras (Apple Silicon only)."
            ) from exc

        size = model.strip().lower()
        if size.startswith("whisper-"):
            size = size[len("whisper-"):]
        # The mlx-community publishes pre-converted MLX weights on HF.
        self._repo = f"mlx-community/whisper-{size}-mlx"
        self.device = device or "mps"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = cache_dir

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        import mlx_whisper  # type: ignore

        result: Any = mlx_whisper.transcribe(
            str(audio_path),
            path_or_hf_repo=self._repo,
            language=language,
        )
        return str(result.get("text", "")).strip()
