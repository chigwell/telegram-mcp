"""Tests for favorite contact aliases: storage, matching, and the ask-the-user loop."""

import json
import os
import stat

import pytest

from telegram_mcp import runtime


@pytest.fixture(autouse=True)
def _tmp_aliases(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(tmp_path / "aliases.json"))
    monkeypatch.delenv("TELEGRAM_CONTACT_FUZZY", raising=False)
    yield


def _ids(aliases):
    return {alias: record["id"] for alias, record in aliases.items()}


def test_apply_alias_returns_saved_id():
    runtime.save_aliases({"андрей": 12345})

    assert runtime.apply_alias("андрей") == 12345
    assert runtime.apply_alias("Андрей") == 12345
    assert runtime.apply_alias("@андрей") == 12345
    assert runtime.apply_alias(" андрей ") == 12345


def test_apply_alias_passes_through_unknown_values():
    runtime.save_aliases({"андрей": 12345})

    assert runtime.apply_alias("bob") == "bob"
    assert runtime.apply_alias(678) == 678


def test_load_aliases_missing_or_corrupt_file(tmp_path):
    assert runtime.load_aliases() == {}

    path = runtime.aliases_file_path()
    path.write_text("not json")
    assert runtime.load_aliases() == {}

    path.write_text('["a list, not an object"]')
    assert runtime.load_aliases() == {}

    path.write_text('{"ok": 5, "bad": {"id": "not-an-int"}}')
    assert _ids(runtime.load_aliases()) == {"ok": 5}  # bad row skipped, good row kept


def test_save_and_load_roundtrip_normalizes_keys():
    runtime.save_aliases({"Работа": -1001234567890})

    assert _ids(runtime.load_aliases()) == {"работа": -1001234567890}


def test_legacy_flat_file_is_upgraded_on_read():
    runtime.save_aliases({"чикичев игорь": 719969066})
    records = runtime.load_aliases()

    assert records["чикичев игорь"] == {"id": 719969066, "name": None, "account": None}


def test_alias_key_folds_yo_case_and_whitespace():
    assert runtime.alias_key("  Пётр  Первый ") == "петр первый"
    assert runtime.alias_key("@Андрей") == "андрей"


def test_matching_tolerates_russian_case_endings_and_word_order():
    runtime.save_aliases({"андрей бекендер": {"id": 111, "name": "Андрей"}})

    assert runtime.apply_alias("Андрею бекендеру") == 111
    assert runtime.apply_alias("бекендер андрей") == 111
    assert runtime.apply_alias("бекендеру") == 111  # a tag word alone is enough


def test_every_query_token_must_match():
    # "игорь" alone would match, but "смирнов" lands nowhere — asking beats
    # sending to the wrong Igor.
    runtime.save_aliases({"чикичев игорь": 719969066})

    assert runtime.apply_alias("игорь смирнов") == "игорь смирнов"


def test_ambiguous_reference_never_resolves():
    runtime.save_aliases({"андрей бекендер": {"id": 111}, "андрей смирнов": {"id": 222}})

    assert runtime.apply_alias("андрей") == "андрей"
    assert len(runtime.match_aliases("андрей")) == 2


def test_same_id_under_two_aliases_is_not_ambiguous():
    runtime.save_aliases({"андрей бекендер": {"id": 111}, "бекендер": {"id": 111}})

    assert runtime.apply_alias("бекендер андрей") == 111


def test_handle_shaped_input_skips_fuzzy():
    # Alias "артем js" must not hijack the real @artemis account.
    runtime.save_aliases({"артем js": 809133446})

    assert runtime.apply_alias("artemis") == "artemis"
    assert runtime.apply_alias("@artemis") == "@artemis"
    assert runtime.apply_alias("me") == "me"
    assert runtime.apply_alias("+79990000000") == "+79990000000"


def test_fuzzy_kill_switch(monkeypatch):
    runtime.save_aliases({"андрей бекендер": {"id": 111}})
    monkeypatch.setenv("TELEGRAM_CONTACT_FUZZY", "0")

    assert runtime.apply_alias("Андрею бекендеру") == "Андрею бекендеру"
    assert runtime.apply_alias("андрей бекендер") == 111  # exact still works


def test_same_word_rejects_look_alike_names():
    assert runtime._same_word("андрею", "андрей")
    assert runtime._same_word("бекендеру", "бекендер")
    assert not runtime._same_word("артем", "артур")
    assert not runtime._same_word("макс", "марк")


def test_ask_payload_is_actionable_json():
    runtime.save_aliases({"андрей бекендер": {"id": 111, "name": "Андрей"}})

    unknown = json.loads(runtime.alias_ask_payload("кто-то новый"))
    assert unknown["error"] == "unknown_contact"
    assert unknown["nothing_sent"] is True
    assert "set_contact_alias" in unknown["instruction"]
    assert "андрей бекендер" in unknown["known_aliases"]

    stale = json.loads(runtime.alias_ask_payload("андрей бекендер", kind="stale", stored_id=111))
    assert stale["error"] == "stale_contact"
    assert "replace=True" in stale["instruction"]


def test_ask_payload_lists_candidates_when_ambiguous():
    runtime.save_aliases(
        {"андрей бекендер": {"id": 111, "name": "A"}, "андрей смирнов": {"id": 222, "name": "B"}}
    )

    payload = json.loads(runtime.alias_ask_payload("андрей"))
    assert {c["id"] for c in payload["candidates"]} == {111, 222}
    assert "which one" in payload["instruction"]


def test_alias_file_is_owner_only_and_written_atomically():
    runtime.save_aliases({"андрей": 1})
    path = runtime.aliases_file_path()

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not path.with_suffix(".tmp").exists()


def test_corrupt_file_is_quarantined_not_overwritten():
    path = runtime.aliases_file_path()
    path.write_text("{ broken", encoding="utf-8")

    runtime.save_aliases({"андрей": 1})

    assert _ids(runtime.load_aliases()) == {"андрей": 1}
    quarantined = list(path.parent.glob("aliases.corrupt-*"))
    assert quarantined and quarantined[0].read_text(encoding="utf-8") == "{ broken"


def test_default_path_is_runtime_state_not_install_dir(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEGRAM_ALIASES_FILE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert runtime.aliases_file_path() == tmp_path / "telegram-mcp" / "aliases.json"


def test_legacy_install_dir_file_still_readable(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"старый": 42}), encoding="utf-8")
    monkeypatch.delenv("TELEGRAM_ALIASES_FILE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(runtime, "_LEGACY_ALIASES_FILE", legacy)

    assert _ids(runtime.load_aliases()) == {"старый": 42}


def test_is_handle_like():
    assert runtime.is_handle_like("artemis")
    assert runtime.is_handle_like("@artemis")
    assert runtime.is_handle_like("me")
    assert runtime.is_handle_like("+79990000000")
    assert runtime.is_handle_like("-1001234567890")
    assert not runtime.is_handle_like("андрей бекендер")
    assert not runtime.is_handle_like("bob")  # too short for a username


def test_unreadable_alias_file_does_not_raise(monkeypatch, tmp_path):
    path = runtime.aliases_file_path()
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o000)
    try:
        assert runtime.load_aliases() == {}  # degraded, never an exception
    finally:
        os.chmod(path, 0o600)
