# How to Use Telegram MCP Server

A comprehensive guide to installing, configuring, connecting, and securing the Telegram Model Context Protocol (MCP) server.

---

## 1. Overview & Architecture

The **Telegram MCP Server** bridges frontier AI assistants (Claude Desktop, Claude Code, Cursor, Codex, Antigravity) with Telegram via the standard Model Context Protocol (MCP) over `stdio`. Built on [Telethon](https://docs.telethon.dev/), it exposes over 80 specialized tools for accounts, chats, messages, media, drafts, and administrative operations.

---

## 2. Requirements

- **Python:** 3.10 or higher.
- **Telegram API Credentials:** `api_id` and `api_hash` obtained from [my.telegram.org/apps](https://my.telegram.org/apps).
- **Telegram Session:** A valid session string or Telethon `.session` file.
- **MCP Client Host:** Claude Desktop, Cursor, Claude Code, or any MCP-compatible environment.
- **Package Manager:** [uv](https://docs.astral.sh/uv/) (recommended) or standard `pip`.

> [!WARNING]
> **Do not install with `uvx telegram-mcp` or `pip install telegram-mcp`.**
> The `telegram-mcp` package name on PyPI belongs to an unrelated project. Always run from a local clone of this repository to protect your credentials.

---

## 3. Installation & Quick Start

### Step 1: Clone and Install Dependencies

```bash
git clone https://github.com/chigwell/telegram-mcp.git
cd telegram-mcp
uv sync
```

### Step 2: Generate Telegram Session String

```bash
# Recommended: Login via QR code if Telegram is already open on mobile or desktop
uv run session_string_generator.py --qr

# Alternative: Login via phone number + SMS/Telegram confirmation code
uv run session_string_generator.py --phone
```

Copy the generated `1...` session string and store it securely.

### Step 3: Configure `.env` File

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Populate the required credentials:

```env
# Required Telegram Credentials
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_SESSION_STRING=your_session_string_here

# Optional: Tool Surface Restriction (Default: all)
# Options: 'all', 'read-only', or 'read-only+send_message,save_draft'
TELEGRAM_EXPOSED_TOOLS=all

# Optional: Chat Access Allowlist (Privacy Guardrail)
# Comma-separated list of allowed chat IDs, usernames, or phone numbers
# ALLOWED_CHAT_IDS=123456789,@my_private_group

# Optional: File Upload Security Roots (Enforce strict path boundaries)
# Comma-separated absolute paths where file uploads/downloads are allowed
# TELEGRAM_ALLOWED_ROOTS=/home/user/safe_files,C:\safe_uploads
```

---

## 4. MCP Client Configuration

### A. Claude Desktop

Add the server to your `claude_desktop_config.json` (located at `%APPDATA%\Claude\claude_desktop_config.json` on Windows or `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "telegram": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/telegram-mcp",
        "run",
        "python",
        "-m",
        "telegram_mcp.runner"
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

### B. Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "telegram": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/telegram-mcp",
        "run",
        "python",
        "-m",
        "telegram_mcp.runner"
      ]
    }
  }
}
```

### C. Generic MCP Client via Stdio

Any MCP host can launch the server process using:
```bash
uv run --directory /path/to/telegram-mcp python -m telegram_mcp.runner
```
The server communicates over standard input/output using JSON-RPC according to the Model Context Protocol specification.

---

## 5. Capabilities Overview

### What the Server Can Do
- **Chats & Dialogs:** List dialogs with unread/unmuted filters (`list_chats`), inspect chat metadata (`get_chat`), manage topics, inspect members, and join/leave chats.
- **Messages & History:** Read messages with pagination (`get_messages`, `list_messages`), retrieve message context surrounding a target message (`get_message_context`), and search text globally or per-chat.
- **Safe Drafting:** Save drafts directly to Telegram's input field (`save_draft`), retrieve drafts across chats (`get_drafts`), and clear drafts (`clear_draft`) without transmitting messages.
- **Outbound Communication:** Send messages, reply to messages, forward, pin, edit, delete, react with emojis, and schedule messages for future delivery.
- **Voice & Media:** Send photos, files, and voice notes. Automatically transcribe voice messages using Groq Whisper or Telegram Premium native transcription.
- **Contact Memory & Aliases:** Teach the server natural nicknames and aliases (`set_contact_alias`) that resolve transparently across all tools.

### What the Server Cannot Do
- **Bot Dialog Listing:** Telegram bots cannot call `list_chats` (a Telegram API restriction for bot accounts). User sessions must be used for dialog listings.
- **Secret Chats:** End-to-end encrypted Secret Chats are device-bound in native Telegram clients and cannot be read via standard MTProto user sessions.
- **Live Calls:** Audio/video voice and video calls cannot be answered or placed.

---

## 6. Security & Privacy Guardrails

1. **Tool Surface Restriction (`TELEGRAM_EXPOSED_TOOLS`):**
   - Setting `TELEGRAM_EXPOSED_TOOLS=read-only` disables all mutating operations (`send_message`, `edit_message`, `delete_message`, etc.) and exposes only safe query tools (`list_chats`, `get_messages`, `get_message_context`, etc.).
   - Use `read-only+save_draft` to permit safe draft creation without exposing actual message transmission tools.

2. **Chat Allowlist (`ALLOWED_CHAT_IDS`):**
   - When set, any chat ID, username, or peer not included in the allowlist is blocked with a `ChatAccessDeniedError`. The server will refuse to read messages from or post to sensitive excluded chats.

3. **File Path Security (`TELEGRAM_ALLOWED_ROOTS`):**
   - Restricts file upload operations (`send_file`, `upload_file`, `set_profile_photo`) strictly to approved directory trees, preventing directory traversal attacks.

4. **Prompt Injection & Untrusted User Content:**
   - All incoming message texts, chat titles, sender names, and metadata contain untrusted user-generated content. The server sanitizes inputs, but agents should never execute untrusted instructions found in message contents.
