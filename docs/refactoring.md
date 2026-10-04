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

`main` remains a facade for external callers; internal implementation modules
must not import it. Tool adapters use explicit runtime imports rather than
wildcard imports. Their frozen `__all__` lists retain historical exports without
exposing the new private implementation-module bindings.

The extracted implementation areas are grouped by responsibility:

| Public adapter | Implementation modules | Boundary |
| --- | --- | --- |
| Runtime values and formatting | `core_types`, `serialization`, `formatting`, `validation`, `error_formatting` | Canonical types, JSON fallback, display/rich-text formatting, identifier checks and error rendering; runtime adapters supply current dependency bindings |
| Runtime policy | `access_policy`, `file_policy` | Per-chat access, roots provenance, path checks and extension policy; policy state remains in runtime |
| Runtime accounts and connections | `account_config`, `connections` | Account/session/proxy discovery, connection checks and resolution retries; clients, locks and connection-health state remain in runtime/runner |
| Runtime aliases | `alias_store` | Persistence, normalization, matching and user-confirmation payloads; canonical alias types are reexported through runtime |
| MCP and startup helpers | `mcp_policy`, `startup` | Exposure/timeouts/result annotation and shared stderr encoding setup; the MCP server, handlers and startup lifetime remain with runtime/runner |
| Messages | `_message_rendering`, `_message_reads`, `_message_sending`, `_message_forwarding`, `_message_mutations`, `_message_interactions`, `_message_export` | Rendering and related RPC operations; public wrappers retain signatures and decorators |
| Chats | `_chat_topics`, `_chat_discovery`, `_chat_settings` | Forum topics, discovery/read operations and chat settings |
| Groups | `_group_membership`, `_group_settings`, `_group_admin`, `_group_invites` | Membership, configuration, administrative permissions and invitations |
| Folders | `_folder_filters` | Common regular/shared-filter reconstruction; folder adapters retain RPC sequencing and idempotency checks |

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
identity and signatures are checked instead. Canonical exception/alias/enum
types also retain their historical `telegram_mcp.runtime` module path for
import and pickling compatibility.

Python minor versions change raw docstring whitespace and standard-library
signature displays. `tests/fixtures/refactor_contract_python_versions.json`
records exact description hashes and export overrides captured from untouched
baseline source on each supported interpreter. The original Python 3.13
catalogue remains unchanged. Every description must match its pristine hash
before its interpreter-specific representation is excluded from the canonical
comparison; names, schemas, annotations and exposure modes stay fully compared.
The profiles record source commits, fixture and lockfile hashes, interpreter
versions and dependency versions. Do not derive expectations from refactored
source or silently accept an uncaptured interpreter version.

`tests/test_refactor_operations.py` supplies complementary behavioral traces:
regular/shared folder add/remove requests preserve complete filter metadata,
return the same text, leave the original object unchanged, and issue no second
write when repeated. Missing folders and unresolved chats stop before update.
Both public entity resolvers preserve reconnect, cache warm-up and marked-ID
fallback ordering, and stop without another lookup when reconnection fails.
Existing family tests continue to cover their broader RPC
success/error behavior; this file adds previously missing characterization.

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
| File roots, client/server precedence, extension restrictions and safe paths | Extract pure path/configuration helpers with policy state left in runtime | File-path security and runtime roots/extension tests; preserve unsupported-client versus explicit-empty-client roots status and all opt-in fallback decisions; rejected paths cause no upload/download RPC |
| Per-chat privacy policy, filtering before pagination, and denial before RPC | Reuse policy and formatting helpers without broadening accepted identifiers | Chat-allowlist tests and touched adapter tests |
| RPC request types/arguments/order and returned text/JSON for touched tool families | Extract repeated adapter implementation blocks | Existing recording-client tests plus focused success/empty/invalid/RPC-failure cases for each extraction |
| Event waits, debounce/feed state, sanitized output, owner-only files and retained failed writes | Make event dependencies explicit while keeping implementation and task ownership in events | Event-feed tests, including chat filtering and failed-write retention |
| Error category/text, FloodWait delay, schema mismatch guidance, sanitized logs and unknown write completion | Consolidate formatting without changing retry/timeout behavior | FloodWait, schema-drift, content-safeguard and runtime logging tests |
| Installed console entrypoints, historical imports and packaged helper modules | Add private modules to the current packaging convention | Build/install wheel in an isolated environment; import each extracted module and compare the MCP catalogue without connecting |

Mocked RPC assertions must capture request types, meaningful arguments, ordering,
and retry counts. Preserve JSON/text output and error text for the touched paths.
Normalize only inherently varying values, such as timestamps or random request
IDs; do not discard fields whose changes would alter caller behavior.

Roots results carry provenance as well as paths. A client that does not support
roots/list differs from one that supplies an explicit empty allowlist. Preserve
the existing distinction, opt-in server fallback and server-only configuration;
do not collapse these cases into a single empty-list test or generic fallback.

## Offline validation and remaining limitations

Test bootstrap runs before application collection: inherited `TELEGRAM_*`,
`MCP_*`, and provider API keys are cleared, dotenv loading is disabled, dummy
credentials use an in-memory session, and persistent state/logs/locks are kept in
temporary directories. Internet socket connections fail immediately; Unix
sockets remain available for event-loop machinery. Forbidden connection attempts
also fail test teardown if a broad exception handler swallowed the immediate
error; only explicit guard self-tests may opt into recording expected attempts.
Tests needing Telegram or
provider behavior must install recording/fake clients rather than real accounts.
Per-test alias, transcript, event-feed and XDG directories prevent persistent
state from leaking between tests. No live Telegram action is part of this suite.

Run targeted characterization after each pass and the complete offline suite
with branch coverage before delivery. Include every extracted core module in
coverage; moving core code must not shrink the measured surface. Extracted tool
implementations remain outside the existing deterministic-core coverage metric,
alongside their original API adapters; catalogue, output and mocked-RPC checks
cover their behavior. Keep the repository's
current Black check, fatal Flake8 gate and CI checks. The existing 80% gate covers
local deterministic logic and is not a substitute for adapter parity tests.

The supported Python floor remains 3.10. The existing test workflow requests
Python 3.11 during setup, but plain `uv run` follows `.python-version` and runs
3.13. Correcting that workflow's interpreter selection is a separate CI task;
explicit `uv run --python <version>` checks avoid relying on its setup label.
Acceptance includes the supported floor when a new language construct is
introduced. Lockfiles and dependency versions remain unchanged during this
campaign. A real Telegram smoke test, if needed, is a separate explicitly
authorized manual verification; offline tests cannot prove Telegram's live
service responses or account-specific permissions.

Separate follow-ups include stricter lint tooling, public typing/schema changes,
Telethon compatibility-request replacement, connection lifecycle redesign and
transcription decomposition. The handwritten forum-topic requests remain intact
pending independent schema verification. TypeSafe/Jev does not apply to these
mechanical extractions: they introduce no semantic judgment or AI decision logic.

A preexisting initialization defect also needs its own functional fix: a malformed
or negative `TELEGRAM_FLOOD_SLEEP_THRESHOLD` can reference logging before the
logger is initialized during account construction. This refactor preserves the
existing initialization order and does not correct that failure path.

## Delivery validation

The final tree passed 881 offline tests independently on Python 3.10.19,
3.11.16, 3.12.3 and 3.13.11, with separate coverage output files. Each run
reported 92.86% branch coverage across the deterministic-core surface and its
extracted implementations. Black checked all 102 tracked Python files; the
blocking Flake8 check and whitespace check passed. Every added Python file also
parsed with Python 3.10 syntax rules. Pristine/refactored contract captures
matched exactly on each interpreter. Sensitivity checks also verified that the
portable tests reject description whitespace, schema, annotation and signature
changes.

An isolated wheel build included all 50 packaged Python files. Noneditable
installation from an explicit source path preserved all 132 tool definitions,
three exposure modes, 732 compatibility exports and three console entrypoints.
Both session utilities passed their real help invocation. The server entrypoint
reached its configuration boundary with the event loop mocked; no Telegram
startup or network access was attempted. Direct raw-wheel installation retains
the existing provenance guard's rejection of the server entrypoint.

Compose configuration validated using `.env.example`. The local Docker engine
timed out; the existing [Docker Build & Compose Validation workflow](https://github.com/chigwell/telegram-mcp/actions/runs/37236734055)
successfully built both production and development images, validated Compose
syntax and built the Compose service. These jobs build images without executing
pytest or starting the application; live container execution remains a separate
smoke check. The [tests workflow](https://github.com/chigwell/telegram-mcp/actions/runs/37236722647)
passed 881 tests with 92.67% coverage on Linux/Python 3.13.16, and the
[lint/format workflow](https://github.com/chigwell/telegram-mcp/actions/runs/37236727944)
passed. Independent review also compared 95 relocated tool
functions with the original AST, including public signatures, decorator order,
docstrings and dependencies, and found no unexplained behavior changes.
