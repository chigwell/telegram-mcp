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


def test_inflections_are_suggested_but_never_sent_to_silently():
    # Fuzzy matching suggests; only an exact key resolves. "лена"/"леня" has the
    # same shape as a declension pair, so a silent fuzzy send could reach the
    # wrong person whenever the intended one is not saved yet.
    runtime.save_aliases({"андрей бекендер": {"id": 111, "name": "Андрей"}})

    for wording in ("Андрею бекендеру", "бекендер андрей", "бекендеру"):
        assert runtime.apply_alias(wording) == wording
        assert [alias for alias, _ in runtime.match_aliases(wording)] == ["андрей бекендер"]

    assert runtime.apply_alias("андрей бекендер") == 111  # the saved wording is exact


def test_near_miss_name_never_resolves_silently():
    runtime.save_aliases({"леня": {"id": 222, "name": "Леонид"}})
    assert runtime.apply_alias("Лена") == "Лена"  # Елена is not Леонид

    runtime.save_aliases({"иван": {"id": 100}})
    assert runtime.apply_alias("Иванов") == "Иванов"  # -ов is a surname, not an ending

    runtime.save_aliases({"максим": {"id": 777}})
    assert runtime.apply_alias("макс") == "макс"


def test_single_lookalike_asks_for_confirmation_not_identification():
    runtime.save_aliases({"леня": {"id": 222, "name": "Леонид"}})

    payload = json.loads(runtime.alias_ask_payload("Лена"))

    assert payload["error"] == "confirm_contact"
    assert payload["candidates"] == [{"alias": "леня", "id": 222, "name": "Леонид"}]
    assert "do NOT" in payload["instruction"]


def test_extra_query_word_cannot_reuse_a_matched_alias_word():
    # Both query words used to land on the single stored token, so the surname
    # naming a different Андрей was ignored.
    runtime.save_aliases({"андрей": {"id": 111, "name": "Андрей Петров"}})

    assert runtime.apply_alias("андрей андреев") == "андрей андреев"
    assert runtime.match_aliases("андрей андреев") == []


def test_every_query_token_must_match():
    # "игорь" alone would match, but "смирнов" lands nowhere — asking beats
    # sending to the wrong Igor.
    runtime.save_aliases({"чикичев игорь": 719969066})

    assert runtime.apply_alias("игорь смирнов") == "игорь смирнов"


def test_ambiguous_reference_never_resolves():
    runtime.save_aliases({"андрей бекендер": {"id": 111}, "андрей смирнов": {"id": 222}})

    assert runtime.apply_alias("андрей") == "андрей"
    assert len(runtime.match_aliases("андрей")) == 2


def test_same_id_under_two_aliases_is_a_confirmation_not_a_choice():
    runtime.save_aliases({"андрей бекендер": {"id": 111}, "бекендер": {"id": 111}})

    payload = json.loads(runtime.alias_ask_payload("бекендеру"))
    assert payload["error"] == "confirm_contact"  # two aliases, one person
    assert {c["id"] for c in payload["candidates"]} == {111}


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


# The whole safety of fuzzy matching rests on these two lists: an inflection of the
# same name must match, a different person's name must not. Pinned as a table so a
# threshold tweak cannot silently start misrouting messages.
INFLECTIONS = [
    ("андрею", "андрей"),
    ("игорю", "игорь"),
    ("марии", "мария"),
    ("бекендеру", "бекендер"),
    ("контакту", "контакт"),
    ("мертвому", "мертвый"),  # adjectives swap 3 chars, not 1
    ("главному", "главный"),
    ("старшему", "старший"),
    ("лена", "лене"),  # short names swap a single character
    ("саша", "сашу"),
    ("иван", "ивана"),
    ("александру", "александр"),
]

DIFFERENT_PEOPLE = [
    ("артем", "артур"),
    ("макс", "марк"),
    ("ольга", "олег"),
    ("олег", "олеся"),  # looks exactly like an inflection, is not
    ("анна", "антон"),
    ("иван", "игорь"),
    ("смирнов", "сидоров"),
    ("бекендер", "фронтендер"),
    ("дима", "дина"),
    ("инна", "инга"),
    ("вера", "вероника"),
    ("владимир", "владислав"),
    ("сергей", "сергеевич"),
]


@pytest.mark.parametrize("query,stored", INFLECTIONS)
def test_same_word_accepts_inflections(query, stored):
    assert runtime._same_word(query, stored)


@pytest.mark.parametrize("query,stored", DIFFERENT_PEOPLE)
def test_same_word_rejects_different_people(query, stored):
    assert not runtime._same_word(query, stored)


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
    assert payload["error"] == "ambiguous_contact"  # matched too many, not "unknown"
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


def test_update_aliases_does_not_lose_a_concurrent_write():
    runtime.save_aliases({"первый": 1})

    runtime.update_aliases(lambda a: a.__setitem__("второй", {"id": 2}))
    runtime.update_aliases(lambda a: a.__setitem__("третий", {"id": 3}))

    assert _ids(runtime.load_aliases()) == {"первый": 1, "второй": 2, "третий": 3}


def test_update_aliases_refuses_to_write_over_an_unreadable_file():
    path = runtime.aliases_file_path()
    runtime.save_aliases({"важный": 1})
    os.chmod(path, 0o000)
    try:
        with pytest.raises(runtime.AliasStoreUnreadable):
            runtime.update_aliases(lambda a: a.__setitem__("новый", {"id": 2}))
    finally:
        os.chmod(path, 0o600)

    assert _ids(runtime.load_aliases()) == {"важный": 1}  # nothing destroyed


def test_wrong_shaped_json_is_quarantined():
    path = runtime.aliases_file_path()
    path.write_text('["not", "an", "object"]', encoding="utf-8")

    runtime.save_aliases({"андрей": 1})

    assert list(path.parent.glob("aliases.corrupt-*"))


def test_temp_file_name_is_unpredictable():
    runtime.save_aliases({"андрей": 1})
    path = runtime.aliases_file_path()

    assert not path.with_suffix(".tmp").exists()
    assert not list(path.parent.glob("*.tmp"))  # nothing left behind


def test_non_string_name_in_file_does_not_raise():
    runtime.aliases_file_path().write_text('{"андрей": {"id": 1, "name": 42}}', encoding="utf-8")

    assert runtime.load_aliases()["андрей"]["name"] == "42"


def test_handle_shaped_key_in_the_file_cannot_hijack_a_real_identifier():
    # Legacy files may already contain such keys; resolution must ignore them.
    runtime.save_aliases({"me": 555, "artemis": 556})

    assert runtime.apply_alias("me") == "me"
    assert runtime.apply_alias("artemis") == "artemis"


def test_alias_id_keeps_the_wording_behind_a_resolved_id():
    # @validate_id substitutes the id before a tool body runs; without the wording
    # a stale mapping could only be reported as an opaque number.
    value = runtime.AliasID(111, "андрею бекендеру")

    assert value == 111 and isinstance(value, int)
    assert runtime.alias_wording(value) == "андрею бекендеру"
    assert json.loads(json.dumps({"id": value}))["id"] == 111


def test_alias_wording_ignores_real_identifiers():
    assert runtime.alias_wording("андрей бекендер") == "андрей бекендер"
    assert runtime.alias_wording("@artemis") is None  # a real handle, not a nickname
    assert runtime.alias_wording(12345) is None


def test_alias_failure_reports_stale_for_a_resolved_id():
    runtime.save_aliases({"мертвый контакт": {"id": 999, "name": "Gone"}})

    stale = json.loads(
        runtime.alias_failure(runtime.AliasID(999, "мертвому контакту"), 999).payload
    )
    assert stale["error"] == "stale_contact"
    assert stale["reference"] == "мертвому контакту"  # never the bare id

    unknown = json.loads(runtime.alias_failure("кто-то", "кто-то").payload)
    assert unknown["error"] == "unknown_contact"

    assert runtime.alias_failure("@artemis", "@artemis") is None  # not an alias problem


@pytest.mark.asyncio
async def test_resolver_turns_a_dead_peer_into_a_repoint_request(monkeypatch):
    # A dead peer answers with an RPC error, not a ValueError — that must still
    # reach the agent as "your saved contact is stale", not a generic failure.
    import telethon

    class _Client:
        async def get_entity(self, identifier):
            raise telethon.errors.rpcerrorlist.ChatIdInvalidError(request=None)

        async def get_dialogs(self):
            return []

    monkeypatch.setattr(runtime, "ensure_connected", lambda client: _noop())
    runtime.save_aliases({"мертвый контакт": {"id": 999}})

    with pytest.raises(runtime.AliasNeedsUser) as excinfo:
        await runtime.resolve_entity(runtime.AliasID(999, "мертвому контакту"), _Client())

    payload = json.loads(excinfo.value.payload)
    assert payload["error"] == "stale_contact"
    assert "replace=True" in payload["instruction"]


async def _noop():
    return None


def test_unreadable_alias_file_does_not_raise(monkeypatch, tmp_path):
    path = runtime.aliases_file_path()
    path.write_text("{}", encoding="utf-8")
    os.chmod(path, 0o000)
    try:
        assert runtime.load_aliases() == {}  # degraded, never an exception
    finally:
        os.chmod(path, 0o600)
