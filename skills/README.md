# Telegram MCP Skills & Workflows

A standardized library of skills, practical workflows, and integration guides for the [Telegram MCP Server](https://github.com/chigwell/telegram-mcp).

This documentation is designed for developers, agent integrators, and AI agents (such as Claude Code, Cursor, Codex, and Antigravity) seeking reliable, copy-paste-ready patterns to interact with Telegram via the Model Context Protocol.

---

## What is a "Skill" in Telegram MCP?

In this repository, a **Skill** represents a structured, reproducible agent workflow combining one or more Telegram MCP tools to accomplish an end-to-end task safely. Each skill provides:
- Concrete tool calls with required parameters.
- Explicit safety and privacy guardrails (e.g., read-only execution, drafting without sending).
- Step-by-step prompt-to-response sequences.
- Practical failure modes and recovery procedures.

---

## Quick Navigation

### Core Integration Guide
- [**How to Use Telegram MCP**](how-to-use-telegram-mcp.md): Complete setup, authentication, environment configuration, MCP client connections (Claude Desktop, Cursor, generic stdio), server capabilities, and privacy architecture.

### Practical Workflow Examples
| Workflow | Focus / Primary Tools | Safety Level | Document |
| :--- | :--- | :--- | :--- |
| **Summarize Unread Messages** | `list_chats(unread_only=True)`, `get_messages` | Read-Only | [`summarize-latest-unread.md`](examples/summarize-latest-unread.md) |
| **Draft Replies Without Sending** | `get_message_context`, `save_draft` (or text draft) | Safe / Non-transmitting | [`draft-replies-without-sending.md`](examples/draft-replies-without-sending.md) |
| **Triage & Action Items** | `list_chats`, `get_messages`, `set_contact_alias` | Read-Only | [`triage-and-action-items.md`](examples/triage-and-action-items.md) |
| **Search Chat & Context Summarization** | `list_messages(search_query=...)`, `get_message_context` | Read-Only | [`search-chat-and-summarize-context.md`](examples/search-chat-and-summarize-context.md) |

---

## Pick a Workflow

### 1. "Catch me up on what I missed"
- **Goal:** Get an executive briefing of all unread messages across direct chats and channels without marking them as read.
- **Guide:** Follow [`skills/examples/summarize-latest-unread.md`](examples/summarize-latest-unread.md).

### 2. "Draft a polite reply to Alex, but don't send anything yet"
- **Goal:** Review recent conversation context and produce a draft for human approval or place it in the Telegram client's input box as a draft.
- **Guide:** Follow [`skills/examples/draft-replies-without-sending.md`](examples/draft-replies-without-sending.md).

### 3. "Extract deadlines, action items, and triage urgent chats"
- **Goal:** Scan active conversations for urgent questions, blockers, and assigned tasks, categorized by urgency.
- **Guide:** Follow [`skills/examples/triage-and-action-items.md`](examples/triage-and-action-items.md).

### 4. "Find where we discussed the API credentials migration"
- **Goal:** Search message history across or within specific chats and reconstruct surrounding conversational context.
- **Guide:** Follow [`skills/examples/search-chat-and-summarize-context.md`](examples/search-chat-and-summarize-context.md).

---

## Standard Skill Documentation Template

All skills in this directory adhere to a 10-section standard template:
1. **Skill Name**
2. **Purpose**
3. **When to Use / Use Cases**
4. **Prerequisites / Configuration**
5. **MCP Tools Used** (names, parameters, notes)
6. **Inputs** (agent/user inputs)
7. **Outputs** (structured results)
8. **Step-by-step Example** (prompt $\rightarrow$ tool calls $\rightarrow$ response)
9. **Safety / Privacy Notes** (allowlist, read-only mode, untrusted user content)
10. **Failure Modes & Recovery** (auth errors, rate limits, access denied)
