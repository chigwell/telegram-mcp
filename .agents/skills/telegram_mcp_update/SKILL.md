---
name: Update Telegram MCP Tools
description: Procedure for updating the 'ALLOWED_TOOLS' environment variable in the Telegram MCP server configuration, preventing SQLite lock issues, and contributing upstream.
---

# Updating Telegram MCP Tools

**Use this skill when** the user requests updating the allowed tools for their `telegram-mcp` server, changing its configuration, or sharing changes upstream via Pull Request.

Follow this precise procedure to prevent orphaned Python processes from locking the SQLite database or to push changes cleanly to the Open Source project.

## Procedure

1. **Update Configuration**:
   Edit the `ALLOWED_TOOLS` environment variable inside the `telegram-mcp` section of `mcp_config.json` with the new comma-separated tools list.

2. **Kill Orphaned Processes**:
   Whenever config is modified, the IDE tries to restart the MCP automatically. Because Telethon/Python does not always shut down cleanly, the old process stays in the background locking `telegram_session.session`.
   You **MUST** proactively run the following command to terminate hanging instances BEFORE asking the user to verify (adjust the string `telegram-mcp` if the user named the directory differently):
   ```bash
   pkill -f "telegram-mcp"
   ```

3. **Instruct the User to Refresh**:
   Inform the user what tools were updated and explicitly ask them to click the **"Refresh"** button in their "Manage MCP servers" UI. The server will restart correctly.

4. **Contributing Upstream (Pull Requests)**:
   Si el usuario pide subir el código para aportar al proyecto original (`upstream`), aplica esta secuencia estandarizada:
   - Revisa si el remoto secundario ya existe. Si origin es el oficial y el usuario no tiene permisos: asegúrate de tener el alias `fork` configurado apuntando a su bifurcación personal (Ej. `nhomar/telegram-mcp`).
   - Genera una rama nueva: `git checkout -b feat/nombre-del-cambio`.
   - Efectúa el `commit` documentando las razones concretas del cambio.
   - Sube forzosamente los cambios al *fork* del usuario: `git push -f fork feat/nombre-del-cambio`.
   - Emplea el cliente oficial de GitHub ubicando siempre explícitamente el repositorio principal `-R chigwell/telegram-mcp` (o el correspondiente) para levantar el PR oficial con formato documentado:
     ```bash
     gh pr create -R chigwell/telegram-mcp --title "Detalle formal del asunto" --body "Explicación integral del impacto de este merge"
     ```
