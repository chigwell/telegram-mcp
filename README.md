<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&height=200&section=header&text=Telegram%20MCP%20Server&fontSize=50&fontAlignY=35&animation=fadeIn&fontColor=FFFFFF&descAlignY=55&descAlign=62" alt="Telegram MCP Server" width="100%" />
</div>

![MCP Badge](https://badge.mcpx.dev)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green?style=flat-square)](https://opensource.org/licenses/Apache-2.0)
[![Python Lint & Format Check](https://github.com/chigwell/telegram-mcp/actions/workflows/python-lint-format.yml/badge.svg)](https://github.com/chigwell/telegram-mcp/actions/workflows/python-lint-format.yml)
[![Docker Build & Compose Validation](https://github.com/chigwell/telegram-mcp/actions/workflows/docker-build.yml/badge.svg)](https://github.com/chigwell/telegram-mcp/actions/workflows/docker-build.yml)

A Telegram integration for Claude, Cursor, and other MCP-compatible clients. It exposes Telegram account, chat, message, contact, media, folder, and admin operations through the [Model Context Protocol](https://modelcontextprotocol.io/) using [Telethon](https://docs.telethon.dev/).

## 🤖 MCP in Action

Basic Telegram MCP usage in Claude:

![Telegram MCP in action](screenshots/1.png)

Asking Claude to analyze chat history and send a response:

![Telegram MCP Request](screenshots/2.png)

Message sent successfully:

![Telegram MCP Result](screenshots/3.png)

## Contents

- [What It Can Do](#what-it-can-do)
- [Requirements](#requirements)
- [Quick Start](#quick-start)
- [MCP Client Configuration](#mcp-client-configuration)
- [Multi-Account Setup](#multi-account-setup)
- [Device Identity](#device-identity)
- [Proxy Support](#proxy-support)
- [File Path Security](#file-path-security)
- [Bulk Chat Export](#bulk-chat-export)
- [Docker](#docker)
- [Development](#development)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## What It Can Do

The server currently includes 80+ MCP tools grouped into these areas:

- **Accounts:** list configured accounts and route tool calls by account label.
- **Chats and groups:** list chats, inspect metadata, create groups/channels, join or leave chats, invite users, manage admins, bans, default permissions, slow mode, topics, invite links, common chats, read receipts, and message links.
- **Messages:** send, schedule, edit, delete, forward, pin, unpin, mark read, reply, search, inspect context, create polls, manage reactions, inspect inline buttons, and press inline callbacks. `send_message`, `reply_to_message`, and `edit_message` support classic formatting (`parse_mode='md'`/`'html'`) and server-side rich formatting (`parse_mode='rich'`/`'rich_markdown'`/`'rich_html'` — full Markdown/HTML with tables, headings, formulas, and collapsible sections). Rich modes require Telegram Premium on the account; Premium is re-checked on every call, and without it nothing is sent — the tool returns a structured `telegram_premium_required` result so the agent can reformat with classic modes and retry.
- **Contacts:** list, search, add, delete, block, unblock, import, export, inspect direct chats, find recent contact interactions, and remember contacts by the names you actually use (see below).

### Remembered contacts

`set_contact_alias` teaches the server what you call someone, and every tool that takes a `chat_id` understands it from then on — `send_message("андрей бекендер", ...)` just works. A contact can carry any number of aliases, which is how tags work: save both `андрей бекендер` and `бекендер` for the same person and either resolves.

**Only an exact saved wording ever sends.** Similar wording (`Андрею бекендеру` for a saved `андрей бекендер`) is matched too, but only to *suggest*: the tool sends nothing and asks you to confirm the contact by name. This is deliberate — `Лена`/`Леня` and `Иван`/`Иванов` differ exactly as much as a case ending does, so a matcher confident enough to handle declensions is also confident enough to message the wrong person whenever the one you meant is not saved yet. Confirming saves that wording as its own alias, so each new phrasing costs one yes/no the first time and nothing ever again. Set `TELEGRAM_CONTACT_FUZZY=0` to drop the suggestions too.

When a reference is unknown, resembles one contact, matches several, or points at a contact that no longer resolves, tools send nothing and return a structured instruction telling the agent exactly what to ask you, to save the answer with `set_contact_alias`, and to retry once. `list_contact_aliases` shows one row per person with all their aliases (use it to spot a wrong memory), `delete_contact_alias` forgets one, and repointing an alias at someone else requires `replace=True`. The save path itself refuses a target it would have to guess at: contacts are saved by @username, phone, numeric ID, or an alias already confirmed for them.

Aliases live in `${XDG_STATE_HOME:-~/.local/state}/telegram-mcp/aliases.json` (owner-only, written atomically); `TELEGRAM_ALIASES_FILE` overrides the path, and a pre-existing `aliases.json` next to the code is still read as a fallback.
- **Media:** send files, download media, upload files, send voice notes, stickers, GIFs, inspect message media, and transcribe voice messages/video notes (see below).

### Voice transcription

`transcribe_voice(chat_id, message_id, engine=None)` turns a voice message or video note into text. Two engines are available:

- `groq` (default): uploads the recording to Groq's hosted `whisper-large-v3-turbo`. Leaves the server and costs a download+upload per call, but doesn't drop the recording's last few words the way native transcription does. Requires `GROQ_API_KEY`.
- `telegram`: native Telegram Premium transcription (`messages.TranscribeAudioRequest`). Free and never leaves Telegram, but empirically drops the last speech segment in roughly 2 of 3 recordings and requires Telegram Premium on the account. Long recordings come back `pending` and are polled automatically.

The engine is chosen per call via the `engine` argument, or otherwise defaults to `TELEGRAM_TRANSCRIBE_ENGINE` (`groq` or `telegram`). Results are cached by `(chat_id, message_id)` in a local SQLite file so repeat reads and repeat listings never re-transcribe the same message. Every transcript is returned with a `note` marking it as a machine transcript, not a verbatim quote — treat it as a paraphrase, not exact wording.

`get_history`, `get_messages`, and `list_messages` fill in already-cached transcripts for voice messages instead of leaving the text empty, controlled by `TELEGRAM_TRANSCRIBE`:

- `off`: `transcribe_voice` is disabled and listings never show transcripts.
- `on-demand` (default): listings show cached transcripts but never spend an API call fetching a new one.
- `auto`: listings also prefetch missing transcripts, bounded per call by `TELEGRAM_TRANSCRIBE_MAX_VOICES`/`TELEGRAM_TRANSCRIBE_MAX_SECONDS` (Groq isn't free, so this prefetch is budgeted rather than unbounded).

The cache lives in `TELEGRAM_TRANSCRIPT_CACHE_DIR` (default `data/transcripts`), written as a 700 directory / 600 file since it holds personal-chat text in plaintext — see [Docker](#docker) for why this needs its own volume mount in a container.
- **Profile and privacy:** get your own account info, update profile fields, set or delete profile photos, inspect privacy settings, get user info/photos/status, and manage bot commands.
- **Folders and drafts:** list, create, update, reorder, and delete Telegram folders; save, list, and clear drafts.
- **Events:** wait for incoming messages with debounce (`wait_for_new_message`, `wait_for_settled_message`), optionally for one chat only via `chat_id` — without it any unrelated conversation wakes the wait — or enable the opt-in incoming event feed for callback-style delivery (see below).

All tool results that include Telegram user-controlled content are sanitized and, where practical, returned as structured JSON.

### Incoming Event Feed (callback mode, Claude Code only)

By default, an agent waits for replies by calling `wait_for_settled_message`, which blocks up to the MCP tool timeout and must be re-called — that works everywhere (Codex, Cursor, etc.) and is unchanged.

Clients that can wake an agent on external output (Claude Code's persistent `Monitor` on `tail -f`) can switch to callback mode instead:

1. The agent calls `enable_incoming_feed` (or set `TELEGRAM_EVENT_FEED=1` in the environment to auto-enable). Each settled incoming burst is appended as one JSON line to `${XDG_STATE_HOME:-~/.local/state}/telegram-mcp/incoming_feed.jsonl`, created owner-only (0600). Override the path with `TELEGRAM_EVENT_FEED_FILE` — an explicit path's directory must already exist. `incoming_feed_status` reports the effective path and a ready-to-use watch command.
2. The agent arms a persistent Monitor with the `watch_command` returned by the tool. Every new line re-invokes the agent with the burst summary; no blocking tool call is held open, and the chat stays free.

`disable_incoming_feed` switches back; `incoming_feed_status` reports the current mode. While the feed is enabled it consumes settled bursts, so don't combine it with `wait_for_settled_message`. Feed lines contain user-generated `name` fields — treat them as untrusted data.

## Requirements

- Python 3.10+
- Telegram API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)
- A Telegram session string or file-based session
- An MCP client such as Claude Desktop, Cursor, or another MCP-compatible host
- Optional: [uv](https://docs.astral.sh/uv/) for local development

## Quick Start

> Do not install this server with `uvx telegram-mcp`, `uvx --from telegram-mcp`,
> or `pip install telegram-mcp`. The `telegram-mcp` name on PyPI is currently
> owned by a different project and does not install this repository. Passing
> `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, or `TELEGRAM_SESSION_STRING` to that
> package can expose Telegram account credentials to unrelated third-party code.

### 1. Clone and Install

```bash
git clone https://github.com/chigwell/telegram-mcp.git
cd telegram-mcp
uv sync
```

### 2. Generate a Session String

```bash
uv run session_string_generator.py
```

Follow the prompts. Save the generated session string securely.

For scripted setup or operational runbooks, choose the login method explicitly:

```bash
# QR login, recommended when you already have Telegram open on another device
uv run session_string_generator.py --qr

# Phone number + verification code login
uv run session_string_generator.py --phone
```

Without a flag, the generator keeps the interactive method prompt.

### 3. Configure Environment

Copy the example file and fill in your real values:

```bash
cp .env.example .env
```

Single-account setup:

```env
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_SESSION_STRING=your_session_string_here
```

By default, all Telegram MCP tools are exposed. If you want to prevent MCP
clients from sending messages or performing chat/account mutations, set
`TELEGRAM_EXPOSED_TOOLS=read-only` to expose only tools annotated with
`readOnlyHint=True`:

```env
TELEGRAM_EXPOSED_TOOLS=read-only
```

If read-only is too strict but `all` is too broad, append `+` and a
comma-separated list of tool names to also expose those specific write tools.
Every other write tool stays unregistered:

```env
TELEGRAM_EXPOSED_TOOLS=read-only+send_message,reply_to_message,send_file
```

An unknown name in the allowlist aborts startup, so a typo cannot silently
degrade into a narrower surface that looks like it worked.

This is an MCP tool-surface restriction, not a Telegram session sandbox or
reduced Telegram account permission. The Telegram session string still has its
normal authority inside the server process; read-only mode only prevents
non-read-only tools from being registered and exposed through MCP. Accepted
values are `all` (the default), `read-only`, and `read-only+<tool>,<tool>`.

Voice transcription (see [Voice transcription](#voice-transcription) above) is
off by default in the sense that no transcript is ever fetched unless you ask
for one — `transcribe_voice` is always available, and listings only pick up
already-cached transcripts. Enable prefetching or pick an engine explicitly:

```env
TELEGRAM_TRANSCRIBE=on-demand       # off / on-demand (default) / auto
TELEGRAM_TRANSCRIBE_ENGINE=groq     # groq (default) or telegram
GROQ_API_KEY=your_groq_api_key_here # required whenever engine=groq is used
```

`engine=groq` requires `GROQ_API_KEY`; `engine=telegram` requires Telegram
Premium on the account. `TELEGRAM_TRANSCRIBE_MAX_VOICES` (default 5) and
`TELEGRAM_TRANSCRIBE_MAX_SECONDS` (default 300) bound how much `auto` mode
prefetches per listing call; `TELEGRAM_TRANSCRIPT_CACHE_DIR` (default
`data/transcripts`) sets where the SQLite cache is written.

Run the server locally:

```bash
uv run main.py
```

## MCP Client Configuration

For Claude Desktop or Cursor, point the MCP server at a cloned checkout of
this project:

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/full/path/to/telegram-mcp",
        "run",
        "main.py"
      ],
      "env": {
        "TELEGRAM_API_ID": "your_api_id_here",
        "TELEGRAM_API_HASH": "your_api_hash_here",
        "TELEGRAM_SESSION_STRING": "your_session_string_here"
      }
    }
  }
}
```

To expose only read-only tools in Claude Desktop or Cursor, add this to the
server `env` block:

```json
"TELEGRAM_EXPOSED_TOOLS": "read-only"
```

Or keep read-only as the baseline and allow a few write tools on top:

```json
"TELEGRAM_EXPOSED_TOOLS": "read-only+send_message,reply_to_message"
```

Alternatively, install this repository directly from GitHub into a virtual
environment using a specific release tag or commit:

```bash
python -m venv .venv
. .venv/bin/activate
pip install "git+https://github.com/chigwell/telegram-mcp.git@<tag-or-commit>"
```

Then configure your MCP client to run the installed console script:

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "/full/path/to/.venv/bin/telegram-mcp",
      "env": {
        "TELEGRAM_API_ID": "your_api_id_here",
        "TELEGRAM_API_HASH": "your_api_hash_here",
        "TELEGRAM_SESSION_STRING": "your_session_string_here"
      }
    }
  }
}
```

Generate a session string without cloning the repo by sourcing this repository
from GitHub explicitly:

```bash
uvx --from "git+https://github.com/chigwell/telegram-mcp.git@<pinned-release-tag-or-commit>" telegram-mcp-generate-session
```

### Transports

The server speaks three MCP transports, selected with `MCP_TRANSPORT`:

| Value   | Transport                  | Use case                                                        |
| ------- | -------------------------- | --------------------------------------------------------------- |
| `stdio` | stdio (default)            | One dedicated server process per MCP client                     |
| `http`  | streamable HTTP            | One shared server for many clients (Claude Code, Codex, Cursor) |
| `sse`   | SSE (legacy HTTP)          | Clients that only support the deprecated SSE transport          |

For `http` and `sse`, the server binds `MCP_HOST`:`MCP_PORT` (default
`127.0.0.1:8765`); the streamable HTTP endpoint is `/mcp`, the SSE endpoint is
`/sse`.

If the server is reachable via a domain (e.g. behind a reverse proxy) rather
than only `127.0.0.1`/`localhost`, set `MCP_ALLOWED_HOSTS` (and optionally
`MCP_ALLOWED_ORIGINS`) to enable DNS-rebinding protection and allow that Host
header, e.g. `MCP_ALLOWED_HOSTS=mcp.example.com`. Comma-separated; supports a
`:*` suffix to allow any port. Left unset, DNS-rebinding protection stays off
(the historical default).

Prefer `http` when more than one MCP client (or many coding-agent sessions)
will use the server: a single long-lived process holds one Telegram
connection, instead of every client spawning its own Telethon session —
Telegram throttles and may flag accounts that open many parallel sessions.

Register the shared server with clients:

```bash
# Claude Code
claude mcp add --transport http telegram http://127.0.0.1:8765/mcp

# Codex
codex mcp add telegram --url http://127.0.0.1:8765/mcp
```

For stdio-only clients, bridge with [mcp-remote](https://www.npmjs.com/package/mcp-remote):

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8765/mcp"]
    }
  }
}
```

## Multi-Account Setup

Use suffixed session variables to configure multiple Telegram accounts:

```env
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_SESSION_STRING_WORK=session_string_for_work
TELEGRAM_SESSION_STRING_PERSONAL=session_string_for_personal
```

Labels are lowercased and become the `account` parameter value in tools.

- In single-account mode, `account` is optional.
- In multi-account mode, write tools require `account`.
- Read-only tools fan out to all accounts when `account` is omitted.

Example prompts:

- "List my accounts"
- "Show unread messages from all accounts"

### Session pool (one account, several concurrent clients)

To run several MCP clients against the **same** Telegram account at once (for
example the desktop app *and* a terminal CLI), give each client its own
authorized session. Telegram forbids one session (auth key) being used from two
IPs simultaneously, so on a VPN or dual-stack host two local clients can collide
with `AuthKeyDuplicatedError`. List several interchangeable session strings in
`TELEGRAM_SESSION_STRINGS` (separated by whitespace, comma or semicolon); each
process claims a free one via an advisory file lock, so clients deterministically
pick distinct sessions:

```env
TELEGRAM_SESSION_STRINGS=<session A> <session B> <session C>
```

Generate extra sessions with `uv run session_string_generator.py`. The pool
takes precedence over `TELEGRAM_SESSION_STRING` for the default account. As an
extra safety net, a transient `AuthKeyDuplicatedError` at connect time (e.g.
during a VPN reconnect) is retried with backoff before the server gives up.

Size the pool to the number of clients you actually run concurrently. If every
slot is already claimed, the server refuses to start with an explicit error
rather than reusing a session another client holds — reuse would make Telegram
permanently invalidate that session for both clients.
- "Send this from my work account to @example"

## Device Identity

These optional variables control how the client appears in Telegram under
**Settings > Devices** (the active-sessions list):

```env
TELEGRAM_DEVICE_MODEL=Telegram MCP
TELEGRAM_SYSTEM_VERSION=1.0
TELEGRAM_APP_VERSION=1.0
```

If left unset, Telethon falls back to the host platform (for example `arm64`).
Because these values are re-sent on every connection, a long-running server
would otherwise overwrite the name chosen during login on each reconnect, so
set them to keep a stable, recognisable device name. The same variables are
read both by the session string generator (at login) and by the server (on
every connect), so set them in the same place as your other credentials.

## Proxy Support

Route Telegram traffic through a proxy by setting the `TELEGRAM_PROXY_*`
environment variables. Supported types are `socks5`, `socks4`, `http`, and
`mtproxy`.

SOCKS and HTTP proxies require the optional `python-socks` package:

```bash
uv sync --extra proxy
# or
pip install python-socks
```

Single-account configuration:

```env
TELEGRAM_PROXY_TYPE=socks5
TELEGRAM_PROXY_HOST=127.0.0.1
TELEGRAM_PROXY_PORT=1080
TELEGRAM_PROXY_USERNAME=optional_user
TELEGRAM_PROXY_PASSWORD=optional_pass
TELEGRAM_PROXY_RDNS=true
```

MTProxy:

```env
TELEGRAM_PROXY_TYPE=mtproxy
TELEGRAM_PROXY_HOST=mtproxy.example
TELEGRAM_PROXY_PORT=443
TELEGRAM_PROXY_SECRET=ee0123456789abcdef...
```

Per-account overrides use the same `_<LABEL>` suffix as session variables and
take precedence over the unsuffixed defaults:

```env
TELEGRAM_PROXY_TYPE=socks5
TELEGRAM_PROXY_HOST=127.0.0.1
TELEGRAM_PROXY_PORT=1080

TELEGRAM_PROXY_TYPE_WORK=http
TELEGRAM_PROXY_HOST_WORK=proxy.work.example
TELEGRAM_PROXY_PORT_WORK=3128
```

Misconfigured proxy settings (unknown type, missing host/port, invalid port,
missing MTProxy secret, or a missing `python-socks` package) cause the server
to fail fast at startup with a clear error message instead of silently
bypassing the proxy.

## File Path Security

File-path tools are disabled until allowed roots are configured. This affects tools such as `send_file`, `download_media`, `upload_file`, `send_voice`, `send_sticker`, `set_profile_photo`, and `edit_chat_photo`.

Allowed roots can come from:

- Server CLI arguments, used as a fallback.
- MCP client Roots, when supported by the client.

Security behavior:

- Client MCP Roots replace server CLI roots when available.
- Some clients (notably Cursor) return workspace roots as bare absolute paths
  instead of `file://` URIs. That breaks MCP SDK validation of `list_roots`;
  the server recovers those absolute paths from the validation error so
  file-path tools keep working.
- Empty client Roots are treated as deny-all by default. Some clients implement
  the Roots capability but advertise an empty list, which disables file tools
  even when server CLI roots are configured. Set
  `TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK=1` to fall back to the server CLI roots
  in that case (opt-in; the default stays deny-all). The same opt-in also applies
  when `list_roots` fails unexpectedly and no client paths could be recovered.
- Paths are resolved through real paths and must stay inside an allowed root.
- Traversal, wildcard-like, shell-like, and null-byte path patterns are rejected.
- Relative paths resolve under the first allowed root.
- Downloads default to `<first_root>/downloads/`.
- Size and extension limits are enforced for sensitive media tools.

Run with allowed roots:

```bash
uv run main.py /data/telegram /tmp/telegram-mcp
```

From an MCP client configuration, pass the same roots after `main.py`:

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/full/path/to/telegram-mcp",
        "run",
        "main.py",
        "/data/telegram",
        "/tmp/telegram-mcp"
      ],
      "env": {
        "TELEGRAM_API_ID": "your_api_id_here",
        "TELEGRAM_API_HASH": "your_api_hash_here",
        "TELEGRAM_SESSION_STRING": "your_session_string_here"
      }
    }
  }
}
```

## Bulk Chat Export

Exporting a dozen chats is a CLI job, not an MCP tool: the output belongs on
disk, not in a client's context window. `telegram-mcp-export` ships with this
package and shares its plumbing - client construction, device identity, proxy
support and voice transcription.

**JSONL is the source of truth; HTML, Markdown and plain text are rendered from
it**, so changing your mind about the format costs nothing and never touches the
Telegram API again.

### Session

Export authorises as its **own device** and refuses to reuse any
`TELEGRAM_SESSION_STRING*` it finds in the environment. Sharing one auth key
between the running server and a second client is what makes Telegram revoke it
(`AuthKeyDuplicatedError`), taking the server down with it. The session pool in
[Multi-Account Setup](#multi-account-setup) solves a different problem -
concurrent clients on one host - and a pool slot is claimed per process, so
export stays out of it.

```bash
telegram-mcp-export login          # once, interactive: phone, code, 2FA password
telegram-mcp-export whoami
```

The session file lives in `~/.config/telegram-mcp/export.session` (override with
`--session`).

### Exporting

```bash
# find the exact chats first
telegram-mcp-export chats --out chats.tsv

# one chat, whole history
telegram-mcp-export export @somechat --all

# a batch, last six months, JSONL + HTML
telegram-mcp-export export --from-file targets.txt --months 6 --format jsonl,html
```

A target is an `@username`, a `t.me/...` link, a numeric id, or a chat title
(exact match first, then a case-insensitive substring over your dialogs; an
ambiguous title lists the candidates and stops rather than guessing).
`--from-file` takes one target per line and allows `#` comments.

Exactly one depth flag is required - the tool never guesses how much history you
meant:

| Flag | Window |
|---|---|
| `--all` | everything, from the first message |
| `--months N` | the last N months |
| `--since 2026-01-01` | from a date |
| `--until 2026-06-30` | additionally cut the top |

A date filter does not walk the whole history: the message id at the date
boundary is located first, and the scan runs forward from there.

### Output

`--format jsonl,html,md,txt` (default `jsonl`, which is always written).

- `messages.jsonl` - one message per line: normalised fields plus `raw`, the
  full Telethon `to_dict()` with datetimes as ISO strings and bytes as base64.
  `--no-raw` shrinks it considerably at the cost of fidelity.
- `messages.html` - a Telegram Desktop-style read: date dividers, consecutive
  messages from one sender joined, a colour per participant, replies linking to
  the quoted message, forwards, reactions and inline media. Paginated every 3000
  messages.
- `messages.md` / `messages.txt` - for reading and for feeding to other tools.
- `meta.json` - export parameters, date window, counts and an id-to-name map.

Re-render an existing export without going online:

```bash
telegram-mcp-export render out/Some\ chat_-1001234567890 --format html,md
```

### Media and transcription are opt-in

```bash
telegram-mcp-export export @chat --all --media --media-max-mb 50
telegram-mcp-export export @chat --months 3 --transcribe            # groq
telegram-mcp-export export @chat --months 3 --transcribe=telegram   # native, Premium
```

Without `--media` nothing is downloaded, but every attachment is still described
(kind, size, name, duration), so the conversation stays readable.

Transcription uses the same engines as the `transcribe_voice` tool, with the
same caveat: the native Telegram engine drops the last speech segment in roughly
two recordings out of three, and the truncation is invisible in the text. Groq
is the default for that reason. Every transcript records which engine produced
it and is a machine reading, not a verbatim quote.

### Interruptions

Ctrl+C loses nothing already written. Continue with:

```bash
telegram-mcp-export export @chat --all --resume
```

`--resume` reads the last id from the existing `messages.jsonl` and appends only
what is newer.

## Docker

Build the image:

```bash
docker build -t telegram-mcp:latest .
```

### Shared server (recommended)

Run one long-lived container serving streamable HTTP, and point every MCP
client at it (see [Transports](#transports) for client registration):

```bash
docker run -d --name telegram-mcp --restart unless-stopped \
  --env-file .env \
  -e MCP_TRANSPORT=http \
  -e MCP_HOST=0.0.0.0 \
  -p 127.0.0.1:8765:8765 \
  telegram-mcp:latest
```

`MCP_HOST=0.0.0.0` binds inside the container so the published port works;
`-p 127.0.0.1:8765:8765` keeps the server reachable only from the local
machine — the endpoint is unauthenticated, so never publish it on a public
interface.

The bundled Compose file runs the same setup:

```bash
docker compose up --build -d
```

It also mounts `./transcript_cache` into the container at
`/app/data/transcripts` so the voice-transcription SQLite cache (see
[Voice transcription](#voice-transcription)) survives a rebuild instead of
living in the container's writable layer. Create it once, owned by the
container's `appuser` (uid 1000), before starting:

```bash
mkdir -p ./transcript_cache && chown 1000:1000 ./transcript_cache
```

### One container per client (stdio)

Alternatively, an MCP client can spawn a dedicated container itself:

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "--env-file", "/full/path/to/.env", "telegram-mcp:latest"]
    }
  }
}
```

This is fine for a single client, but with several clients (or coding agents
that spawn subagent sessions) each one starts its own container and its own
Telegram session, which Telegram throttles; a client that exits uncleanly can
also leave its container running. Prefer the shared server above in those
setups.

For multiple accounts, pass variables such as `TELEGRAM_SESSION_STRING_WORK` and `TELEGRAM_SESSION_STRING_PERSONAL`.

## Development

The implementation is split into a small compatibility entrypoint and modular package code:

```text
main.py                    # historical entrypoint and compatibility exports
telegram_mcp/runtime.py    # shared MCP setup, account routing, validation, file safety
telegram_mcp/runner.py     # application startup
telegram_mcp/tools/        # tool modules grouped by domain
sanitize.py                # output sanitization helpers
tests/                     # pytest suite
```

Run tests:

```bash
uv run pytest
```

Run tests with coverage:

```bash
uv run pytest --cov --cov-report=term-missing --cov-report=xml
```

Coverage is configured in `pyproject.toml` with an 80% minimum gate for deterministic unit-testable core modules. GitHub Actions runs the same coverage command and uploads `coverage.xml`.

Run formatting checks:

```bash
uv run black --check .
uv run flake8 .
```

## Security Notes

- Never commit `.env`, session strings, or `.session` files.
- A Telegram session string grants access to the account it belongs to.
- The `telegram-mcp` package name on PyPI is not controlled by this project.
  Avoid PyPI-based `telegram-mcp` install commands unless ownership changes and
  the package is verified.
- This repository includes a best-effort startup guard that refuses installed
  `telegram-mcp` distributions without a source checkout or direct git/file
  install record. That guard cannot run when the unrelated PyPI package itself
  is launched, so use clone-based or explicit git installs.
- Prefer session strings over file sessions when running multiple server instances.
- By default, Telegram API calls go directly from your machine/container to Telegram.
  If `TELEGRAM_PROXY_*` is configured, Telegram traffic is routed through the
  configured SOCKS/HTTP/MTProxy proxy instead.
- User-generated Telegram content is sanitized before being returned to MCP clients.

### Prompt Injection Protection

Telegram messages, display names, chat titles, and button labels are untrusted content. The server mitigates prompt-injection risk with:

- Structured JSON output for user-controlled data where practical.
- `sanitize_user_content()`, `sanitize_name()`, and `sanitize_dict()` for control-character stripping, invisible-character stripping, and length limits.
- MCP content annotations marking returned content as user audience data.
- Tool descriptions that warn clients not to treat returned Telegram fields as model instructions.
- No brittle keyword-based filtering.

## Troubleshooting

- **No Telegram session configured:** set `TELEGRAM_SESSION_STRING`, `TELEGRAM_SESSION_NAME`, or suffixed multi-account variants.
- **Session is not authorized:** run `uv run session_string_generator.py --qr` outside
  the MCP server when you can scan from an existing Telegram app, or
  `uv run session_string_generator.py --phone` when you need phone-code login.
  Then set `TELEGRAM_SESSION_STRING` in `.env`. The MCP server does not perform
  interactive phone-code login over stdio.
- **Invalid API credentials:** verify `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` at [my.telegram.org/apps](https://my.telegram.org/apps).
- **Database is locked:** prefer string sessions, or make sure no other process is using the same file session.
- **`AuthKeyDuplicatedError` / "Another telegram-mcp process is already connected with this session":** two processes tried to connect the same Telegram session at once (e.g. an MCP client restarted the connector before the old process exited), which Telegram rejects and can invalidate the session for both. The server now takes an exclusive lock per session before connecting; a second concurrent launch waits briefly (default 20s, override with `TELEGRAM_LOCK_GRACE_SECONDS`) for the first to release it and otherwise exits without ever calling `connect()`, instead of racing into a duplicate connection. Retry once only one instance is running.
- **File tools are disabled:** pass allowed roots or configure MCP Roots in your client.
- **Path rejected:** ensure the path is inside an allowed root and does not use traversal or wildcard patterns.
- **Auth errors after password changes:** regenerate your session string.
- **Bot-only tool rejected:** regular user accounts cannot manage bot command settings.
- **Need details:** check your MCP client logs, terminal output, and `mcp_errors.log`.

## Contributing

1. Fork and clone the repository.
2. Install dependencies and git hooks:
   - `uv sync`
   - `uv run pre-commit install --hook-type pre-commit --hook-type pre-push`
3. Create a focused branch.
4. Add or update tests when behavior changes.
5. Run checks locally:
   - `uv run pre-commit run --all-files`
   - `uv run pre-commit run --hook-stage pre-push --all-files`
6. Open a pull request with a concise description.

## License

This project is licensed under the [Apache 2.0 License](LICENSE).

## Acknowledgements

- [Telethon](https://github.com/LonamiWebs/Telethon)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Claude](https://www.anthropic.com/) and [Cursor](https://cursor.so/)
- [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) upstream project

Maintained by [@chigwell](https://github.com/chigwell) and [@l1v0n1](https://github.com/l1v0n1). PRs welcome.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=chigwell/telegram-mcp&type=Date)](https://www.star-history.com/#chigwell/telegram-mcp&Date)

## Contributors

<a href="https://github.com/chigwell/telegram-mcp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=chigwell/telegram-mcp" />
</a>
