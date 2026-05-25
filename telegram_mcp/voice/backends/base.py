"""Whisper backend ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class VoiceTranscriptionUnavailable(RuntimeError):
    """Transcription backend is missing, disabled, or hardware not present."""


class WhisperBackend(ABC):
    """Abstract whisper transcription backend.

    Subclasses pick one of: ``faster_whisper`` (universal CPU + NVIDIA CUDA),
    ``openvino`` (Intel CPU/iGPU/Arc), ``mlx`` (Apple Silicon).
    """

    name: str = "<base>"
    device: str = "cpu"

    @abstractmethod
    def __init__(self, *, model: str, device: str, cache_dir: Path) -> None: ...

    @abstractmethod
    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> str:
        """Transcribe an audio file to text. ``audio_path`` must point to an existing file."""
