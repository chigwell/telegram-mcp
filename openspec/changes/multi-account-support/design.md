## Context

The server (`main.py`, ~4800 lines) initializes a single global `TelegramClient` at module level (line 101-106) from environment variables. All 60+ MCP tools reference this global `client` directly. Configuration lives in `.env` with three variables: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_SESSION_STRING` (or `TELEGRAM_SESSION_NAME`).

## Goals / Non-Goals

**Goals:**
- Support 2+ simultaneous Telegram accounts with user-defined labels
- Zero breakage for single-account users (config and tool behavior unchanged)
- Read-only tools fan out to all accounts when `account` is omitted
- Write tools require explicit account selection
- Minimal per-tool code changes via a shared decorator

**Non-Goals:**
- Dynamic account add/remove at runtime (requires server restart)
- Per-account `API_ID`/`API_HASH` overrides (one Telegram app for all accounts)
- Account-level permission/access control
- Merging or deduplicating contacts across accounts

## Decisions

### 1. Client registry as a plain dict

**Decision:** `clients: dict[str, TelegramClient]` at module level, replacing the single `client` global.

**Alternatives considered:**
- Class-based `AccountManager` with methods — adds indirection with no real benefit; a dict + a few helper functions is simpler.
- Keep global `client` and add a second one — doesn't scale, creates ad-hoc naming.

**Rationale:** A dict keyed by label is the simplest structure that supports N accounts. Helper function `get_client(account: str) -> TelegramClient` handles lookup and error messaging.

### 2. `@with_account` decorator for tool injection

**Decision:** A decorator that:
- Adds `account: str = None` to the tool's MCP signature.
- For `readonly=True` tools: if `account` is None and multi-mode is active, calls the wrapped function once per client, prefixes each result with `[label]`, and concatenates.
- For `readonly=False` tools: if `account` is None in multi-mode, returns an error asking the LLM to specify an account.
- In single-mode: always resolves to the sole client, `account` parameter is effectively ignored.
- Injects resolved `TelegramClient` as the `client` keyword argument.

**Alternatives considered:**
- Manual edits to each tool without decorator — same effect but 60+ places to get wrong, harder to change behavior later.
- Context-based injection via MCP Context — Context is already used for file-path security (Roots API); overloading it for client selection is confusing.

**Rationale:** The decorator centralizes the account-resolution logic. Each tool change is mechanical: add decorator, replace bare `client` with `client` parameter.

### 3. Label-suffixed `.env` variables for configuration

**Decision:** Multi-account config via env var suffix convention:
```
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=abcdef
TELEGRAM_SESSION_STRING_WORK=...
TELEGRAM_SESSION_STRING_PERSONAL=...
```

Detection logic at startup:
1. Scan env vars matching `TELEGRAM_SESSION_STRING_*` and `TELEGRAM_SESSION_NAME_*`.
2. If suffixed vars found → multi-mode. Labels extracted from suffixes, lowercased.
3. If only unsuffixed `TELEGRAM_SESSION_STRING`/`TELEGRAM_SESSION_NAME` found → single-mode, label = `"default"`.
4. If both suffixed and unsuffixed exist → unsuffixed becomes label `"default"`, combined with suffixed ones into multi-mode.
5. If nothing found → startup error.

**Alternatives considered:**
- Separate YAML/JSON config file — requires new dependency or new file format; breaks existing single-account setups.
- Numbered suffixes (`_1`, `_2`) — requires a separate label variable per account; less self-documenting.

**Rationale:** Stays within `.env` convention. Existing single-account configs work unchanged. Labels are self-documenting. No new files or formats.

### 4. Per-account entity cache warming

**Decision:** At startup, call `await cl.get_dialogs()` for each client in the registry. Entity resolution functions (`resolve_entity`, `resolve_input_entity`) take a `client` parameter instead of using the global.

**Rationale:** StringSession has no persistent entity cache. Each client needs its own warm cache. The existing pattern stays the same, just parameterized.

### 5. Output tagging with `[label]` prefix

**Decision:** When a read-only tool fans out to all accounts, each account's result block is prefixed with `[label]` on its own line, followed by the tool's normal output. In single-mode, no prefix is added.

**Rationale:** Simple, readable by both humans and LLMs. The LLM can parse the label to determine which account context to use for subsequent write operations.

## Risks / Trade-offs

**[Increased startup time]** → Each additional account adds one `client.start()` + `get_dialogs()` call. For 2-3 accounts this is a few seconds. Mitigation: start clients concurrently with `asyncio.gather`.

**[Larger tool signatures]** → Every tool gains an `account` parameter. For single-account users this is invisible (default = None, resolved to sole client). For multi-account users, LLMs handle it naturally via context.

**[Mechanical refactor of 60+ tools]** → High volume of changes but each change is small and formulaic. Risk of missed tools or typos. Mitigation: grep for bare `client.` references after refactor to catch stragglers.

**[Telethon concurrency]** → Multiple TelegramClient instances are independent and safe to run concurrently — each has its own connection and state. No shared-state issues. Fan-out reads use `asyncio.gather` for parallel execution.
