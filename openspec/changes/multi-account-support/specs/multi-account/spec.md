## ADDED Requirements

### Requirement: Configuration parsing supports labeled accounts
The server SHALL parse `.env` variables matching `TELEGRAM_SESSION_STRING_<LABEL>` and `TELEGRAM_SESSION_NAME_<LABEL>` to discover multiple accounts. Labels SHALL be extracted from the suffix and lowercased. A shared `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` SHALL be used for all accounts.

#### Scenario: Multi-account config with two labeled sessions
- **WHEN** `.env` contains `TELEGRAM_SESSION_STRING_WORK=...` and `TELEGRAM_SESSION_STRING_PERSONAL=...` along with shared `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`
- **THEN** the server starts in multi-mode with two clients labeled `"work"` and `"personal"`

#### Scenario: Single-account backward compatibility
- **WHEN** `.env` contains only unsuffixed `TELEGRAM_SESSION_STRING` (no suffixed variants)
- **THEN** the server starts in single-mode with one client labeled `"default"` and all tools work without requiring the `account` parameter

#### Scenario: Mixed suffixed and unsuffixed sessions
- **WHEN** `.env` contains both unsuffixed `TELEGRAM_SESSION_STRING` and suffixed `TELEGRAM_SESSION_STRING_WORK`
- **THEN** the server starts in multi-mode with the unsuffixed session labeled `"default"` and the suffixed session labeled `"work"`

#### Scenario: No session configured
- **WHEN** `.env` contains no `TELEGRAM_SESSION_STRING` or `TELEGRAM_SESSION_NAME` variables (suffixed or unsuffixed)
- **THEN** the server SHALL exit with a configuration error message

### Requirement: Client registry manages multiple TelegramClient instances
The server SHALL maintain a `clients: dict[str, TelegramClient]` registry mapping account labels to authenticated `TelegramClient` instances. A `get_client(account: str) -> TelegramClient` function SHALL provide lookup with a clear error if the label is unknown.

#### Scenario: Lookup existing account
- **WHEN** `get_client("work")` is called and a client with label `"work"` exists
- **THEN** the corresponding `TelegramClient` instance is returned

#### Scenario: Lookup unknown account
- **WHEN** `get_client("nonexistent")` is called
- **THEN** an error is returned listing available account labels

### Requirement: All clients are started and cache-warmed at startup
The server SHALL call `client.start()` and `client.get_dialogs()` for every client in the registry during startup. Startup of multiple clients SHALL be concurrent via `asyncio.gather`.

#### Scenario: Startup with two accounts
- **WHEN** the server starts with accounts `"work"` and `"personal"` configured
- **THEN** both clients are authenticated and their entity caches are warmed before the MCP server begins accepting requests

### Requirement: list_accounts tool exposes available accounts
The server SHALL provide a `list_accounts` MCP tool that returns all configured account labels with profile information (name, phone number, online status) for each.

#### Scenario: List accounts in multi-mode
- **WHEN** `list_accounts()` is called with two configured accounts
- **THEN** the response includes each account's label, display name, phone number, and online status

#### Scenario: List accounts in single-mode
- **WHEN** `list_accounts()` is called in single-mode
- **THEN** the response includes one account with label `"default"` and its profile information

### Requirement: Entity resolution is per-client
The `resolve_entity` and `resolve_input_entity` functions SHALL accept a `client` parameter instead of using a global. Each client's entity cache is independent.

#### Scenario: Resolve entity for specific account
- **WHEN** `resolve_entity(identifier, client=work_client)` is called
- **THEN** entity resolution uses the `work_client`'s cache, falling back to `work_client.get_dialogs()` on cache miss
