# MCP Events implementation plan

Issue: https://github.com/chigwell/telegram-mcp/issues/418

## Agreed behavior

- Add opt-in HTTP webhook subscriptions for `telegram.message.created`.
- Reuse the per-message log and wake-up signal used by `wait_for_events`.
- Default to one selected chat. Require explicit opt-in for account-wide subscriptions.
- Exclude outgoing messages unless `include_outgoing` is explicitly true.
- Preserve chat, account, blocklist and forum-topic access restrictions.
- Deliver only account label, chat ID, message ID and timestamp.
- Support independent subscriptions, expiration, refresh and unsubscribe.
- Persist subscription secrets and metadata with private filesystem permissions.
- Advertise no replay (`cursor: null`). Do not add message archives or settled events.
- Leave existing polling tools and the JSONL feed unchanged.

## Implementation

1. Inspect HTTP transport, SDK extension points, event filtering and test conventions.
2. Add event discovery and JSON-RPC subscription methods on the authenticated HTTP endpoint.
3. Add durable subscription storage, signed callback verification and signed delivery.
4. Validate HTTPS destinations at connection time, block non-public addresses and redirects.
5. Run background delivery without blocking tools; bound retries and isolate subscriptions.
6. Document configuration, explicit scope, lifetimes and non-replay limitations.

## Validation

- Add pytest coverage for filtering, access restrictions, expiration, refresh, persistence,
  signing, callback validation, retries, independent subscribers and HTTP discovery.
- Run relevant tests, repository formatting/lint checks and the complete suite if feasible.
- Inspect the existing local integration without printing or copying credentials.
- Prefer the running Telegram service as the live event source to avoid a second session owner.
- Test with a temporary chat and authorized test messages if installed tools permit writes.
- Report any live-test limitation; do not claim a ChatGPT end-to-end check without one.
- Request independent subagent code review before completion and fix actionable findings.

## Status

- Branch created: `codex/mcp-events-webhooks`.
- Implementation and automated validation complete; live incoming-message acceptance pending.

## Implementation and automated validation

- Implemented the event catalog and subscription lifecycle on the HTTP endpoint.
- Added explicit chat/account scope, outgoing opt-in, persistent subscriptions,
  callback verification, signed delivery, retries and access rechecks.
- Added private storage ownership, startup failure handling and account/session binding.
- Added configuration examples and `docs/mcp-events.md`.
- Full suite: 2,111 tests passed. The 43 MCP Events tests passed separately.
- New event modules: 84.74% combined branch/statement coverage (80% threshold passed).
- Black and fatal-error lint checks passed. New modules and tests also pass full flake8.
  Existing complexity and docstring-length findings in `tools/events.py` remain unchanged.
- Astra and an independent regression reviewer found no remaining blockers after fixes.

## Live validation and deployment boundary

- Reused the existing local Telegram login without printing or copying credential values.
- Temporarily stopped the normal session owner and restored it automatically after testing.
- Created only dedicated temporary test groups; no real chats were read or modified.
- Verified callback challenge and HMAC signatures over a local HTTPS test receiver.
  A test-only transport routes the validated test address to loopback; production code has
  no localhost bypass.
- A second connection using the same Telegram login did not produce an event on the receiver.
  A user-originated message is needed for the remaining real delivery check.
- Direct ChatGPT Work acceptance has not been tested. Its connection UI cannot supply a
  custom static bearer token. Deployment requires a single-owner OAuth gateway which
  authenticates the user and injects the backend token; this PR does not deploy that gateway.
