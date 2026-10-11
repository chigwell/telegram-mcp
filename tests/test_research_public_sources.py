"""Exercise the runnable example through the SDK's HTTP MCP client."""

import importlib.util
import json
from pathlib import Path
from uuid import UUID

import httpx
import pytest

EXAMPLE = Path(__file__).parents[1] / "skills/examples/research-public-sources.py"
spec = importlib.util.spec_from_file_location("research_public_sources", EXAMPLE)
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


@pytest.fixture
def mcp_servers(monkeypatch):
    requests = []
    state = {"missing": False, "error": False}

    def handle(request):
        body = json.loads(request.content) if request.content else {}
        requests.append((request, body))
        if request.method == "DELETE" or "id" not in body:
            return httpx.Response(202)
        method = body["method"]
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fixture", "version": "1"},
            }
        elif method == "tools/list":
            names = ["get_message_context", "web_search", "web_fetch"]
            result = {
                "tools": (
                    []
                    if state["missing"]
                    else [{"name": name, "inputSchema": {"type": "object"}} for name in names]
                )
            }
        elif method == "tools/call":
            text = (
                "PRIVATE_CHAT_SENTINEL"
                if body["params"]["name"] == "get_message_context"
                else '{"results":[{"url":"https://redis.io/","excerpts":["Public docs"]}]}'
            )
            result = {"content": [{"type": "text", "text": text}], "isError": state["error"]}
        else:
            raise AssertionError(method)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    original_client = httpx.AsyncClient

    def client(**kwargs):
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(example.httpx, "AsyncClient", client)
    return requests, state


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["--public-query", "--public-url"])
async def test_full_workflow_keeps_chat_out_of_public_requests(mcp_servers, mode):
    value = "Redis connection pool limits" if mode == "--public-query" else "https://redis.io/"
    args = example.parse_args(
        [mode, value, "--chat-id", "@private_group", "--message-id", "123", "--account", "work"]
    )
    output = await example.research(args)
    assert output["telegram_context"]["content"][0]["text"] == "PRIVATE_CHAT_SENTINEL"
    assert "Public docs" in output["public_sources"]["content"][0]["text"]
    requests, _ = mcp_servers
    calls = [(r, b) for r, b in requests if b.get("method") == "tools/call"]
    assert len(calls) == 2
    telegram, public = calls
    assert str(telegram[0].url) == "http://127.0.0.1:8765/mcp"
    assert telegram[1]["params"]["arguments"] == {
        "chat_id": "@private_group",
        "message_id": 123,
        "context_size": 3,
        "account": "work",
    }
    assert str(public[0].url) == example.PARALLEL_URL
    public_args = public[1]["params"]["arguments"]
    UUID(public_args.pop("session_id"))
    if mode == "--public-query":
        assert public[1]["params"]["name"] == "web_search"
        assert public_args == {"objective": value, "search_queries": [value]}
    else:
        assert public[1]["params"]["name"] == "web_fetch"
        assert public_args == {"urls": [value]}
    for request, body in requests:
        assert request.headers["User-Agent"] == example.USER_AGENT
        assert "authorization" not in request.headers
        if request.url.host == "search.parallel.ai":
            assert "PRIVATE_CHAT_SENTINEL" not in json.dumps(body)
            assert "@private_group" not in json.dumps(body)
            assert "work" not in json.dumps(body)


@pytest.mark.asyncio
async def test_public_only_ignores_saved_and_environment_credentials(mcp_servers, monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "SECRET_KEY_SENTINEL")
    monkeypatch.setenv("TELEGRAM_SESSION_STRING", "SECRET_SESSION_SENTINEL")
    output = await example.research(example.parse_args(["--public-query", "Redis pool limits"]))
    assert set(output) == {"public_sources"}
    for request, body in mcp_servers[0]:
        assert request.url.host == "search.parallel.ai"
        assert "authorization" not in request.headers
        assert "SECRET" not in json.dumps(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "error"])
async def test_telegram_mcp_failure_stops_before_public_call(mcp_servers, failure):
    requests, state = mcp_servers
    state[failure] = True
    args = example.parse_args(
        ["--public-query", "Redis pool limits", "--chat-id", "@group", "--message-id", "123"]
    )
    with pytest.raises(Exception):
        await example.research(args)
    assert all(request.url.host == "127.0.0.1" for request, _ in requests)
    assert sum(body.get("method") == "tools/call" for _, body in requests) <= 1


@pytest.mark.parametrize(
    "extra",
    [["--chat-id", "@group"], ["--message-id", "123"], ["--account", "work"]],
)
def test_incomplete_telegram_selection_is_rejected(extra):
    with pytest.raises(SystemExit):
        example.parse_args(["--public-query", "Redis pool limits", *extra])


@pytest.mark.parametrize("url", ["file:///tmp/secret", "https://", "https://user:secret@host/"])
def test_non_public_url_forms_are_rejected(url):
    with pytest.raises(SystemExit):
        example.parse_args(["--public-url", url])
