"""Tests for telegram_mcp.voice public facade."""

from __future__ import annotations

import importlib

import pytest


def _reset_voice_state():
    """Force reset the singleton + warning latch between tests."""
    from telegram_mcp import voice as voice_mod

    voice_mod._backend = None
    voice_mod._warned_cpu = False


def test_disabled_raises(monkeypatch):
    monkeypatch.setenv("WHISPER_ENABLED", "false")
    from telegram_mcp.voice import config as cfg_mod

    importlib.reload(cfg_mod)
    from telegram_mcp import voice as voice_mod

    importlib.reload(voice_mod)
    _reset_voice_state()
    with pytest.raises(voice_mod.VoiceTranscriptionUnavailable):
        voice_mod.get_backend()


def test_get_backend_info_keys():
    from telegram_mcp import voice as voice_mod

    info = voice_mod.get_backend_info()
    assert {"enabled", "backend", "device", "model", "language", "warn_cpu", "cache_dir"} <= info.keys()
