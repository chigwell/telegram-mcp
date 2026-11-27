# MCP Events

`telegram.message.created` exposes new-message metadata through the existing
bounded message log. Events are available automatically; clients choose what to
monitor. They obey the same configured Telegram/chat/topic access as tools.
Existing `wait_for_events`, debounce tools and the optional JSONL feed keep their
behavior. This implements the official **Events beta**, which may still change.

| MCP connection | Poll | Push | Webhook |
| --- | --- | --- | --- |
| stdio | Yes | Yes | No |
| Anonymous Streamable HTTP | Yes | Yes | No |
| Streamable HTTP with configured bearer auth | Yes | Yes | Yes |
| Legacy SSE | No | No | No; ordinary tools remain available |

`events/list` describes supported modes. `server/discover` advertises
`capabilities.extensions["io.modelcontextprotocol/events"]`. Anonymous webhook
requests return Forbidden (`-32024`) with a configuration explanation; they do
not disable the server or its other modes. A client supporting only webhooks
needs authenticated HTTP. Client application wake-up support is also required.

## Configuration

Normal local stdio installation needs no new setting. Existing client wiring
continues to launch the command with Telegram environment variables, for example:

```json
{
  "mcpServers": {
    "telegram": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/telegram-mcp", "run", "main.py"],
      "env": {
        "TELEGRAM_API_ID": "your_api_id",
        "TELEGRAM_API_HASH": "your_api_hash",
        "TELEGRAM_SESSION_STRING": "your_session"
      }
    }
  }
}
```

For a shared HTTP process, retain the normal Telegram environment and set
`MCP_TRANSPORT=http`. Clients connect to `http://127.0.0.1:8765/mcp`. Authentication
is optional for tools, poll and push. To enable webhooks, generate a random token:

```bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Set that value as `TELEGRAM_MCP_HTTP_TOKEN` in the **server** environment. It must
contain at least 32 printable ASCII characters. The SDK's bearer middleware then
requires `Authorization: Bearer <same token>` on MCP HTTP requests. Configure the
same token in each client. For Codex's ordinary HTTP tool connection:

```toml
[mcp_servers.telegram]
url = "http://127.0.0.1:8765/mcp"
bearer_token_env_var = "TELEGRAM_MCP_HTTP_TOKEN"
```

This config supplies a credential; it does not establish native Codex event
wake-up. For clients with generic HTTP headers, set `Authorization` to
`Bearer <token>` in their MCP server entry. Avoid committing credential values.
The server provides no OAuth authorization server, browser login or fake OAuth
metadata. Clients requiring an OAuth flow need a separate deployment integration;
that is outside this feature. Host/Origin validation remains enabled independently.

Static authentication represents **one deployment owner**. Every client with that
token shares that owner's webhook registrations and configured tool access. Token
rotation preserves ownership; update the server and clients together. This is not
multi-user authorization. Webhook persistence is supported for this configured
static owner, not arbitrary embedding applications' custom SDK identity providers.
No additional authentication is introduced on stdio.

## What to monitor

Use `arguments: {"chat_id": 123, "account": "default"}` for one chat. IDs,
usernames and aliases follow existing resolution rules. An explicit account is
required with multiple accounts. For account-wide monitoring, omit `chat_id` and
explicitly set `all_chats: true`. Outgoing messages are excluded unless
`include_outgoing: true` is explicit. These defaults prevent accidental broad
monitoring and reply loops. Subscriptions do not grant additional read access.

Sources are private chats, basic groups and supergroups. Broadcast channels,
edits, deletions, reactions and settled bursts are not included. Each occurrence
contains `eventId`, `name`, `timestamp`, `data` and `cursor`. Data contains only
`account`, `chat_id`, `message_id` and `timestamp`; read content with existing tools.
Occurrence IDs are stable across delivery modes and retries. Consumers should
deduplicate within each subscription.

`TELEGRAM_EVENT_LOG_SIZE=0` disables collection and removes the event from the
catalog, without disabling ordinary tools.

## Poll and push

Poll with `events/poll` using `name`, `arguments`, optional `cursor`, `maxEvents`
(up to 100) and `maxAgeMs`. The result contains `events`, `cursor`, `truncated`,
`hasMore` and suggested `nextPollMs`. No registration or unsubscribe is required.
A null/omitted cursor bootstraps at the current position, returning no history.

Push opens a long-lived request:

```json
{
  "jsonrpc": "2.0",
  "id": "watch-123",
  "method": "events/stream",
  "params": {
    "name": "telegram.message.created",
    "arguments": {"chat_id": 123, "account": "default"},
    "cursor": null,
    "heartbeatMs": 30000
  }
}
```

Examples omit the core protocol's per-request `_meta` fields for readability;
modern clients provide their version, client information and capabilities.
Modern HTTP requests also need `Mcp-Method` matching the method and `Mcp-Name`
matching the event name, where applicable. A mismatch returns HTTP 400 /
HeaderMismatch (`-32020`). Standard Base64-encoded header values are accepted.

The server sends `notifications/events/active` with authoritative `heartbeatMs`,
then matching `notifications/events/event` frames. Quiet streams receive
`notifications/events/heartbeat`. Suggested intervals are clamped to 1–30 seconds;
the default is 30 seconds. Each notification carries the request ID at
`params._meta["io.modelcontextprotocol/subscriptionId"]`.

On HTTP the POST returns SSE frames; abort that response to cancel. On stdio send
`notifications/cancelled` with `params.requestId`. Independent streams may have
identical filters; cancelling one leaves others running. Ordinary tools remain
callable with their existing timeouts. Streams need no registration file or secret.

## Webhooks

On authenticated HTTP, call `events/subscribe` with `name`, `arguments` and:

```json
{
  "delivery": {
    "mode": "webhook",
    "url": "https://receiver.example/callback",
    "secret": "whsec_<base64-encoded-24-to-64-random-bytes>"
  },
  "ttlMs": 3600000,
  "cursor": null
}
```

The client supplies the signing secret. The server verifies a signed challenge,
then uses Standard Webhooks signatures. This signing secret is separate from the
HTTP bearer token: bearer identifies the client to Telegram MCP; the webhook
secret lets the callback verify deliveries from Telegram MCP. Callback URLs require
public HTTPS on port 443. Address validation, pinned connections, normal TLS,
no redirects, bounded verification bodies and request timeouts remain enforced.

Registration identity is owner + event name + canonical **original** arguments +
callback URL. Repeating that identity refreshes it. Different arguments or URLs
create independent subscriptions. Omitted defaults and explicitly supplied defaults
remain distinct. Aliases bind to their resolved chat; refresh does not move them.

Results contain `id`, `refreshBefore`, a replay cursor and `truncated`. Default TTL
is one hour; grants range from one second to 24 hours. `ttlMs: null` receives a
finite 24-hour grant. Refresh before expiration. Signing-key replacement retains
the old signature for five minutes. Up to 100 active registrations are supported.
Unsubscribe using the original name, arguments and delivery URL, without a secret.
A delivery already in flight may arrive after cancellation.

Registrations survive restart in
`${XDG_STATE_HOME:-~/.local/state}/telegram-mcp/subscriptions.json`.
Writes are atomic and private (mode 0600 on POSIX). Only one HTTP app may own a
state file. Separate instances need separate state directories. Stdio and anonymous
HTTP do not open the webhook store.

Each delivery has a random `webhook-id`, distinct from its occurrence `eventId`.
Four concurrent deliveries retry independently, with four total attempts and
1/2/4-second backoff. Retries preserve ID/body and regenerate signatures. HTTP
408/425/429 and server errors are retried. HTTP 410/413 abandon that delivery,
not the registration. Gap and termination control messages are signed and retried.

## Replay and limits

All modes share opaque cursors over the **bounded in-memory** log. Pass the last
cursor back to resume; never parse or edit it. `maxAgeMs` limits initial replay,
not later live events with old Telegram timestamps. Cursor checkpoints and message
history are not persisted. Restart, overrun or age clipping reports `truncated`
(or a webhook gap). Resume covers only retained events; it is not an archive.
Webhook cursor watermarks never pass an outstanding earlier delivery.

Downtime or exhausted retries can lose events. Clients can reconcile through
existing message-reading tools. A client must implement both Events delivery and
agent scheduling to wake an agent. OpenCode 2.4.2 offers native push event
automations. Support in one client does not establish Codex, ChatGPT or dot
compatibility. Hosted authentication/tunnels and OAuth login are outside this PR.

References: [official Events beta](https://github.com/modelcontextprotocol/ext-events/blob/main/specification/beta/events/events.mdx),
[core MCP protocol](https://modelcontextprotocol.io/specification/2026-07-28),
[Standard Webhooks](https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md).
