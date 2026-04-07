---
name: Update Telegram MCP Tools
description: Safely update MCP tool configuration and handle lifecycle, git workflow, and validation aligned with current architecture.
---

# Telegram MCP Update Skill

## When to use
- Updating `ALLOWED_TOOLS`
- Modifying MCP configuration
- Preparing changes for commit/PR

---

## Core Principles

1. **Lifecycle is internal**
   - The server manages Telethon via `telegram_client_lifespan`
   - Never kill processes manually

2. **Separation of concerns**
   - Config changes ≠ runtime fixes
   - Do not patch runtime behavior externally

3. **Environment awareness**
   - Do not assume git remotes, branches, or repo structure
   - Always inspect before acting

---

## Procedure

### 1. Update Configuration
Modify `ALLOWED_TOOLS` in `mcp_config.json` or `claude_desktop_config.json`.

### 2. Apply Changes
- Ask user to refresh MCP server via their MCP client UI.
- Do not restart processes manually.
- Do not use `pkill`.

### 3. Validate Behavior
- Confirm tools are available.
- If failure occurs:
  - Check logs (`mcp_errors.log` or server output).
  - Do not apply external fixes (no process killing).

### 4. Git Workflow (Adaptive)

1. **Inspect repo:**
   ```bash
   git remote -v
   ```

2. **Branch:**
   ```bash
   git checkout -b feat/<change-name>
   ```

3. **Commit:**
   ```bash
   git commit -m "feat: <clear description>"
   ```

4. **Push:**
   Use existing remote:
   ```bash
   git push origin feat/<change-name>
   ```

5. **Pull Request (PR):**
   - If `upstream` exists → target upstream.
   - Else → target `origin`.

---

## Anti-Patterns (Forbidden)
- Using `pkill` or killing processes manually.
- Direct Telethon manipulation outside the infrastructure layer.
- Assuming git remotes (`fork`, `upstream`) without confirming via `git remote -v`.
- Mixing languages in system instructions (English ONLY).

---

## Expected Outcome
- Clean config update.
- No orphan processes.
- Deterministic agent behavior.
- Repo-agnostic workflow.
