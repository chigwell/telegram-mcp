"""MCPL (MCP Live) protocol support for the Telegram MCP server.

This subpackage layers the MCPL extension on top of the standard MCP server
provided by FastMCP. The standard MCP tool surface (send_message, list_chats,
…) is unchanged; MCPL adds:

  - capability advertisement (experimental.mcpl in initialize)
  - channel registration (Telegram dialogs → MCPL channels)
  - push events (Telethon NewMessage → channels/incoming)
  - host → server publish (channels/publish → Telethon send_message)
  - lifecycle methods (channels/list/open/close, channels/typing)

Reference: mcpl/SPEC.md in the connectome-typescript monorepo, and the Java
precedent at discord-mcp/src/main/java/dev/saseq/mcpl/.

Resilience model:

  - Telethon transport disconnects (network blip): handled by chigwell's
    existing ensure_connected / _force_reconnect in runtime.py. Event
    handlers are attached to the TelegramClient instance and persist
    across reconnects, so message flow resumes automatically.
  - Host process restart: the host re-issues `initialize` and
    `notifications/initialized`. The transport's on_ready re-fires;
    `attach_event_handlers` is idempotent (per-client sentinel) so
    NewMessage isn't pushed twice. channels/register is re-emitted with
    the freshly-enumerated dialog list.
  - Long disconnect with channel-set drift: Telegram doesn't replay
    missed updates after a long offline window. Use `channels/list` from
    the host to re-fetch the dialog set on demand.
"""

MCPL_VERSION = "0.4"
"""Protocol version advertised in capabilities.experimental.mcpl.version."""
