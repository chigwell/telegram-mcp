from telegram_mcp.client_identity import client_identity_kwargs


def _clear_device_env(monkeypatch):
    for key in ("TELEGRAM_DEVICE_MODEL", "TELEGRAM_SYSTEM_VERSION", "TELEGRAM_APP_VERSION"):
        monkeypatch.delenv(key, raising=False)


def test_client_identity_kwargs_empty_when_unset(monkeypatch):
    _clear_device_env(monkeypatch)

    assert client_identity_kwargs() == {}


def test_client_identity_kwargs_maps_all_variables(monkeypatch):
    _clear_device_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_DEVICE_MODEL", "Telegram MCP")
    monkeypatch.setenv("TELEGRAM_SYSTEM_VERSION", "2.0")
    monkeypatch.setenv("TELEGRAM_APP_VERSION", "3.1")

    assert client_identity_kwargs() == {
        "device_model": "Telegram MCP",
        "system_version": "2.0",
        "app_version": "3.1",
    }


def test_client_identity_kwargs_ignores_empty_values(monkeypatch):
    _clear_device_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_DEVICE_MODEL", "Telegram MCP")
    monkeypatch.setenv("TELEGRAM_SYSTEM_VERSION", "")

    assert client_identity_kwargs() == {"device_model": "Telegram MCP"}
