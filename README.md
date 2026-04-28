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
- [File Path Security](#file-path-security)
- [Docker](#docker)
- [Development](#development)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## What It Can Do

The server currently includes 80+ MCP tools grouped into these areas:

- **Accounts:** list configured accounts and route tool calls by account label.
- **Chats and groups:** list chats, inspect metadata, create groups/channels, join or leave chats, invite users, manage admins, bans, default permissions, slow mode, topics, invite links, common chats, read receipts, and message links.
- **Messages:** send, schedule, edit, delete, forward, pin, unpin, mark read, reply, search, inspect context, create polls, manage reactions, inspect inline buttons, and press inline callbacks.
- **Contacts:** list, search, add, delete, block, unblock, import, export, inspect direct chats, and find recent contact interactions.
- **Media:** send files, download media, upload files, send voice notes, stickers, GIFs, and inspect message media.
- **Profile and privacy:** get your own account info, update profile fields, set or delete profile photos, inspect privacy settings, get user info/photos/status, and manage bot commands.
- **Folders and drafts:** list, create, update, reorder, and delete Telegram folders; save, list, and clear drafts.

All tool results that include Telegram user-controlled content are sanitized and, where practical, returned as structured JSON.

## Requirements

- Python 3.10+
- Telegram API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)
- A Telegram session string or file-based session
- An MCP client such as Claude Desktop, Cursor, or another MCP-compatible host
- Optional: [uv](https://docs.astral.sh/uv/) for local development and `uvx` usage

## Quick Start

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

Run the server locally:

```bash
uv run main.py
```

## MCP Client Configuration

For Claude Desktop or Cursor, point the MCP server at this project:

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
      ]
    }
  }
}
```

You can also run from PyPI with `uvx`:

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "uvx",
      "args": ["telegram-mcp"],
      "env": {
        "TELEGRAM_API_ID": "your_api_id_here",
        "TELEGRAM_API_HASH": "your_api_hash_here",
        "TELEGRAM_SESSION_STRING": "your_session_string_here"
      }
    }
  }
}
```

Generate a session string without cloning the repo:

```bash
uvx --from telegram-mcp telegram-mcp-generate-session
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
- "Send this from my work account to @example"

## File Path Security

File-path tools are disabled until allowed roots are configured. This affects tools such as `send_file`, `download_media`, `upload_file`, `send_voice`, `send_sticker`, `set_profile_photo`, and `edit_chat_photo`.

Allowed roots can come from:

- Server CLI arguments, used as a fallback.
- MCP client Roots, when supported by the client.

Security behavior:

- Client MCP Roots replace server CLI roots when available.
- Empty client Roots are treated as deny-all.
- Paths are resolved through real paths and must stay inside an allowed root.
- Traversal, wildcard-like, shell-like, and null-byte path patterns are rejected.
- Relative paths resolve under the first allowed root.
- Downloads default to `<first_root>/downloads/`.
- Size and extension limits are enforced for sensitive media tools.

Run with allowed roots:

```bash
uv run main.py /data/telegram /tmp/telegram-mcp
```

Or with `uvx`:

```bash
uvx telegram-mcp /data/telegram /tmp/telegram-mcp
```

## Docker

Build the image:

```bash
docker build -t telegram-mcp:latest .
```

Run with Compose:

```bash
docker compose up --build
```

Run directly:

```bash
docker run -it --rm \
  -e TELEGRAM_API_ID="YOUR_API_ID" \
  -e TELEGRAM_API_HASH="YOUR_API_HASH" \
  -e TELEGRAM_SESSION_STRING="YOUR_SESSION_STRING" \
  telegram-mcp:latest
```

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
- Prefer session strings over file sessions when running multiple server instances.
- All Telegram API calls go directly from your machine/container to Telegram.
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
- **Invalid API credentials:** verify `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` at [my.telegram.org/apps](https://my.telegram.org/apps).
- **Database is locked:** prefer string sessions, or make sure no other process is using the same file session.
- **File tools are disabled:** pass allowed roots or configure MCP Roots in your client.
- **Path rejected:** ensure the path is inside an allowed root and does not use traversal or wildcard patterns.
- **Auth errors after password changes:** regenerate your session string.
- **Bot-only tool rejected:** regular user accounts cannot manage bot command settings.
- **Need details:** check your MCP client logs, terminal output, and `mcp_errors.log`.

## Contributing

1. Fork and clone the repository.
2. Create a focused branch.
3. Add or update tests when behavior changes.
4. Run formatting, tests, and coverage locally.
5. Open a pull request with a concise description.

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
