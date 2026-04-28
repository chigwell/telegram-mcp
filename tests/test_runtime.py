import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from telethon.tl.types import Channel, Chat, PeerUser, User

import main
from telegram_mcp import runtime


def _clear_session_env(monkeypatch):
    for key in list(runtime.os.environ):
        if key.startswith("TELEGRAM_SESSION_STRING") or key.startswith("TELEGRAM_SESSION_NAME"):
            monkeypatch.delenv(key, raising=False)


class _FakeTelegramClient:
    def __init__(self, *args):
        self.args = args


def test_discover_accounts_supports_suffixed_and_default_sessions(monkeypatch):
    _clear_session_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_SESSION_STRING_WORK", "work-session")
    monkeypatch.setenv("TELEGRAM_SESSION_NAME_PERSONAL", "personal.session")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "default-session")
    monkeypatch.setattr(runtime, "TelegramClient", _FakeTelegramClient)
    monkeypatch.setattr(runtime, "StringSession", lambda value: f"StringSession:{value}")

    accounts = runtime._discover_accounts()

    assert sorted(accounts) == ["default", "personal", "work"]
    assert accounts["work"].args[0] == "StringSession:work-session"
    assert accounts["personal"].args[0] == "personal.session"
    assert accounts["default"].args[0] == "StringSession:default-session"


def test_discover_accounts_exits_when_no_sessions_configured(monkeypatch):
    _clear_session_env(monkeypatch)

    with pytest.raises(SystemExit):
        runtime._discover_accounts()


def test_get_client_single_and_multi_account_paths(monkeypatch):
    only = object()
    monkeypatch.setattr(runtime, "clients", {"only": only})
    assert runtime.get_client() is only
    assert runtime.is_multi_mode() is False

    work = object()
    personal = object()
    monkeypatch.setattr(runtime, "clients", {"work": work, "personal": personal})
    assert runtime.is_multi_mode() is True
    assert runtime.get_client("WORK") is work
    with pytest.raises(ValueError, match="Account is required"):
        runtime.get_client()
    with pytest.raises(ValueError, match="Unknown account"):
        runtime.get_client("missing")


@pytest.mark.asyncio
async def test_with_account_routes_single_multi_and_readonly(monkeypatch):
    async def tool(account=None):
        return account or "single"

    monkeypatch.setattr(runtime, "clients", {"default": object()})
    assert await runtime.with_account(readonly=False)(tool)() == "single"

    monkeypatch.setattr(runtime, "clients", {"work": object(), "personal": object()})
    assert await runtime.with_account(readonly=False)(tool)() == (
        "Error: 'account' is required. Available accounts: work, personal"
    )
    assert await runtime.with_account(readonly=False)(tool)(account="work") == "work"
    assert (
        await runtime.with_account(readonly=True)(tool)() == "[work]\nwork\n\n[personal]\npersonal"
    )


class _ConnectivityClient:
    def __init__(self, *, connected=True, authorized=True, ping_error=None):
        self.connected = connected
        self.authorized = authorized
        self.ping_error = ping_error
        self.calls = []

    def is_connected(self):
        self.calls.append("is_connected")
        return self.connected

    async def disconnect(self):
        self.calls.append("disconnect")

    async def connect(self):
        self.calls.append("connect")
        self.connected = True

    async def is_user_authorized(self):
        self.calls.append("is_user_authorized")
        return self.authorized

    async def start(self):
        self.calls.append("start")
        self.authorized = True

    async def __call__(self, _request):
        self.calls.append("ping")
        if self.ping_error:
            raise self.ping_error
        return "ok"


@pytest.mark.asyncio
async def test_ensure_connected_reconnects_disconnected_client(monkeypatch):
    client = _ConnectivityClient(connected=False, authorized=False)
    monkeypatch.setattr(runtime, "_last_conn_verified", {})

    await runtime.ensure_connected(client)

    assert client.calls == ["is_connected", "disconnect", "connect", "is_user_authorized", "start"]
    assert runtime._last_conn_verified[id(client)] > 0


@pytest.mark.asyncio
async def test_ensure_connected_pings_and_reconnects_on_failed_ping(monkeypatch):
    client = _ConnectivityClient(connected=True, authorized=True, ping_error=ConnectionError())
    monkeypatch.setattr(runtime, "_last_conn_verified", {})

    await runtime.ensure_connected(client)

    assert "ping" in client.calls
    assert client.calls[-3:] == ["disconnect", "connect", "is_user_authorized"]


@pytest.mark.asyncio
async def test_ensure_connected_skips_recently_verified_client(monkeypatch):
    client = _ConnectivityClient(connected=True)
    monkeypatch.setattr(runtime, "_last_conn_verified", {id(client): runtime.time.time()})

    await runtime.ensure_connected(client)

    assert client.calls == ["is_connected"]


class _ResolvingClient:
    def __init__(self, method_name, failures):
        self.method_name = method_name
        self.failures = list(failures)
        self.dialogs_loaded = 0

    async def get_dialogs(self):
        self.dialogs_loaded += 1

    async def get_entity(self, identifier):
        return await self._resolve(identifier)

    async def get_input_entity(self, identifier):
        return await self._resolve(identifier)

    async def _resolve(self, identifier):
        if self.failures:
            raise self.failures.pop(0)
        return f"{self.method_name}:{identifier}"


@pytest.mark.asyncio
async def test_resolve_entity_warms_cache_after_value_error(monkeypatch):
    async def noop(_client):
        return None

    client = _ResolvingClient("entity", [ValueError("cold cache")])
    monkeypatch.setattr(runtime, "ensure_connected", noop)

    assert await runtime.resolve_entity("chat", client) == "entity:chat"
    assert client.dialogs_loaded == 1


@pytest.mark.asyncio
async def test_resolve_input_entity_retries_after_connection_error(monkeypatch):
    async def noop(_client):
        return None

    client = _ResolvingClient("input", [ConnectionError(), ValueError("cold cache")])
    monkeypatch.setattr(runtime, "ensure_connected", noop)

    assert await runtime.resolve_input_entity("chat", client) == "input:chat"
    assert client.dialogs_loaded == 1


def test_json_serializer_handles_supported_and_unsupported_values():
    dt = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    assert runtime.json_serializer(dt) == "2026-01-02T03:04:00+00:00"
    assert runtime.json_serializer(b"hello\xff") == "hello�"
    with pytest.raises(TypeError):
        runtime.json_serializer(object())


def test_entity_type_filter_and_formatting_helpers():
    user = User(
        id=1,
        is_self=False,
        contact=False,
        mutual_contact=False,
        deleted=False,
        bot=False,
        bot_chat_history=False,
        bot_nochats=False,
        verified=False,
        restricted=False,
        min=False,
        bot_inline_geo=False,
        support=False,
        scam=False,
        apply_min_photo=False,
        fake=False,
        bot_attach_menu=False,
        premium=False,
        attach_menu_enabled=False,
        bot_can_edit=False,
        close_friend=False,
        stories_hidden=False,
        stories_unavailable=False,
        access_hash=1,
        first_name="John",
        last_name="Doe",
        username="jdoe",
        phone="123",
    )
    chat = Chat(
        id=2, title="Group\x00Name", photo=None, participants_count=3, date=None, version=1
    )
    channel = Channel(
        id=3,
        title="Channel",
        photo=None,
        date=None,
        creator=False,
        left=False,
        broadcast=True,
        verified=False,
        megagroup=False,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=False,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        access_hash=1,
    )
    supergroup = Channel(
        id=4,
        title="Super",
        photo=None,
        date=None,
        creator=False,
        left=False,
        broadcast=False,
        verified=False,
        megagroup=True,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=False,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        access_hash=1,
    )

    assert runtime.get_entity_type(user) == "User"
    assert runtime.get_entity_filter_type(user) == "user"
    assert runtime.get_entity_type(chat) == "Group (Basic)"
    assert runtime.get_entity_filter_type(chat) == "group"
    assert runtime.get_entity_type(channel) == "Channel"
    assert runtime.get_entity_filter_type(channel) == "channel"
    assert runtime.get_entity_type(supergroup) == "Supergroup"
    assert runtime.get_entity_filter_type(supergroup) == "group"
    assert runtime.get_entity_filter_type(object()) is None

    assert runtime.format_entity(user) == {
        "id": 1,
        "name": "John Doe",
        "type": "user",
        "username": "jdoe",
        "phone": "123",
    }
    assert runtime.format_entity(chat) == {"id": 2, "name": "GroupName", "type": "group"}
    assert runtime.format_entity(channel) == {"id": 3, "name": "Channel", "type": "channel"}


def test_message_formatting_sender_and_engagement_helpers():
    message = SimpleNamespace(
        id=42,
        date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        message="hello\x00world",
        from_id=PeerUser(user_id=99),
        media=SimpleNamespace(),
        sender=SimpleNamespace(first_name="Jane", last_name="Doe"),
        views=10,
        forwards=2,
        reactions=SimpleNamespace(results=[SimpleNamespace(count=3), SimpleNamespace(count=None)]),
    )

    formatted = runtime.format_message(message)
    assert formatted["from_id"] == 99
    assert formatted["has_media"] is True
    assert formatted["text"] == "helloworld"
    assert runtime.get_sender_name(message) == "Jane Doe"
    assert runtime.get_sender_name(SimpleNamespace(sender=None)) == "Unknown"
    assert (
        runtime.get_sender_name(SimpleNamespace(sender=SimpleNamespace(title="A\nGroup")))
        == "A Group"
    )
    assert runtime.get_engagement_info(message) == " | views:10, forwards:2, reactions:3"
    assert runtime.get_engagement_dict(message) == {"views": 10, "forwards": 2, "reactions": 3}
    assert runtime.get_engagement_info(SimpleNamespace()) == ""
    assert runtime.get_engagement_dict(SimpleNamespace()) is None


def test_log_and_format_error_returns_custom_and_generated_messages(caplog):
    custom = runtime.log_and_format_error(
        "validate_user",
        runtime.ValidationError("bad"),
        prefix="VALIDATION-001",
        user_message="bad input",
        user_id="abc",
    )
    assert custom == "bad input"

    generated = runtime.log_and_format_error("get_chat", RuntimeError("boom"))
    assert "code: CHAT-ERR-" in generated
    assert "Check mcp_errors.log" in generated


def test_path_helper_edges(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    file_root = root / "allowed.txt"
    file_root.write_text("ok", encoding="utf-8")

    assert runtime._dedupe_paths([root, root, file_root]) == [root, file_root]
    assert runtime._contains_forbidden_path_patterns("   ") == "Path must not be empty."
    assert "wildcard" in runtime._contains_forbidden_path_patterns("*.txt")
    assert runtime._contains_forbidden_path_patterns("safe/name.txt") is None
    with pytest.raises(ValueError, match="Unsupported root URI scheme"):
        runtime._coerce_root_uri_to_path("https://example.com/root")
    assert runtime._coerce_root_uri_to_path(root.as_uri()) == root.resolve()
    assert runtime._path_is_within_root(file_root.resolve(), file_root.resolve()) is True
    assert runtime._path_is_within_root(root.resolve(), file_root.resolve()) is False
    assert runtime._first_resolution_root([file_root.resolve()]) == root.resolve()
    assert runtime._ensure_extension_allowed("send_sticker", root / "bad.txt").startswith(
        "File extension is not allowed"
    )
    assert runtime._ensure_extension_allowed("send_file", root / "any.txt") is None

    too_big = root / "big.bin"
    too_big.write_bytes(b"12345")
    monkeypatch.setitem(runtime.MAX_FILE_BYTES, "tiny_tool", 4)
    assert runtime._ensure_size_within_limit("tiny_tool", too_big).startswith("File is too large")
    assert runtime._ensure_size_within_limit("unknown_tool", too_big) is None


@pytest.mark.asyncio
async def test_more_file_resolution_edges(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    file_path = nested / "file.txt"
    file_path.write_text("ok", encoding="utf-8")
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])

    resolved, error = await runtime._resolve_readable_file_path(
        raw_path="missing.txt", ctx=None, tool_name="send_file"
    )
    assert resolved is None
    assert error == "File not found: missing.txt"

    resolved, error = await runtime._resolve_readable_file_path(
        raw_path="nested", ctx=None, tool_name="send_file"
    )
    assert resolved is None
    assert "Path is not a file" in error

    out_path, error = await runtime._resolve_writable_file_path(
        raw_path="nested/out.bin",
        default_filename="ignored.bin",
        ctx=None,
        tool_name="download_media",
    )
    assert error is None
    assert out_path == (root / "nested" / "out.bin").resolve()

    out_path, error = await runtime._resolve_writable_file_path(
        raw_path="../outside.bin",
        default_filename="ignored.bin",
        ctx=None,
        tool_name="download_media",
    )
    assert out_path is None
    assert error == "Path traversal is not allowed."

    out_path, error = await runtime._resolve_writable_file_path(
        raw_path=str(tmp_path / "outside.bin"),
        default_filename="ignored.bin",
        ctx=None,
        tool_name="download_media",
    )
    assert out_path is None
    assert error == "Path is outside allowed roots."


def test_roots_unsupported_detection():
    assert runtime._is_roots_unsupported_error(NotImplementedError()) is True
    assert runtime._is_roots_unsupported_error(AttributeError("missing list_roots")) is True
    assert runtime._is_roots_unsupported_error(AttributeError("other")) is False
    assert (
        runtime._is_roots_unsupported_error(
            McpError(ErrorData(code=-32000, message="not implemented"))
        )
        is True
    )
    assert runtime._is_roots_unsupported_error(RuntimeError("boom")) is False


def test_configure_allowed_roots_from_cli_updates_runtime_and_main_alias(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()

    runtime._configure_allowed_roots_from_cli([str(root), str(root)])
    assert runtime.SERVER_ALLOWED_ROOTS == [root.resolve()]

    main._configure_allowed_roots_from_cli([str(root)])
    assert main.SERVER_ALLOWED_ROOTS == [root.resolve()]

    with pytest.raises(SystemExit, match="Allowed root does not exist"):
        runtime._configure_allowed_roots_from_cli([str(tmp_path / "missing")])


def test_main_compatibility_wrappers_are_exported():
    assert main.send_message is not None
    assert main.validate_id is runtime.validate_id
    assert main.log_file_path.endswith("mcp_errors.log")
