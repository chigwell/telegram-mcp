"""Regression tests for privacy-safe Telegram MCP error logging."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from telegram_mcp import runtime
from telegram_mcp.tools import folders, groups, media, profile


def test_error_log_omits_sensitive_context_exception_text_and_traceback(caplog):
    try:
        raise RuntimeError("exception-payload-secret")
    except RuntimeError as error:
        with caplog.at_level("ERROR", logger=runtime.logger.name):
            result = runtime.log_and_format_error(
                "send_message",
                error,
                chat_id=-100123456,
                message="message-secret",
                caption="caption-secret",
                transcript="transcript-secret",
                provider_payload={"text": "provider-secret"},
                file_path="/private/local-path-secret.txt",
            )

    assert result.startswith("An error occurred (code: GEN-ERR-")
    assert "Telegram MCP operation failed" in caplog.text
    for secret in (
        "exception-payload-secret",
        "-100123456",
        "message-secret",
        "caption-secret",
        "transcript-secret",
        "provider-secret",
        "local-path-secret",
        "Traceback",
    ):
        assert secret not in caplog.text


def test_flood_wait_log_keeps_seconds_but_omits_sensitive_payloads(caplog):
    error = runtime.FloodWaitError(request=None, capture=45)
    error.args = ("exception-provider-payload-secret",)

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        result = runtime.log_and_format_error(
            "transcribe_voice",
            error,
            chat_id=-100987654,
            transcript="transcript-secret",
            provider_payload={"text": "provider-secret"},
            file_path="/private/audio-secret.ogg",
        )

    assert "45 seconds" in result
    assert "Do NOT retry immediately" in result
    assert "Telegram FloodWait" in caplog.text
    for secret in (
        "exception-provider-payload-secret",
        "-100987654",
        "transcript-secret",
        "provider-secret",
        "audio-secret",
    ):
        assert secret not in caplog.text


def _logger_call(node):
    if not isinstance(node, ast.Call):
        return None
    call = node
    if not isinstance(call.func, ast.Attribute):
        return None
    if not isinstance(call.func.value, ast.Name) or call.func.value.id != "logger":
        return None
    return call


def _persistent_log_call(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
        return None
    return node


def _is_leaking_log_statement(statement):
    if not isinstance(statement, ast.Expr):
        return False
    call = _logger_call(statement.value)
    if call is None:
        return False
    if call.func.attr == "exception":
        return True
    return call.func.attr == "error" and any(
        keyword.arg == "exc_info"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in call.keywords
    )


def _returns_formatted_error(statement):
    return isinstance(statement, ast.Return) and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "log_and_format_error"
        for node in ast.walk(statement)
    )


def test_tool_handlers_do_not_log_sensitive_exceptions_before_formatting():
    tools_dir = Path(runtime.__file__).parent / "tools"
    leaking_sites = []

    for source_path in sorted(tools_dir.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for parent in ast.walk(tree):
            for _, value in ast.iter_fields(parent):
                if not isinstance(value, list):
                    continue
                for current, following in zip(value, value[1:]):
                    if _is_leaking_log_statement(current) and _returns_formatted_error(following):
                        leaking_sites.append(f"{source_path.name}:{current.lineno}")

    assert leaking_sites == []


def _runtime_and_tool_sources():
    package_dir = Path(runtime.__file__).parent
    return package_dir, [
        package_dir / "runtime.py",
        *sorted((package_dir / "tools").glob("*.py")),
    ]


def test_all_runtime_and_tool_logs_use_constant_privacy_safe_messages():
    package_dir, source_paths = _runtime_and_tool_sources()
    unsafe = []

    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            call = _persistent_log_call(node)
            if call is None:
                continue
            # exception() always persists an implicit traceback. Every other log
            # call must not accept arbitrary values through %-args, f-strings,
            # or keyword fields; a literal message is the enforceable boundary.
            literal_only = (
                call.func.attr != "exception"
                and len(call.args) == 1
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
                and not call.keywords
            )
            if not literal_only:
                unsafe.append(f"{source_path.relative_to(package_dir)}:{node.lineno}")

    assert unsafe == []


def test_all_runtime_and_tool_exception_handlers_do_not_return_raw_exceptions():
    package_dir, source_paths = _runtime_and_tool_sources()
    unsafe = []

    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
            if not handler.name:
                continue
            body = ast.Module(body=handler.body, type_ignores=[])
            for node in ast.walk(body):
                if not isinstance(node, ast.Return) or node.value is None:
                    continue
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                    if node.value.func.id == "log_and_format_error":
                        continue
                exposes_exception = any(
                    isinstance(value, ast.Name) and value.id == handler.name
                    for value in ast.walk(node.value)
                )
                if exposes_exception:
                    unsafe.append(f"{source_path.relative_to(package_dir)}:{node.lineno}")

    assert unsafe == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [media.list_photos, media.get_photo_sheet])
async def test_unknown_photo_source_response_omits_caller_value(tool):
    result = await tool(source="secret\nTraceback: /private/provider/path", chat_id=123)

    assert result == "Unknown photo source. Expected one of: avatars, messages."
    assert "secret" not in result
    assert "Traceback" not in result
    assert "/private/provider/path" not in result


def test_unreadable_alias_store_omits_path_and_parser_details(tmp_path, monkeypatch, caplog):
    secret_path = tmp_path / "alias-path-secret.json"
    secret_path.write_text("{ alias-json-secret", encoding="utf-8")
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(secret_path))

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        with pytest.raises(runtime.AliasStoreUnreadable) as excinfo:
            runtime.load_aliases(strict=True)

    assert "no changes were written" in str(excinfo.value).lower()
    for secret in ("alias-path-secret", "alias-json-secret", "Expecting property name"):
        assert secret not in caplog.text
        assert secret not in str(excinfo.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_enabled", [False, True])
async def test_roots_failure_log_omits_exception_payload_and_traceback(
    tmp_path, monkeypatch, caplog, fallback_enabled
):
    class Session:
        async def list_roots(self):
            raise RuntimeError("roots-exception-secret")

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])
    if fallback_enabled:
        monkeypatch.setenv("TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK", "1")
    else:
        monkeypatch.delenv("TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK", raising=False)

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        await runtime._get_effective_allowed_roots_with_status(SimpleNamespace(session=Session()))

    assert "MCP roots request failed" in caplog.text
    assert "roots-exception-secret" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_join_chat_unknown_error_uses_sanitized_response_and_log(monkeypatch, caplog):
    class Client:
        async def __call__(self, request):
            raise RuntimeError("join-exception-secret")

    async def connected(client):
        return None

    monkeypatch.setattr(groups, "get_client", lambda account=None: Client())
    monkeypatch.setattr(groups, "ensure_connected", connected)

    with caplog.at_level("ERROR", logger=runtime.logger.name):
        result = await groups.join_chat_by_link(link="https://t.me/+invite-link-secret")

    assert result.startswith("An error occurred (code:")
    for secret in ("join-exception-secret", "invite-link-secret", "Traceback"):
        assert secret not in result
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_profile_resolution_warning_omits_identifier_and_exception(monkeypatch, caplog):
    class Client:
        async def __call__(self, request):
            return SimpleNamespace()

    async def fail_resolution(user_id, client):
        raise RuntimeError("profile-exception-secret")

    monkeypatch.setattr(profile, "get_client", lambda account=None: Client())
    monkeypatch.setattr(profile, "resolve_entity", fail_resolution)

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        result = await profile.set_privacy_settings(
            key="status", allow_users=["@profile_user_secret"]
        )

    assert result == "Privacy settings for status updated successfully."
    assert "profile-exception-secret" not in caplog.text
    assert "profile_user_secret" not in caplog.text


@pytest.mark.asyncio
async def test_folder_resolution_error_omits_identifier_and_exception(monkeypatch):
    class Client:
        async def __call__(self, request):
            return SimpleNamespace(filters=[])

    async def fail_resolution(chat_id, client):
        raise RuntimeError("folder-exception-secret")

    monkeypatch.setattr(folders, "get_client", lambda account=None: Client())
    monkeypatch.setattr(folders, "resolve_input_entity", fail_resolution)

    result = await folders.create_folder(title="safe title", chat_ids=["folder-chat-secret"])

    assert result.startswith("Failed to resolve a requested chat")
    assert "folder-exception-secret" not in result
    assert "folder-chat-secret" not in result


@pytest.mark.asyncio
async def test_gif_fallback_error_omits_exception_and_query_payload(monkeypatch):
    class Client:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            if self.calls == 1:
                raise AttributeError("force fallback")
            raise RuntimeError("exception-payload-secret")

    async def connected(client):
        return None

    monkeypatch.setattr(
        media.functions.messages,
        "SearchGifsRequest",
        lambda **kwargs: "primary-request",
        raising=False,
    )
    monkeypatch.setattr(media, "get_client", lambda account: Client())
    monkeypatch.setattr(media, "ensure_connected", connected)

    result = await media.get_gif_search(query="gif-query-secret")

    assert result.startswith("An error occurred (code:")
    assert "exception-payload-secret" not in result
    assert "gif-query-secret" not in result
