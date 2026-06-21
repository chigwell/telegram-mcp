import pytest

import session_string_generator


def test_qr_login_uses_hidden_password_prompt_for_2fa(monkeypatch):
    class _Loop:
        def run_until_complete(self, awaitable):
            awaitable.close()
            raise session_string_generator.errors.SessionPasswordNeededError(request=None)

    class _Client:
        def __init__(self):
            self.loop = _Loop()
            self.sign_in_calls = []

        def qr_login(self):
            class _Qr:
                url = "https://example.test/qr"

                class _Expiry:
                    def strftime(self, _fmt):
                        return "00:00:00"

                expires = _Expiry()

                async def wait(self, timeout=120):
                    return None

            return _Qr()

        def sign_in(self, password=None):
            self.sign_in_calls.append(password)

    class _FakeQrCode:
        def __init__(self, border=1):
            self.border = border

        def add_data(self, _data):
            return None

        def make(self, fit=True):
            return None

        def print_ascii(self, out, invert=True):
            out.write("qr")

    client = _Client()
    prompted = []

    monkeypatch.setattr("getpass.getpass", lambda prompt: prompted.append(prompt) or "secret")
    monkeypatch.setattr("qrcode.QRCode", _FakeQrCode)

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
