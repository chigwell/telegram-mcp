import pytest

import session_string_generator


def test_parse_args_qr_selects_qr_login(monkeypatch):
    monkeypatch.setattr("sys.argv", ["session_string_generator.py", "--qr"])

    args = session_string_generator._parse_args()

    assert args.qr is True
    assert args.phone is False


def test_parse_args_phone_selects_phone_login(monkeypatch):
    monkeypatch.setattr("sys.argv", ["session_string_generator.py", "--phone"])

    args = session_string_generator._parse_args()

    assert args.qr is False
    assert args.phone is True


def test_parse_args_without_flags_keeps_interactive_login_choice(monkeypatch):
    monkeypatch.setattr("sys.argv", ["session_string_generator.py"])

    args = session_string_generator._parse_args()

    assert args.qr is False
    assert args.phone is False


def test_parse_args_rejects_conflicting_login_modes(monkeypatch):
    monkeypatch.setattr("sys.argv", ["session_string_generator.py", "--qr", "--phone"])

    with pytest.raises(SystemExit):
        session_string_generator._parse_args()
