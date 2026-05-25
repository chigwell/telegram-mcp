"""Tests for telegram_mcp.voice.config — env-driven WhisperConfig."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def reload_config(monkeypatch):
    """Reload voice.config so env changes take effect for each test."""

    def _reload():
        from telegram_mcp.voice import config as cfg_mod

        importlib.reload(cfg_mod)
        return cfg_mod.get_config()

    return _reload


def _clear(monkeypatch, *names):
    for n in names:
        monkeypatch.delenv(n, raising=False)


def test_defaults_when_no_env(monkeypatch, reload_config):
    _clear(
        monkeypatch,
        "WHISPER_ENABLED",
        "WHISPER_BACKEND",
        "WHISPER_DEVICE",
        "WHISPER_MODEL",
        "WHISPER_LANGUAGE",
        "WHISPER_WARN_CPU",
        "WHISPER_CACHE_DIR",
    )
    cfg = reload_config()
    assert cfg.enabled is True
    assert cfg.backend == "auto"
    assert cfg.device == "auto"
    assert cfg.model == "base"
    assert cfg.language is None
    assert cfg.warn_cpu is True
    assert "whisper" in str(cfg.cache_dir).lower()


def test_disable_via_env(monkeypatch, reload_config):
    monkeypatch.setenv("WHISPER_ENABLED", "false")
    cfg = reload_config()
    assert cfg.enabled is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_bool_truthy(monkeypatch, reload_config, val):
    monkeypatch.setenv("WHISPER_ENABLED", val)
    assert reload_config().enabled is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "anything-else"])
def test_bool_falsey(monkeypatch, reload_config, val):
    monkeypatch.setenv("WHISPER_ENABLED", val)
    assert reload_config().enabled is False


def test_invalid_backend_falls_back_to_auto(monkeypatch, reload_config):
    monkeypatch.setenv("WHISPER_BACKEND", "tensorflow")
    assert reload_config().backend == "auto"


def test_language_auto_alias(monkeypatch, reload_config):
    monkeypatch.setenv("WHISPER_LANGUAGE", "auto")
    assert reload_config().language is None


def test_explicit_language(monkeypatch, reload_config):
    monkeypatch.setenv("WHISPER_LANGUAGE", "ru")
    assert reload_config().language == "ru"


def test_explicit_cache_dir(monkeypatch, tmp_path, reload_config):
    monkeypatch.setenv("WHISPER_CACHE_DIR", str(tmp_path))
    cfg = reload_config()
    assert cfg.cache_dir == tmp_path.resolve()
