# MCP Events

Opt-in webhook subscriptions expose `telegram.message.created` on the HTTP MCP
endpoint. They reuse the bounded per-message event log behind `wait_for_events`.
Existing polling tools, stdio clients and the settled-message JSONL feed keep
their existing behavior.

## Availability

Event support is available automatically on the HTTP transport. Clients must
explicitly subscribe before receiving anything. Events use the same configured
access as existing MCP tools. There is no extra client password or login flow.
`TELEGRAM_EVENT_LOG_SIZE=0` disables the event source without disabling tools.

Subscriptions default to `$XDG_STATE_HOME/telegram-mcp/subscriptions.json`, or
`~/.local/state/telegram-mcp/subscriptions.json`. The optional
`TELEGRAM_MCP_EVENTS_FILE` changes that location for separate deployments.

The subscription file contains callback signing secrets. It is written atomically
with mode 0600 on POSIX and must reside in a trusted, private state directory.
Only one service may own a subscription file at a time. Give separate deployments
separate files. Windows operators must protect the directory with appropriate ACLs.

## Subscribe

The server advertises `events` in initialization and modern `server/discover`.
`events/list` describes the event and its schemas. These are JSON-RPC methods,
not tools named `events/subscribe`.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "events/subscribe",
  "params": {
    "name": "telegram.message.created",
    "arguments": {
      "account": "default",
      "chat_id": -1001234567890,
      "include_outgoing": false
    },
    "delivery": {
      "mode": "webhook",
      "url": "https://receiver.example/callback",
      "secret": "whsec_<base64-encoded-24-to-64-byte-key>"
    },
    "ttlMs": 3600000,
    "cursor": null
  }
}
```

Use one subscription per chat. `chat_id` accepts the same numeric IDs, usernames
and aliases as polling. With multiple accounts, select an explicit `account`.
For account-wide monitoring, omit `chat_id` and explicitly set `all_chats: true`.
Omitting both is an error. Outgoing messages are excluded unless
`include_outgoing: true` is explicit. Groups, supergroups and private chats are
supported; broadcast channels, edits, deletions, reactions and settled bursts are
not event types in this version.

Creation verifies the callback with a signed challenge. Callbacks require HTTPS
on port 443. Verification and delivery reject non-public destination addresses,
DNS rebinding and redirects. TLS still verifies the callback's original hostname.
Requests have a total deadline and callback response bodies are bounded.

The response returns a deterministic `id`, `refreshBefore`, `cursor: null` and
`truncated: false`. Refresh with the same event, original arguments and callback
URL. Chat aliases/usernames bind to their resolved chat at creation; refresh does
not move the subscription if an alias changes. Argument object key order does
not affect identity. Omitted defaults are normalized, but supplying an account
explicitly is a different identity from omitting it. Prefer explicit accounts and
numeric chat IDs for stable integration configuration.

The default lifetime is one hour. The maximum grant is 24 hours. `ttlMs: null`
also receives a finite 24-hour grant; clients must refresh before expiration.
Signing-key replacement is verified and retains the previous signature for five
minutes, including across restarts. At most 100 active subscriptions are allowed.

Unsubscribe using `events/unsubscribe` with the original `name`, `arguments` and
`delivery` (`mode` and `url`, without `secret`). Unsubscribe is idempotent and
works even after read access is revoked. A request already in flight may have
reached the receiver before unsubscribe or expiration takes effect.

## Delivery and limits

Each webhook uses the MCP Events envelope and Standard Webhooks HMAC-SHA256
headers. Its `data` contains only `account`, `chat_id`, `message_id` and
`timestamp`. Fetch message content through existing read tools. Monitoring does
not grant read access. Chat and topic restrictions are checked before delivery
and retries; account-wide subscriptions still obey those restrictions.

Workers run independently from tool requests and other subscriptions. A slow
callback does not block tools. Transient failures receive four total attempts
with exponential backoff. Retries preserve the event ID and body, and sign with a
fresh timestamp. HTTP 410 ends the subscription; HTTP 413 and other permanent
client errors are not retried.

Subscriptions survive restarts; messages and delivery queues do not. There is no
protocol replay. Downtime, log overruns and exhausted retries can lose events.
Duplicate deliveries are possible after ambiguous network failures. Consumers
must deduplicate by `eventId`. Events may arrive while an agent's tools run;
client scheduling controls when its agent reacts.

## Validation

Run `pytest tests/test_mcp_events.py tests/test_message_events.py tests/test_protocol_negotiation.py`.
Tests cover independent consumers, outgoing and account-wide opt-in, access
restrictions, expiration, restart, refresh, signing, callback validation and HTTP
integration. Real Telegram and ChatGPT callback verification require separate
integration checks; unit tests do not claim either.

Reference: [OpenAI MCP Events guide](https://developers.openai.com/plugins/build/mcp-events).
