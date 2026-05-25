"""OpenVINO backend (best on Intel CPU, Iris Xe iGPU, Arc dGPU)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .base import VoiceTranscriptionUnavailable, WhisperBackend


def _normalize_model_name(model: str) -> str:
    """Accept ``base``, ``whisper-base``, ``whisper-large-v3-turbo`` etc.

    Returns the OpenVINO HF repo's model size segment (e.g. ``base``,
    ``large-v3-turbo``).
    """
    name = model.strip().lower()
    if name.startswith("whisper-"):
        name = name[len("whisper-"):]
    return name


class OpenVINOBackend(WhisperBackend):
    name = "openvino"

    def __init__(self, *, model: str, device: str, cache_dir: Path) -> None:
        try:
            import openvino_genai  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise VoiceTranscriptionUnavailable(
                "openvino-genai is not installed. Install [voice-openvino] extras."
            ) from exc
        try:
            from huggingface_hub import snapshot_download  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise VoiceTranscriptionUnavailable(
                "huggingface_hub is required for the OpenVINO backend."
            ) from exc

        size = _normalize_model_name(model)
        repo = f"OpenVINO/whisper-{size}-fp16-ov"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local = cache_dir / f"whisper-{size}-fp16-ov"
        if not local.exists() or not any(local.glob("openvino_*.xml")):
            snapshot_download(repo_id=repo, local_dir=str(local))

        self.device = device.upper() if device else "CPU"
        self._pipe: Any = openvino_genai.WhisperPipeline(str(local), self.device)
        self._librosa: Optional[Any] = None
        self._np: Optional[Any] = None

    def _load_audio(self, audio_path: Path) -> Any:
        if self._librosa is None:
            try:
                import librosa  # type: ignore
                import numpy as np  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise VoiceTranscriptionUnavailable(
                    "librosa is required for the OpenVINO backend."
                ) from exc
            self._librosa = librosa
            self._np = np
        audio, _ = self._librosa.load(str(audio_path), sr=16000, mono=True)
        return audio.astype(self._np.float32)

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        audio = self._load_audio(audio_path)
        kwargs = {}
        if language:
            kwargs["language"] = f"<|{language}|>"
        result = self._pipe.generate(audio, **kwargs)
        return str(result).strip()
