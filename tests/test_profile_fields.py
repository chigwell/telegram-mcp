import pytest

from telegram_mcp.tools import profile


class _Username:
    def __init__(self, username):
        self.username = username


class _RestrictionReason:
    def __init__(self, text):
        self.text = text


class _EntityPhoto:
    def __init__(self, photo_id):
        self.photo_id = photo_id


class _User:
    def __init__(self, **attributes):
        self.username = attributes.pop("username", None)
        for name, value in attributes.items():
            setattr(self, name, value)


class _FullUser:
    def __init__(self, **attributes):
        for name, value in attributes.items():
            setattr(self, name, value)


def test_additional_usernames_exclude_the_primary_one():
    user = _User(
        username="example_user",
        usernames=[_Username("example_user"), _Username("example_alias"), _Username("")],
    )

    assert profile._additional_usernames(user) == ["example_alias"]


def test_additional_usernames_are_empty_without_collectibles():
    assert profile._additional_usernames(_User(username="solo")) == []


def test_current_avatar_id_comes_from_the_entity_without_an_extra_call():
    assert profile._current_avatar_id(_User(photo=_EntityPhoto(1820385360168986609))) == (
        1820385360168986609
    )


def test_current_avatar_id_is_none_when_no_avatar_is_set():
    assert profile._current_avatar_id(_User()) is None


def test_trust_flags_only_report_what_is_set():
    assert profile._trust_flags(_User(scam=True, fake=False)) == {"scam": True}


def test_trust_flags_are_empty_for_an_ordinary_account():
    assert profile._trust_flags(_User()) == {}


def test_restriction_reasons_are_sanitized(monkeypatch):
    monkeypatch.setattr(profile, "sanitize_user_content", lambda text, max_length: "SANITIZED")
    user = _User(restricted=True, restriction_reason=[_RestrictionReason("spam")])

    flags = profile._trust_flags(user)

    assert flags["restricted"] is True
    assert flags["restriction_reasons"] == ["SANITIZED"]


def test_relationship_flags_report_mutual_contacts():
    flags = profile._relationship_flags(_User(contact=True, mutual_contact=True))

    assert flags == {"contact": True, "mutual_contact": True}


def test_business_profile_is_empty_for_a_personal_account():
    assert profile._business_profile(_FullUser()) == {}


def test_business_profile_surfaces_location_hours_and_intro(monkeypatch):
    monkeypatch.setattr(profile, "sanitize_user_content", lambda text, max_length: text)
    full_user = _FullUser(
        business_location=type("Location", (), {"address": "Via Bovisa 3, Milano"})(),
        business_work_hours=type("Hours", (), {"timezone_id": "Europe/Rome"})(),
        business_intro=type("Intro", (), {"title": "Studio", "description": "By appointment"})(),
    )

    assert profile._business_profile(full_user) == {
        "location": "Via Bovisa 3, Milano",
        "timezone": "Europe/Rome",
        "intro_title": "Studio",
        "intro_description": "By appointment",
    }
