## Why

The Telegram MCP server currently supports only a single Telegram account per server instance. Users who maintain separate work and personal accounts need to interact with both simultaneously throughout the day — reading messages from all accounts at once and replying from the correct one based on context. Running multiple server instances is not viable: tool names collide, and shared contacts across accounts lead to messages sent from the wrong identity.

## What Changes

- **Multi-client registry**: Replace the global singleton `TelegramClient` with a `dict[str, TelegramClient]` keyed by user-defined labels (e.g., `"work"`, `"personal"`).
- **Account-aware tools**: Add an `account` parameter to all 60+ MCP tools. Read-only tools default to querying all accounts (results concatenated with `[label]` prefix). Write tools require an explicit `account` value.
- **Backward-compatible configuration**: Extend `.env` to support labeled session strings (`TELEGRAM_SESSION_STRING_WORK`, `TELEGRAM_SESSION_STRING_PERSONAL`) alongside the existing unsuffixed `TELEGRAM_SESSION_STRING` for single-account mode.
- **Single-account mode preservation**: When only the unsuffixed `TELEGRAM_SESSION_STRING` is present, the server behaves exactly as today — `account` parameter is optional everywhere.
- **New `list_accounts` tool**: Exposes available accounts with labels and profile info so the LLM can orient itself.
- **Per-account entity caches**: Each `TelegramClient` maintains its own entity cache, warmed at startup.

## Capabilities

### New Capabilities
- `multi-account`: Account registry, client lifecycle, configuration parsing, account resolution logic, and the `list_accounts` tool.
- `account-aware-tools`: Decorator-based account injection into existing tools, including read-only fan-out and write-tool validation.

### Modified Capabilities

## Impact

- **main.py**: All 60+ tool functions gain the `@with_account` decorator and replace direct `client` references with an injected `client` parameter. Startup sequence initializes multiple clients.
- **Configuration**: `.env` format extended (backward-compatible). `.env.example` updated with multi-account examples.
- **session_string_generator.py**: May need updates to label generated sessions.
- **No breaking changes** for single-account users — existing `.env` files and tool call signatures continue to work unchanged.
