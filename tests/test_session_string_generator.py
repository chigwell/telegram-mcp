import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import session_string_generator


class _NS:
    def __init__(self, expires):
        self.expires = expires


class _FakeQR:
    def __init__(self, wait_outcomes):
        # Each entry is either an exception to raise on wait(), or None to succeed.
        self._wait_outcomes = list(wait_outcomes)
        self.url = "tg://login?token=fake"
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)
        self.recreate_calls = 0

    def wait(self, timeout=None):
        return ("wait", self._wait_outcomes.pop(0))

    def recreate(self):
        self.recreate_calls += 1
        self.expires = datetime.now(timezone.utc) + timedelta(seconds=30)
        return ("recreate", None)


class _FakeLoop:
    def run_until_complete(self, marker):
        kind, outcome = marker
        if kind == "wait" and outcome is not None:
            raise outcome
        return None


class _FakeClient:
    def __init__(self, qr):
        self._qr = qr
        self.loop = _FakeLoop()
        self.disconnected = False
        self.sign_in_calls = []

    def qr_login(self):
        return self._qr

    def sign_in(self, password=None):
        self.sign_in_calls.append(password)

    def disconnect(self):
        self.disconnected = True


def test_seconds_until_expiry_handles_naive_and_aware():
    naive = datetime(2999, 1, 1)
    aware = datetime.now(timezone.utc) + timedelta(seconds=30)

    assert session_string_generator._seconds_until_expiry(_NS(naive)) > 1.0
    assert session_string_generator._seconds_until_expiry(_NS(aware)) > 1.0


def test_seconds_until_expiry_never_below_one():
    past = datetime.now(timezone.utc) - timedelta(seconds=10)

    assert session_string_generator._seconds_until_expiry(_NS(past)) == 1.0


def test_qr_login_returns_on_first_successful_scan():
    qr = _FakeQR([None])
    client = _FakeClient(qr)

    session_string_generator._qr_login(client)

    assert qr.recreate_calls == 0
    assert client.disconnected is False


def test_qr_login_refreshes_after_expiry_then_succeeds():
    qr = _FakeQR([asyncio.TimeoutError(), None])
    client = _FakeClient(qr)

    session_string_generator._qr_login(client)

    assert qr.recreate_calls == 1
    assert client.disconnected is False


def test_qr_login_gives_up_after_max_refreshes(monkeypatch):
    monkeypatch.setattr(session_string_generator, "_QR_MAX_REFRESHES", 3)
    qr = _FakeQR([asyncio.TimeoutError()] * 3)
    client = _FakeClient(qr)

    with pytest.raises(SystemExit):
        session_string_generator._qr_login(client)

    assert qr.recreate_calls == 3
    assert client.disconnected is True


def test_qr_login_uses_hidden_password_prompt_for_2fa(monkeypatch):
    qr = _FakeQR([session_string_generator.errors.SessionPasswordNeededError(request=None)])
    client = _FakeClient(qr)
    prompted = []

    monkeypatch.setattr("getpass.getpass", lambda prompt: prompted.append(prompt) or "secret")

    session_string_generator._qr_login(client)

    assert prompted == ["\nTwo-factor authentication enabled. Please enter your password: "]
    assert client.sign_in_calls == ["secret"]


def test_phone_login_uses_hidden_password_prompt_for_2fa(monkeypatch):
    class _Client:
        def __init__(self):
            self.sign_in_calls = []

        def send_code_request(self, phone):
            self.phone = phone

        def disconnect(self):
            return None

        def sign_in(self, *args, **kwargs):
            if kwargs.get("password") is not None:
                self.sign_in_calls.append(kwargs["password"])
                return None
            raise session_string_generator.errors.SessionPasswordNeededError(request=None)

    client = _Client()
    prompted = []
    entered = iter(["+10000000000", "12345"])

    monkeypatch.setattr("builtins.input", lambda _prompt="": next(entered))
    monkeypatch.setattr("getpass.getpass", lambda prompt: prompted.append(prompt) or "secret")

    session_string_generator._phone_login(client)

    assert client.phone == "+10000000000"
    assert prompted == ["Two-factor authentication enabled. Please enter your password: "]
    assert client.sign_in_calls == ["secret"]


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
