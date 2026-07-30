"""Tests for favorite contact aliases."""

from telegram_mcp import runtime


def _use_tmp_aliases(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "_ALIASES_FILE", tmp_path / "aliases.json")


def test_apply_alias_returns_saved_id(monkeypatch, tmp_path):
    _use_tmp_aliases(monkeypatch, tmp_path)
    runtime.save_aliases({"андрей": 12345})

    assert runtime.apply_alias("андрей") == 12345
    assert runtime.apply_alias("Андрей") == 12345
    assert runtime.apply_alias("@андрей") == 12345
    assert runtime.apply_alias(" андрей ") == 12345


def test_apply_alias_passes_through_unknown_values(monkeypatch, tmp_path):
    _use_tmp_aliases(monkeypatch, tmp_path)
    runtime.save_aliases({"андрей": 12345})

    assert runtime.apply_alias("bob") == "bob"
    assert runtime.apply_alias(678) == 678


def test_load_aliases_missing_or_corrupt_file(monkeypatch, tmp_path):
    _use_tmp_aliases(monkeypatch, tmp_path)
    assert runtime.load_aliases() == {}

    (tmp_path / "aliases.json").write_text("not json")
    assert runtime.load_aliases() == {}


def test_save_and_load_roundtrip_lowercases_keys(monkeypatch, tmp_path):
    _use_tmp_aliases(monkeypatch, tmp_path)
    runtime.save_aliases({"Работа": -1001234567890})

    assert runtime.load_aliases() == {"работа": -1001234567890}
