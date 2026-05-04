"""Tests for telegram_mcp.voice.detect — backend selection logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from telegram_mcp.voice import detect
from telegram_mcp.voice.backends.base import VoiceTranscriptionUnavailable
from telegram_mcp.voice.config import WhisperConfig


def _cfg(backend="auto", device="auto") -> WhisperConfig:
    return WhisperConfig(
        enabled=True,
        backend=backend,
        device=device,
        model="base",
        language=None,
        warn_cpu=False,
        cache_dir=Path("/tmp/wh"),
    )


def test_no_backends_installed_raises(monkeypatch):
    monkeypatch.setattr(detect, "_has", lambda _m: False)
    with pytest.raises(VoiceTranscriptionUnavailable):
        detect.resolve_backend(_cfg())


def test_explicit_mlx_without_install_raises(monkeypatch):
    monkeypatch.setattr(detect, "_has", lambda m: m != "mlx_whisper")
    with pytest.raises(VoiceTranscriptionUnavailable):
        detect.resolve_backend(_cfg(backend="mlx"))


def test_explicit_openvino_without_install_raises(monkeypatch):
    monkeypatch.setattr(detect, "_has", lambda m: m != "openvino_genai")
    with pytest.raises(VoiceTranscriptionUnavailable):
        detect.resolve_backend(_cfg(backend="openvino"))


def test_explicit_faster_whisper_without_install_raises(monkeypatch):
    monkeypatch.setattr(detect, "_has", lambda m: m != "faster_whisper")
    with pytest.raises(VoiceTranscriptionUnavailable):
        detect.resolve_backend(_cfg(backend="faster_whisper"))


def test_auto_picks_mlx_on_apple_silicon(monkeypatch):
    monkeypatch.setattr(detect, "_is_apple_silicon", lambda: True)
    monkeypatch.setattr(detect, "_has", lambda m: m == "mlx_whisper")
    name, dev = detect.resolve_backend(_cfg())
    assert name == "mlx"
    assert dev == "mps"


def test_auto_picks_cuda_when_available(monkeypatch):
    monkeypatch.setattr(detect, "_is_apple_silicon", lambda: False)
    monkeypatch.setattr(detect, "_has", lambda m: m == "faster_whisper")
    monkeypatch.setattr(detect, "_detect_cuda", lambda: True)
    name, dev = detect.resolve_backend(_cfg())
    assert name == "faster_whisper"
    assert dev == "cuda"


def test_auto_picks_openvino_gpu_over_cpu(monkeypatch):
    monkeypatch.setattr(detect, "_is_apple_silicon", lambda: False)
    monkeypatch.setattr(detect, "_detect_cuda", lambda: False)
    monkeypatch.setattr(detect, "_has", lambda m: m in ("openvino_genai", "faster_whisper"))
    monkeypatch.setattr(detect, "_detect_openvino_gpus", lambda: ["GPU.0", "GPU.1"])
    name, dev = detect.resolve_backend(_cfg())
    assert name == "openvino"
    assert dev == "GPU.1"  # highest-indexed → typically the discrete GPU


def test_auto_falls_back_to_faster_whisper_cpu(monkeypatch):
    monkeypatch.setattr(detect, "_is_apple_silicon", lambda: False)
    monkeypatch.setattr(detect, "_detect_cuda", lambda: False)
    monkeypatch.setattr(detect, "_has", lambda m: m == "faster_whisper")
    monkeypatch.setattr(detect, "_detect_openvino_gpus", lambda: [])
    name, dev = detect.resolve_backend(_cfg())
    assert name == "faster_whisper"
    assert dev == "cpu"


def test_explicit_device_cpu_skips_openvino_gpu(monkeypatch):
    monkeypatch.setattr(detect, "_is_apple_silicon", lambda: False)
    monkeypatch.setattr(detect, "_detect_cuda", lambda: False)
    monkeypatch.setattr(detect, "_has", lambda m: m in ("openvino_genai", "faster_whisper"))
    monkeypatch.setattr(detect, "_detect_openvino_gpus", lambda: ["GPU.0", "GPU.1"])
    name, dev = detect.resolve_backend(_cfg(device="cpu"))
    # User asked for cpu — should pick faster-whisper-cpu, not openvino-GPU.
    assert name == "faster_whisper"
    assert dev == "cpu"


def test_pick_openvino_device_specific(monkeypatch):
    chosen = detect._pick_openvino_device(["GPU.0", "GPU.1"], "GPU.0")
    assert chosen == "GPU.0"


def test_pick_openvino_device_generic_gpu_picks_highest(monkeypatch):
    chosen = detect._pick_openvino_device(["GPU.0", "GPU.1", "GPU.2"], "GPU")
    assert chosen == "GPU.2"


def test_pick_openvino_device_no_gpus_returns_cpu(monkeypatch):
    chosen = detect._pick_openvino_device([], "GPU")
    assert chosen == "CPU"
