"""Participant pagination and failure signaling through the real MCP handler."""

import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolRequest, CallToolRequestParams, ImageContent

from telegram_mcp import runtime
from telegram_mcp.tools import groups


class FakeParticipants:
    def __init__(self, count=4, *, ignore_limit=False, fail_at=None):
        self.count = count
        self.ignore_limit = ignore_limit
        self.fail_at = fail_at
        self.calls = []
        self.yielded = 0
        self.connections = 0

    # Match Telethon's supported arguments; offset would raise TypeError.
    def iter_participants(self, chat_id, *, limit):
        self.calls.append((chat_id, limit))

        async def participants():
            for index in range(self.count):
                if not self.ignore_limit and index >= limit:
                    return
                if index == self.fail_at:
                    raise RuntimeError("private provider detail must not be returned")
                self.yielded += 1
                yield SimpleNamespace(
                    id=index + 1,
                    first_name=f"Person {index + 1}",
                    last_name="",
                    username=None,
                )

        return participants()


@pytest.fixture
def client(monkeypatch, tmp_path):
    client = FakeParticipants()
    monkeypatch.setattr(runtime, "clients", {"fixture": client})
    monkeypatch.setenv("TELEGRAM_ALIASES_FILE", str(tmp_path / "aliases.json"))
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)

    async def connected(selected):
        selected.connections += 1

    monkeypatch.setattr(groups, "ensure_connected", connected)
    return client


async def call_participants(**arguments):
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="get_participants", arguments=arguments),
    )
    handler = runtime.mcp._mcp_server.request_handlers[CallToolRequest]
    return (await handler(request)).root


def text(result):
    return "\n".join(block.text for block in result.content if block.type == "text")


def records(result):
    return json.loads(text(result).split("\n\n")[0])["results"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "count,page,expected,has_more",
    [
        (4, 1, [1, 2], True),
        (4, 2, [3, 4], False),
        (4, 3, [], False),
        (5, 2, [3, 4], True),
        (5, 3, [5], False),
        (0, 1, [], False),
    ],
)
async def test_numbered_pages_and_exhaustion(client, count, page, expected, has_more):
    client.count = count
    result = await call_participants(chat_id=-12345, page=page, page_size=2)

    assert result.isError is False
    assert [p["id"] for p in records(result)] == expected
    assert ("more results available" in text(result)) is has_more
    assert client.calls == [(-12345, (page - 1) * 2 + 3)]
    assert client.connections == 1
    assert all(block.annotations.audience == ["user"] for block in result.content)


@pytest.mark.asyncio
@pytest.mark.parametrize("page", [1, 3])
async def test_basic_group_ignoring_limit_stops_after_lookahead(client, page):
    client.count = 100_000
    client.ignore_limit = True

    result = await call_participants(chat_id=-12345, page=page, page_size=2)

    assert result.isError is False
    assert [p["id"] for p in records(result)] == [2 * page - 1, 2 * page]
    assert client.yielded == (page - 1) * 2 + 3
    assert "more results available" in text(result)


@pytest.mark.asyncio
async def test_maximum_page_retains_only_that_page(client):
    client.count = 1001
    result = await call_participants(chat_id=-12345, page_size=1000)

    assert result.isError is False
    assert len(records(result)) == 1000
    assert client.yielded == 1001
    assert "more results available" in text(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments,expected",
    [
        ({"page": 0}, "page must be at least 1"),
        ({"page": -1}, "page must be at least 1"),
        ({"page_size": 0}, "page_size must be between 1 and 1000"),
        ({"page_size": -1}, "page_size must be between 1 and 1000"),
        ({"page_size": 1001}, "page_size must be between 1 and 1000"),
        ({"chat_id": 2**64}, "out of the valid integer range"),
        ({"chat_id": 1.5}, "validation error"),
        ({"page": "not a number"}, "validation error"),
        ({"account": "missing"}, "An error occurred"),
    ],
)
async def test_invalid_arguments_are_mcp_errors_before_iteration(client, arguments, expected):
    result = await call_participants(**{"chat_id": -12345, **arguments})

    assert result.isError is True
    assert expected in text(result)
    assert client.calls == []
    assert client.connections == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [None, "chosen"])
async def test_library_failure_is_not_empty_or_partial_success(client, monkeypatch, account):
    other = FakeParticipants(count=1)
    if account is not None:
        monkeypatch.setattr(runtime, "clients", {"chosen": client, "other": other})
    client.fail_at = 1
    arguments = {"chat_id": -12345, "page_size": 2}
    if account is not None:
        arguments["account"] = account
    result = await call_participants(**arguments)

    assert result.isError is True
    assert "An error occurred" in text(result)
    assert "private provider detail" not in text(result)
    assert '"results"' not in text(result)
    assert len(client.calls) == 1
    assert other.calls == []


@pytest.mark.asyncio
async def test_connection_failure_is_an_mcp_error_without_iterator_call(client, monkeypatch):
    async def failed_connection(selected):
        raise RuntimeError("private connection detail must not be returned")

    monkeypatch.setattr(groups, "ensure_connected", failed_connection)
    result = await call_participants(chat_id=-12345)

    assert result.isError is True
    assert "private connection detail" not in text(result)
    assert client.calls == []


@pytest.mark.asyncio
async def test_privacy_denial_remains_blocked_and_is_an_mcp_error(client, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-22222")
    result = await call_participants(chat_id=-12345)

    assert result.isError is True
    assert "restricted by privacy policy" in text(result)
    assert client.calls == []
    assert client.connections == 0


@pytest.mark.asyncio
async def test_unknown_alias_keeps_ask_user_payload_and_never_reads(client):
    result = await call_participants(chat_id="a wholly unknown contact")

    assert result.isError is True
    assert "unknown_contact" in text(result)
    assert "set_contact_alias" in text(result)
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chat_id,normalized", [("-12345", -12345), ("@sample_group", "@sample_group")]
)
async def test_valid_identifiers_keep_normalization(client, chat_id, normalized):
    result = await call_participants(chat_id=chat_id)

    assert result.isError is False
    assert client.calls == [(normalized, 201)]


@pytest.mark.asyncio
async def test_explicit_account_does_not_fall_back_to_another(client, monkeypatch):
    other = FakeParticipants(count=1)
    monkeypatch.setattr(runtime, "clients", {"chosen": client, "other": other})

    result = await call_participants(chat_id=-12345, account="chosen")

    assert result.isError is False
    assert len(records(result)) == 4
    assert other.calls == []


@pytest.mark.asyncio
async def test_readonly_account_fanout_is_preserved(client, monkeypatch):
    other = FakeParticipants(count=1)
    monkeypatch.setattr(runtime, "clients", {"chosen": client, "other": other})

    result = await call_participants(chat_id=-12345)

    assert result.isError is False
    assert "[chosen]" in text(result) and "[other]" in text(result)
    assert len(client.calls) == len(other.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_account", ["chosen", "other"])
async def test_readonly_fanout_retains_success_when_one_account_fails(
    client, monkeypatch, failing_account
):
    other = FakeParticipants(count=1)
    accounts = {"chosen": client, "other": other}
    accounts[failing_account].fail_at = 0
    monkeypatch.setattr(runtime, "clients", accounts)

    result = await call_participants(chat_id=-12345)

    assert result.isError is False
    success_account = "other" if failing_account == "chosen" else "chosen"
    chosen_text, other_text = text(result).removeprefix("[chosen]\n").split("\n\n[other]\n", 1)
    sections = {"chosen": chosen_text, "other": other_text}
    successful = sections[success_account]
    failed = sections[failing_account]
    assert (
        len(json.loads(successful.split("\n\n")[0])["results"]) == accounts[success_account].count
    )
    assert "An error occurred" in failed
    assert "private provider detail" not in failed
    assert len(client.calls) == len(other.calls) == 1


@pytest.mark.asyncio
async def test_readonly_fanout_all_failures_are_labelled_mcp_errors(client, monkeypatch):
    client.fail_at = 0
    other = FakeParticipants(fail_at=0)
    monkeypatch.setattr(runtime, "clients", {"chosen": client, "other": other})

    result = await call_participants(chat_id=-12345)

    assert result.isError is True
    assert "[chosen]\n" in text(result) and "[other]\n" in text(result)
    assert text(result).count("An error occurred") == 2
    assert "private provider detail" not in text(result)
    assert len(client.calls) == len(other.calls) == 1


@pytest.mark.asyncio
async def test_fanout_tool_error_does_not_stringify_successful_image(client, monkeypatch):
    monkeypatch.setattr(runtime, "clients", {"chosen": client, "other": FakeParticipants()})
    image = ImageContent(type="image", data="fixture", mimeType="image/png")

    @runtime.with_account(readonly=True)
    async def content(account=None):
        if account == "other":
            raise ToolError("Account unavailable")
        return [image]

    assert await content() == ["[chosen]", image, "[other]", "Account unavailable"]


@pytest.mark.asyncio
async def test_fanout_does_not_catch_other_exception_types(client, monkeypatch):
    monkeypatch.setattr(runtime, "clients", {"chosen": client, "other": FakeParticipants()})

    @runtime.with_account(readonly=True)
    async def unexpected_failure(account=None):
        if account == "other":
            raise RuntimeError("Unexpected failure")
        return "Roster"

    with pytest.raises(RuntimeError, match="Unexpected failure"):
        await unexpected_failure()


@pytest.mark.asyncio
async def test_opt_in_validation_handles_lists_and_default_behavior_stays_unchanged():
    @runtime.validate_id("user_ids", raise_errors=True)
    async def strict(user_ids):
        return user_ids

    @runtime.validate_id("user_ids")
    async def legacy(user_ids):
        return user_ids

    with pytest.raises(ToolError, match="out of the valid integer range"):
        await strict(user_ids=[1, 2**64])
    assert "out of the valid integer range" in await legacy(user_ids=[1, 2**64])


@pytest.mark.asyncio
async def test_tool_schema_and_readonly_annotation_are_preserved(client):
    tool = next(tool for tool in await runtime.mcp.list_tools() if tool.name == "get_participants")

    assert tool.annotations.readOnlyHint is True
    assert set(tool.inputSchema["properties"]) == {"chat_id", "page", "page_size", "account"}
