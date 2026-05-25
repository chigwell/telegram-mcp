"""Whisper backend implementations.

Each backend module is imported lazily via the facade in
`telegram_mcp.voice` so users only pay for the deps they have installed.
"""

from .base import VoiceTranscriptionUnavailable, WhisperBackend

__all__ = ["VoiceTranscriptionUnavailable", "WhisperBackend"]
