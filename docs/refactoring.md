# Modular refactor contracts

The baseline is remote main commit `e6dd352f286f71464fc19946a8d601bff51d8fd1`.
This campaign preserves behavior while extracting private, independently
testable helpers from runtime and the largest tool adapters. Changes to
frameworks, dependency versions, transport lifecycle, public APIs, and
transcription engines require separate migration tasks.

## Ownership and compatibility

`main.py` is the historical compatibility facade and console entrypoint. It
reexports runtime and tool names, preserves canonical exception/enum identities,
and synchronizes its monkeypatchable roots/chat-allowlist aliases with runtime.
Keep these names and call signatures working, including private helpers
historically exposed through runtime's `__all__`.

`telegram_mcp.runtime` owns the singleton MCP server, discovered account clients,
connection health state, registration hooks, and configurable security policies.
`telegram_mcp.runner` owns startup, transport selection, connection/session-lock
lifetime, cache warm-up, and shutdown. This refactor does not move ownership of
that mutable state or defer the existing import-time initialization.

`telegram_mcp.tools` imports adapters to register the MCP tools. Decorator order,
tool descriptions/schemas/annotations, account routing, and result formatting are
contracts. Private implementation modules must not register tools or instantiate
clients. Helpers should receive values and explicit dependencies from adapters,
so existing adapter/runtime monkeypatch seams still work and leaf helpers do not
import runtime back into their own dependency graph.

`telegram_mcp.transcription` remains a separate service with its current caching,
engine selection, providers, and integration points. Its internal redesign is
outside this campaign. Existing session/install guards and filesystem/chat
access restrictions remain active before any operation they currently protect.

## Frozen contracts and review checks

`tests/fixtures/refactor_contract.json` captures all 132 registered MCP tools:
descriptions, input/output schemas, annotations, and exposed/hidden sets for
`all`, `read-only`, and `read-only+send_file,send_message`. It also captures
facade/runtime/tools export names, value kinds, and callable signatures.
`tests/test_refactor_parity.py` compares the live registrations against this
fixture without invoking Telegram or pruning the singleton server. Only process
addresses in callable default representations are normalized. A relocated
implementation's defining module is deliberately not frozen; public class
identity and signatures are checked instead.

Do not regenerate the fixture to make a structural refactor pass. Any difference
requires investigation; an intentional public change needs separate approval
and an accompanying compatibility specification. In particular, new private
imports must not leak into the historical wildcard exports.

Review each pass with a concrete before/after behavior, the smaller structural
unit, and its parity evidence. Delete code only after checking registration,
exports, tests, packaging, and dynamic references. Keep commits small enough to
review and revert independently.

## Behavior matrix

| Behavior preserved | Structural pass | Acceptance evidence |
| --- | --- | --- |
| All registered tools, defaults, schemas, descriptions, annotations and exposure modes | Extract implementation helpers while leaving public wrappers/decorators in place | Frozen catalogue and export parity tests |
| CLI startup validation, configuration order, connections and cleanup | Remove shadowed tests and isolate startup helpers | Real runner entrypoint tests; extension validation remains before tool pruning; invalid transport connects before validation and still disconnects/releases locks |
| Single-account routing, readonly multi-account fan-out, account labels and text/image content | Simplify repeated routing and content helpers | Runtime/account tests and content safeguard tests; exact content block ordering and annotations |
| Input validation, aliases, entity resolution, retries and cache warming | Extract parsing/formatting/validation helpers | Validation, aliases, runtime and entity-resolution tests; canonical exception identities |
| File roots, client/server precedence, extension restrictions and safe paths | Extract pure path/configuration helpers with policy state left in runtime | File-path security and runtime roots/extension tests; rejected paths cause no upload/download RPC |
| Per-chat privacy policy, filtering before pagination, and denial before RPC | Reuse policy and formatting helpers without broadening accepted identifiers | Chat-allowlist tests and touched adapter tests |
| RPC request types/arguments/order and returned text/JSON for touched tool families | Extract repeated adapter implementation blocks | Existing recording-client tests plus focused success/empty/invalid/RPC-failure cases for each extraction |
| Event waits, debounce/feed state, sanitized output, owner-only files and retained failed writes | Extract pure event summary/scanning helpers while keeping task ownership in events | Event-feed tests, including chat filtering and failed-write retention |
| Error category/text, FloodWait delay, schema mismatch guidance, sanitized logs and unknown write completion | Consolidate formatting without changing retry/timeout behavior | FloodWait, schema-drift, content-safeguard and runtime logging tests |
| Installed console entrypoints, historical imports and packaged helper modules | Add private modules to the current packaging convention | Build/install wheel in an isolated environment; import each extracted module and compare the MCP catalogue without connecting |

Mocked RPC assertions must capture request types, meaningful arguments, ordering,
and retry counts. Preserve JSON/text output and error text for the touched paths.
Normalize only inherently varying values, such as timestamps or random request
IDs; do not discard fields whose changes would alter caller behavior.

## Offline validation and remaining limitations

Test bootstrap runs before application collection: inherited `TELEGRAM_*`,
`MCP_*`, and provider API keys are cleared, dotenv loading is disabled, dummy
credentials use an in-memory session, and persistent state/logs/locks are kept in
temporary directories. Internet socket connections fail immediately; Unix
sockets remain available for event-loop machinery. Tests needing Telegram or
provider behavior must install recording/fake clients rather than real accounts.
Per-test alias, transcript, event-feed and XDG directories prevent persistent
state from leaking between tests. No live Telegram action is part of this suite.

Run targeted characterization after each pass and the complete offline suite
with branch coverage before delivery. Include every new extracted module in
coverage; moving code must not shrink the measured surface. Keep the repository's
current Black check, fatal Flake8 gate and CI checks. The existing 80% gate covers
local deterministic logic and is not a substitute for adapter parity tests.

The supported Python floor remains 3.10; the current test workflow uses 3.11.
Acceptance includes the supported floor when a new language construct is
introduced. Lockfiles and dependency versions remain unchanged during this
campaign. A real Telegram smoke test, if needed, is a separate explicitly
authorized manual verification; offline tests cannot prove Telegram's live
service responses or account-specific permissions.
