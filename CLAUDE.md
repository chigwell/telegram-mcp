# telegram-mcp — Claude Code Project Memory

## Architecture (Read This First)

This is a Telegram MCP server built on **Telethon** + **FastMCP**.  
Entry points: `main.py` (compat shim) → `telegram_mcp/runner.py` → `telegram_mcp/runtime.py`

### Key files:
- `telegram_mcp/runner.py` — `main()`, `_main()`, `_serve(transport)`, `_configure_transport_security()`
- `telegram_mcp/runtime.py` — ALL shared state, `_configure_allowed_roots_from_cli()` (argparse, line ~2312), `mcp` FastMCP instance, env-var parsing
- `telegram_mcp/tools/` — all 80+ MCP tools (messages.py, chats.py, contacts.py, etc.)
- `main.py` — backward-compatible aliases only, delegates to runner/runtime
- `tests/test_runner.py` — runner integration tests with `_FakeClient`, `_isolate_session_locks` fixture

### Transport (already implemented in runner.py):
- `_serve(transport)` handles "stdio", "http", "sse" — DO NOT reimplement
- `mcp.run_streamable_http_async()` = HTTP, `mcp.run_sse_async()` = SSE, `mcp.run_stdio_async()` = STDIO
- `MCP_TRANSPORT` env var read in `_main()` line 192
- `MCP_HOST` / `MCP_PORT` env vars read in `_serve()` line 145-146

## Dev Commands

```bash
uv run pytest tests/ -v          # Run all tests
uv run pytest tests/test_runner.py -v  # Run runner tests only
uv run black --check .           # Lint (CI enforces this)
uv run black .                   # Auto-format
```

## Code Standards

- Python 3.10+, type hints required on all public functions
- Black formatting, max line length 88
- Use `parse_known_args` NOT `parse_args` in argparse (avoids breaking callers that pass extra flags)
- ALL argparse in runtime: `_configure_allowed_roots_from_cli()` — extend this parser, don't duplicate it
- No new dependencies — pyproject.toml deps are frozen; don't add imports not already in the codebase
- Tests: follow `_FakeClient` pattern, use monkeypatch, no real Telegram connections

## Git Workflow

```bash
git add -A
git commit -S -m "type(scope): description"  # signed commit
uv run pytest tests/ -v         # verify before committing
```

## Critical Constraints

- NEVER break `test_configure_allowed_roots_from_cli_updates_runtime_and_main_alias` test
- NEVER change `_serve()` dispatch logic — it already works
- NEVER add dependencies not already in pyproject.toml
- The `--file` prompt flag in runner.py reads prompt from disk — Claude sees the full prompt text
