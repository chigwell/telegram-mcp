## ADDED Requirements

### Requirement: with_account decorator injects client into tools
A `@with_account(readonly=True|False)` decorator SHALL add an `account: str = None` parameter to the MCP tool signature and inject a resolved `TelegramClient` as the `client` keyword argument.

#### Scenario: Read-only tool with explicit account
- **WHEN** a read-only tool is called with `account="work"`
- **THEN** the decorator resolves the `"work"` client and passes it as `client` to the tool function

#### Scenario: Write tool with explicit account
- **WHEN** a write tool is called with `account="personal"`
- **THEN** the decorator resolves the `"personal"` client and passes it as `client` to the tool function

### Requirement: Read-only tools fan out to all accounts when account is omitted
In multi-mode, when a read-only tool (`@with_account(readonly=True)`) is called without an `account` parameter, the decorator SHALL execute the tool function once per registered client, prefix each result with `[label]`, and return the concatenated output.

#### Scenario: get_chats without account in multi-mode
- **WHEN** `get_chats(page=1)` is called without `account` and two accounts exist (`"work"`, `"personal"`)
- **THEN** the tool executes for both accounts, returning results prefixed with `[work]` and `[personal]`

#### Scenario: Fan-out executes concurrently
- **WHEN** a read-only tool fans out to multiple accounts
- **THEN** the calls to each account's client SHALL execute concurrently via `asyncio.gather`

### Requirement: Write tools require explicit account in multi-mode
In multi-mode, when a write tool (`@with_account(readonly=False)`) is called without an `account` parameter, the decorator SHALL return an error message listing available accounts.

#### Scenario: send_message without account in multi-mode
- **WHEN** `send_message(chat_id=123, text="hello")` is called without `account` and multiple accounts exist
- **THEN** an error is returned: account is required, available accounts are listed

### Requirement: Single-mode tools work without account parameter
In single-mode (one account), all tools SHALL work without the `account` parameter regardless of readonly setting. The sole client is always used.

#### Scenario: Tool call in single-mode without account
- **WHEN** any tool is called without `account` and only one account (`"default"`) is configured
- **THEN** the tool executes against the `"default"` client with no error

#### Scenario: Tool call in single-mode with explicit account
- **WHEN** a tool is called with `account="default"` in single-mode
- **THEN** the tool executes normally against the sole client

### Requirement: Output tagging in multi-mode fan-out
When a read-only tool fans out to all accounts, each account's result block SHALL be prefixed with `[label]` on its own line. In single-mode, no prefix SHALL be added.

#### Scenario: Tagged output format
- **WHEN** a read-only tool returns `"Chat A\nChat B"` for account `"work"` and `"Chat C"` for account `"personal"`
- **THEN** the combined output is `"[work]\nChat A\nChat B\n\n[personal]\nChat C"`

#### Scenario: No tagging in single-mode
- **WHEN** a read-only tool fans out in single-mode (one account)
- **THEN** the output is returned without any `[label]` prefix
