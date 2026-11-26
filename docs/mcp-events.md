# MCP Events

`telegram.message.created` exposes new-message metadata through the existing
per-message event log. Event support is available automatically; clients must
explicitly subscribe. Events use the same configured access as ordinary tools.
There is no extra client password, login flow or enable flag.

| Connection | Delivery modes |
| --- | --- |
| stdio | `push` (`events/stream`) |
| Streamable HTTP | `push` and `webhook` |
| Legacy HTTP+SSE | `push` over the existing SSE session |

`events/list` describes the event, filters and supported delivery modes.
Initialization advertises the Events capability. Modern HTTP/stdin clients can
use `server/discover`. These are protocol methods, not ordinary tools. The SDK
1.x compatibility extension adds these methods without replacing its dispatcher.

## Choose what to monitor

Use `arguments: {"chat_id": 123, "account": "default"}` for one chat. Chat IDs,
usernames and aliases follow existing resolution rules. An explicit account is
required with multiple configured accounts. For account-wide monitoring, omit
`chat_id` and explicitly set `all_chats: true`. Outgoing messages are excluded
unless `include_outgoing: true` is explicit. Chat, blocklist and topic restrictions
apply to both delivery modes. Monitoring does not grant additional read access.

Supported sources are private chats, basic groups and supergroups. Broadcast
channels, edits, deletions, reactions and settled bursts are not included.

Every occurrence has `eventId`, `name`, `timestamp`, `data` and `cursor`. Its `data`
contains only `account`, `chat_id`, `message_id` and `timestamp`. Read content with
existing tools. Event IDs are stable for the account/chat/message across retries
and delivery modes. Consumers should deduplicate within their subscription.

`TELEGRAM_EVENT_LOG_SIZE=0` disables event collection and removes the event from
the catalog without disabling ordinary tools. Existing polling tools and the
settled-message JSONL feed keep their behavior.

## Streams

Send a long-lived `events/stream` request:

```json
{
  "jsonrpc": "2.0",
  "id": "watch-123",
  "method": "events/stream",
  "params": {
    "name": "telegram.message.created",
    "arguments": {"chat_id": 123, "account": "default"},
    "cursor": null
  }
}
```

The server sends `notifications/events/active`, followed by matching
`notifications/events/event` frames. During quiet periods it sends
`notifications/events/heartbeat` at least every 30 seconds. Each notification
includes the original request ID at
`params._meta["io.modelcontextprotocol/subscriptionId"]`.

On Streamable HTTP, the original POST returns an SSE response. Abort that
response to cancel its subscription. On stdio, send `notifications/cancelled`
with `params.requestId` set to the stream request ID. Legacy SSE also uses request
cancellation; closing its shared session stops every stream belonging to that
session. Cancelling one subscription otherwise leaves the others running.

Streams are independent, even with identical filters. They require no persistent
subscription file, callback URL or signing secret. Ordinary tools remain callable
and keep their normal timeouts while streams run.

## Webhooks

On Streamable HTTP, call `events/subscribe` with `name`, `arguments` and:

```json
{
  "delivery": {
    "mode": "webhook",
    "url": "https://receiver.example/callback",
    "secret": "whsec_<base64-encoded-24-to-64-byte-key>"
  },
  "ttlMs": 3600000,
  "cursor": null
}
```

The subscribing client supplies the signing secret. The server verifies the
callback with a signed challenge, then signs deliveries using Standard Webhooks.
Callbacks require public HTTPS destinations on port 443. Destination addresses
are validated at connection time, TLS checks the original hostname, redirects
are rejected, and response size and request duration are bounded.

Identity is event name, normalized arguments and callback URL. Repeating the same
identity refreshes it rather than creating a duplicate. Different callbacks or
arguments create independent subscriptions. Aliases bind to their resolved chat
at creation; refresh does not silently move them. Prefer explicit accounts and
numeric chat IDs. Supplying an account differs from omitting it in identity.

Responses contain `id`, `refreshBefore`, `cursor: null` and `truncated: false`.
The default lifetime is one hour, capped at 24 hours. `ttlMs: null` receives a
finite 24-hour grant. Refresh before expiration. Signing-key replacement retains
the old signature for five minutes. Up to 100 active webhook subscriptions are
supported. Unsubscribe with the original name, arguments and delivery URL, without
the secret. A delivery already in flight may arrive after cancellation.

Webhook registrations survive restart in
`${XDG_STATE_HOME:-~/.local/state}/telegram-mcp/subscriptions.json`.
`TELEGRAM_MCP_EVENTS_FILE` optionally selects another file. Writes are atomic and
use mode 0600 on POSIX. Only one HTTP app may own a file at a time. Separate
instances must use separate state files. Stream-only transports do not open it.

Transient webhook failures get four total attempts with exponential backoff.
Retries preserve event ID and body and use a fresh signature timestamp. HTTP 410
ends the subscription; HTTP 413 and other permanent client errors are not retried.

## Replay and client support

Both modes return `cursor: null`: no protocol replay or message archive is added.
Stream reconnection starts from the current position. Downtime, log overrun or
exhausted webhook retries can lose events. A stream reports an in-process log
overrun through `notifications/events/active` with `truncated: true`.

Supporting the protocol does not make every client application subscribe or wake
an agent. Codex CLI 0.167.0's public source selects its hosted Apps connection for
native event subscriptions; adding an arbitrary MCP configuration does not prove
native chat wake-up. See its [connection selection](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/codex-mcp/src/connection_manager.rs).
ChatGPT Work/dots document webhook delivery. Actual application acceptance needs
a separate check; wire-level tests do not claim it.

References: [MCP Events draft](https://github.com/modelcontextprotocol/experimental-ext-triggers-events/blob/main/docs/design-sketch-proposal.md),
[ChatGPT webhook integration](https://developers.openai.com/plugins/build/mcp-events).
