<div align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&color=gradient&height=200&section=header&text=Telegram%20MCP%20Server&fontSize=50&fontAlignY=35&animation=fadeIn&fontColor=FFFFFF&descAlignY=55&descAlign=62" alt="Telegram MCP Server" width="100%" />
</div>

![MCP Badge](https://badge.mcpx.dev)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green?style=flat-square)](https://opensource.org/licenses/Apache-2.0)
[![Python Lint & Format Check](https://github.com/chigwell/telegram-mcp/actions/workflows/python-lint-format.yml/badge.svg)](https://github.com/chigwell/telegram-mcp/actions/workflows/python-lint-format.yml)
[![Docker Build & Compose Validation](https://github.com/chigwell/telegram-mcp/actions/workflows/docker-build.yml/badge.svg)](https://github.com/chigwell/telegram-mcp/actions/workflows/docker-build.yml)

---

## 🤖 MCP in Action

Here's a demonstration of the Telegram MCP capabilities in [Claude](https://docs.anthropic.com/en/docs/agents-and-tools/mcp):

 **Basic usage example:**

![Telegram MCP in action](screenshots/1.png)

1. **Example: Asking Claude to analyze chat history and send a response:**

![Telegram MCP Request](screenshots/2.png)

2. **Successfully sent message to the group:**

![Telegram MCP Result](screenshots/3.png)

As you can see, the AI can seamlessly interact with your Telegram account, retrieving and displaying your chats, messages, and other data in a natural way.

---

A full-featured Telegram integration for Claude, Cursor, and any MCP-compatible client, powered by [Telethon](https://docs.telethon.dev/) and the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). This project lets you interact with your Telegram account programmatically, automating everything from messaging to group management.


---

## 🚀 Features & Tools

This MCP server exposes a huge suite of Telegram tools. **Every major Telegram/Telethon feature is available as a tool!**

### Chat & Group Management
- **get_chats(page, page_size)**: Paginated list of chats
- **list_chats(chat_type, limit)**: List chats with metadata and filtering
- **get_chat(chat_id)**: Detailed info about a chat
- **create_group(title, user_ids)**: Create a new group
- **invite_to_group(group_id, user_ids)**: Invite users to a group or channel
- **create_channel(title, about, megagroup)**: Create a channel or supergroup
- **edit_chat_title(chat_id, title)**: Change chat/group/channel title
- **delete_chat_photo(chat_id)**: Remove chat/group/channel photo
- **leave_chat(chat_id)**: Leave a group or channel
- **get_participants(chat_id)**: List all participants
- **get_admins(chat_id)**: List all admins
- **get_banned_users(chat_id)**: List all banned users
- **promote_admin(chat_id, user_id)**: Promote user to admin
- **demote_admin(chat_id, user_id)**: Demote admin to user
- **ban_user(chat_id, user_id)**: Ban user
- **unban_user(chat_id, user_id)**: Unban user
- **get_invite_link(chat_id)**: Get invite link
- **export_chat_invite(chat_id)**: Export invite link
- **import_chat_invite(hash)**: Join chat by invite hash
- **join_chat_by_link(link)**: Join chat by invite link
- **subscribe_public_channel(channel)**: Subscribe to a public channel or supergroup by username or ID

### Messaging
- **get_messages(chat_id, page, page_size)**: Paginated messages
- **list_messages(chat_id, limit, search_query, from_date, to_date)**: Filtered messages
- **list_topics(chat_id, limit, offset_topic, search_query)**: List forum topics in supergroups
- **send_message(chat_id, message)**: Send a message
- **reply_to_message(chat_id, message_id, text)**: Reply to a message
- **edit_message(chat_id, message_id, new_text)**: Edit your message
- **delete_message(chat_id, message_id)**: Delete a message
- **forward_message(from_chat_id, message_id, to_chat_id)**: Forward a message
- **pin_message(chat_id, message_id)**: Pin a message
- **unpin_message(chat_id, message_id)**: Unpin a message
- **mark_as_read(chat_id)**: Mark all as read
- **get_message_context(chat_id, message_id, context_size)**: Context around a message
- **get_history(chat_id, limit)**: Full chat history
- **get_pinned_messages(chat_id)**: List pinned messages
- **get_last_interaction(contact_id)**: Most recent message with a contact
- **create_poll(chat_id, question, options, multiple_choice, quiz_mode, public_votes, close_date)**: Create a poll
- **list_inline_buttons(chat_id, message_id, limit)**: Inspect inline keyboards to discover button text/index
- **press_inline_button(chat_id, message_id, button_text, button_index)**: Trigger inline keyboard callbacks by label or index
-  **send_reaction(chat_id, message_id, emoji, big=False)**: Add a reaction to a message
-  **remove_reaction(chat_id, message_id)**: Remove a reaction from a message
-  **get_message_reactions(chat_id, message_id, limit=50)**: Get all reactions on a message

### Contact Management
- **list_contacts()**: List all contacts
- **search_contacts(query)**: Search contacts
- **add_contact(phone, first_name, last_name)**: Add a contact
- **delete_contact(user_id)**: Delete a contact
- **block_user(user_id)**: Block a user
- **unblock_user(user_id)**: Unblock a user
- **import_contacts(contacts)**: Bulk import contacts
- **export_contacts()**: Export all contacts as JSON
- **get_blocked_users()**: List blocked users
- **get_contact_ids()**: List all contact IDs
- **get_direct_chat_by_contact(contact_query)**: Find direct chat with a contact
- **get_contact_chats(contact_id)**: List all chats with a contact

### User & Profile
- **get_me()**: Get your user info
- **update_profile(first_name, last_name, about)**: Update your profile
- **set_profile_photo(file_path)**: Set a profile photo from an allowed root path
- **delete_profile_photo()**: Remove your profile photo
- **get_user_photos(user_id, limit)**: Get a user's profile photos
- **get_user_status(user_id)**: Get a user's online status

### Media
- **get_media_info(chat_id, message_id)**: Get info about media in a message
- **send_file(chat_id, file_path, caption)**: Send a local file from allowed roots
- **download_media(chat_id, message_id, file_path)**: Save message media under allowed roots
- **upload_file(file_path)**: Upload a local file and return upload metadata
- **send_voice(chat_id, file_path)**: Send `.ogg/.opus` voice note from allowed roots
- **send_sticker(chat_id, file_path)**: Send `.webp` sticker from allowed roots
- **edit_chat_photo(chat_id, file_path)**: Update chat photo from allowed roots

### Search & Discovery
- **search_public_chats(query, limit)**: Search public chats/channels/bots with a configurable result limit
- **search_messages(chat_id, query, limit)**: Search messages in a chat
- **search_global(query, page, page_size)**: Search messages globally with pagination
- **resolve_username(username)**: Resolve a username to ID

### Stickers, GIFs, Bots
- **get_sticker_sets()**: List sticker sets
- **get_bot_info(bot_username)**: Get info about a bot
- **set_bot_commands(bot_username, commands)**: Set bot commands (bot accounts only)

### Privacy, Settings, and Misc
- **get_privacy_settings()**: Get privacy settings
- **set_privacy_settings(key, allow_users, disallow_users)**: Set privacy settings
- **mute_chat(chat_id)**: Mute notifications
- **unmute_chat(chat_id)**: Unmute notifications
- **archive_chat(chat_id)**: Archive a chat
- **unarchive_chat(chat_id)**: Unarchive a chat
- **get_recent_actions(chat_id)**: Get recent admin actions

### Drafts
- **save_draft(chat_id, message, reply_to_msg_id, no_webpage)**: Save a draft message to a chat/channel
- **get_drafts()**: Get all draft messages across all chats
- **clear_draft(chat_id)**: Clear/delete a draft from a specific chat

### Input Validation

To improve robustness, all functions accepting `chat_id` or `user_id` parameters now include input validation. You can use any of the following formats for these IDs:

-   **Integer ID**: The direct integer ID for a user, chat, or channel (e.g., `123456789` or `-1001234567890`).
-   **String ID**: The integer ID provided as a string (e.g., `"123456789"`).
-   **Username**: The public username for a user or channel (e.g., `"@username"` or `"username"`).

The server will automatically validate the input and convert it to the correct format before making a request to Telegram. If the input is invalid, a clear error message will be returned.

## File-path Tools Security Model

File-path tools are available, but **disabled by default** until allowed roots are configured.

Supported file-path tools:
- `send_file`, `download_media`, `set_profile_photo`, `edit_chat_photo`, `send_voice`, `send_sticker`, `upload_file`

Security semantics (aligned with MCP filesystem server):
- Server-side allowlist via CLI positional arguments (fallback when Roots API is unsupported).
- Client-provided MCP Roots replace the server allowlist when available.
- If the client returns an empty Roots list, file-path tools are disabled (deny-all).
- All paths are resolved via realpath and must stay inside an allowed root.
- Traversal/glob-like patterns are rejected (`..`, `*`, `?`, `~`, etc.).
- Relative paths resolve against the first allowed root.
- Write tools default to `<first_root>/downloads/` when `file_path` is omitted.

Example server launch with allowlisted roots:
```bash
uv --directory /full/path/to/telegram-mcp run telegram-mcp /data/telegram /tmp/telegram-mcp
```

GIF tools are currently limited: `get_gif_search` and `send_gif` are available, while `get_saved_gifs` is not implemented due to reliability limits in Telethon/Telegram API interactions.

---

## 📋 Requirements
- Python 3.10+
- [Telethon](https://docs.telethon.dev/)
- [MCP Python SDK](https://modelcontextprotocol.io/docs/)
- [Claude Desktop](https://claude.ai/desktop) or [Cursor](https://cursor.so/) (or any MCP client)

---

## 🔧 Installation & Setup

### 1. Fork & Clone

```bash
git clone https://github.com/chigwell/telegram-mcp.git
cd telegram-mcp
```

### 2. Install Dependencies with uv

```bash
uv sync
```

### 3. Generate a Session String

```bash
uv run session_string_generator.py
```
Follow the prompts to authenticate and update your `.env` file.

### 4. Configure .env

Copy `.env.example` to `.env` and fill in your values:

```
TELEGRAM_API_ID=your_api_id_here
TELEGRAM_API_HASH=your_api_hash_here
TELEGRAM_SESSION_NAME=anon
TELEGRAM_SESSION_STRING=your_session_string_here
```
Get your API credentials at [my.telegram.org/apps](https://my.telegram.org/apps).

---

## 🐳 Running with Docker

If you have Docker and Docker Compose installed, you can build and run the server in a container, simplifying dependency management.

### 1. Build the Image

From the project root directory, build the Docker image:

```bash
docker build -t telegram-mcp:latest .
```

### 2. Running the Container

You have two options:

**Option A: Using Docker Compose (Recommended for Local Use)**

This method uses the `docker-compose.yml` file and automatically reads your credentials from a `.env` file.

1.  **Create `.env` File:** Ensure you have a `.env` file in the project root containing your `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `TELEGRAM_SESSION_STRING` (or `TELEGRAM_SESSION_NAME`). Use `.env.example` as a template.
2.  **Run Compose:**
    ```bash
    docker compose up --build
    ```
    *   Use `docker compose up -d` to run in detached mode (background).
    *   Press `Ctrl+C` to stop the server.

**Option B: Using `docker run`**

You can run the container directly, passing credentials as environment variables.

```bash
docker run -it --rm \
  -e TELEGRAM_API_ID="YOUR_API_ID" \
  -e TELEGRAM_API_HASH="YOUR_API_HASH" \
  -e TELEGRAM_SESSION_STRING="YOUR_SESSION_STRING" \
  telegram-mcp:latest
```
*   Replace placeholders with your actual credentials.
*   Use `-e TELEGRAM_SESSION_NAME=your_session_file_name` instead of `TELEGRAM_SESSION_STRING` if you prefer file-based sessions (requires volume mounting, see `docker-compose.yml` for an example).
*   The `-it` flags are crucial for interacting with the server.

---

## ⚙️ Configuration for Claude & Cursor

### MCP Configuration
Edit your Claude desktop config (e.g. `~/Library/Application Support/Claude/claude_desktop_config.json`) or Cursor config (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "telegram-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/full/path/to/telegram-mcp",
        "run",
        "telegram-mcp"
      ]
    }
  }
}
```

## 🏗️ Architecture

This repository strictly follows a **Hexagonal Architecture** (Ports and Adapters) pattern to ensure complete decoupling between the MCP interface, business logic, and Telegram infrastructure.

### Layers

- **Tools (MCP Interface):** Mapped inside `src/telegram_mcp/mcp_server.py`. These entry points act purely as a facade. They receive requests, validate MCP inputs, and immediately delegate to the Domain layer.
- **Domain Services:** Separated by business entities (e.g., `chats.py`, `messages.py`, `contacts.py`). This layer contains all core business logic and orchestrates Telethon methods, remaining completely agnostic of the MCP server.
- **Infrastructure Layer:** Core connections handle graceful setups and teardowns (e.g., `client.py` using `telegram_client_lifespan`).

### Adding a New Tool (The Golden Rule)

**❌ Anti-Pattern:**
Never use direct `Telethon` client calls inside a tool definition.
```python
@mcp.tool()
async def send_message(chat_id: int, message: str):
    entity = await client.get_entity(chat_id)  # Breaking the boundary!
    await client.send_message(entity, message)
```

**✅ Correct Pattern:**
```python
@mcp.tool()
async def send_message(chat_id: int, message: str) -> str:
    from telegram_mcp import messages
    return await messages.send_message(chat_id, message) # Delegating to domain service!
```

---

## 🛠️ Development Workflow

Scaling and maintaining this project properly requires validating your code against our automated workflows before creating a Pull Request.

### 1. Run Tests
We use a comprehensive suite of mocked tests.
```bash
uv run pytest
```

### 2. Linting and Formatting
Ensure your code is clean and adheres to the project's formatting specifications.
```bash
uv run black src/
uv run flake8 src/
```
*(We use `black` for formatting and `flake8` for syntax/style checking).*

### 3. Pre-PR Checklist
If you modify this server:
- [ ] Tests must pass locally (`pytest`).
- [ ] Code must be formatted and linted properly.
- [ ] You must respect the architectural boundaries (no raw Telethon client logic inside `mcp_server.py`).

> **Rule of Thumb:** If the CI pipeline fails and this README doesn't tell you how to avoid it or reproduce it locally, the README is broken. Please open an issue.

---

## 🎮 Natural Language Usage Usage Examples

- "Show my recent chats"
- "Send 'Hello world' to chat 123456789"
- "Add contact with phone +1234567890, name John Doe"
- "Create a group 'Project Team' with users 111, 222, 333"
- "Download the media from message 42 in chat 123456789"
- "Mute notifications for chat 123456789"
- "Promote user 111 to admin in group 123456789"
- "Search for public channels about 'news'"
- "Join the Telegram group with invite link https://t.me/+AbCdEfGhIjK"
- "Send a sticker to my Saved Messages"
- "Get all my sticker sets"

You can use these tools via natural language in Claude, Cursor, or any MCP-compatible client.

---

## 🧠 Error Handling & Robustness

This implementation includes comprehensive error handling:

- **Session management**: Works with both file-based and string-based sessions
- **Error reporting**: Detailed errors logged to `mcp_errors.log`
- **Graceful degradation**: Multiple fallback approaches for critical functions
- **User-friendly messages**: Clear, actionable error messages instead of technical errors
- **Account type detection**: Functions that require bot accounts detect and notify when used with user accounts
- **Invite link processing**: Handles various link formats and already-member cases

The code is designed to be robust against common Telegram API issues and limitations.

---

## 🛠️ Contribution Guide

1. **Fork this repo:** [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp)
2. **Clone your fork:**
   ```bash
   git clone https://github.com/<your-github-username>/telegram-mcp.git
   ```
3. **Create a new branch:**
   ```bash
   git checkout -b my-feature
   ```
4. **Make your changes, add tests/docs if needed.**
5. **Push and open a Pull Request** to [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) with a clear description.
6. **Tag @chigwell or @l1v0n1** in your PR for review.

---

## 🔒 Security Considerations
- **Never commit your `.env` or session string.**
- The session string gives full access to your Telegram account—keep it safe!
- All processing is local; no data is sent anywhere except Telegram's API.
- Use `.env.example` as a template and keep your actual `.env` file private.
- Test files are automatically excluded in `.gitignore`.

---

## 🛠️ Troubleshooting
- **Check logs** in your MCP client (Claude/Cursor) and the terminal for errors.
- **Detailed error logs** can be found in `mcp_errors.log`.
- **Interpreter errors?** Make sure your `.venv` is created and selected.
- **Database lock?** Handled internally via `telegram_client_lifespan`. If you still see this, ensure the server is shutting down cleanly and avoid running multiple concurrent clients pointing to the same session simultaneously.
- **iCloud/Dropbox issues?** Move your project to a local path without spaces if you see odd errors.
- **Regenerate session string** if you change your Telegram password or see auth errors.
- **Bot-only functions** will show clear messages when used with regular user accounts.
- **Test script failures?** Check test configuration in `.env` for valid test accounts/groups.

---

## 📄 License

This project is licensed under the [Apache 2.0 License](LICENSE).

---

## 🙏 Acknowledgements
- [Telethon](https://github.com/LonamiWebs/Telethon)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Claude](https://www.anthropic.com/) and [Cursor](https://cursor.so/)
- [chigwell/telegram-mcp](https://github.com/chigwell/telegram-mcp) (upstream)

---

**Maintained by [@chigwell](https://github.com/chigwell) and [@l1v0n1](https://github.com/l1v0n1). PRs welcome!**

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=chigwell/telegram-mcp&type=Date)](https://www.star-history.com/#chigwell/telegram-mcp&Date)

## Contributors

<a href="https://github.com/chigwell/telegram-mcp/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=chigwell/telegram-mcp" />
</a>
