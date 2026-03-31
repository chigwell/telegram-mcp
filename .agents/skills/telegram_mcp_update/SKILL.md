---
name: Update Telegram MCP Tools
description: Procedure for updating the 'ALLOWED_TOOLS' environment variable in the Telegram MCP server configuration to prevent SQLite database lock issues and ensure the GUI correctly reloads the server.
---

# Updating Telegram MCP Tools

**Use this skill when** the user requests updating the allowed tools for their `telegram-mcp` server or changing its configuration in `mcp_config.json`.

Follow this precise procedure to prevent orphaned Python processes from locking the SQLite database. If the database is locked during an auto-reload, the server crashes and disappears from the IDE's MCP manager.

## Procedure

1. **Update Configuration**:
   Edit the `ALLOWED_TOOLS` environment variable inside the `telegram-mcp` section of `mcp_config.json` with the new comma-separated tools list.

2. **Kill Orphaned Processes**:
   Whenever `mcp_config.json` is modified for this server, the IDE tries to restart it automatically. Because Telethon/Python does not always shut down cleanly, the old process stays in the background locking `telegram_session.session`.
   You **MUST** proactively run the following command to terminate hanging instances BEFORE asking the user to verify (adjust the string `telegram-mcp` if the user named the directory differently):
   ```bash
   pkill -f "telegram-mcp"
   ```

3. **Instruct the User to Refresh**:
   Inform the user what tools were updated and explicitly ask them to click the **"Refresh"** button in their "Manage MCP servers" UI. Since the database lock was cleared by you, the server will restart correctly and reappear immediately.
