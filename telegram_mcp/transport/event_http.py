"""Attach durable webhook delivery to the HTTP application's existing lifespan."""

from contextlib import asynccontextmanager
import os
from pathlib import Path

from telegram_mcp.event_protocol import install_events
from telegram_mcp.event_webhooks import MessageSubscriptions


def with_events(app, server):
    protocol = install_events(server)
    if not protocol.catalog()["events"]:
        return app
    state = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = os.getenv("TELEGRAM_MCP_EVENTS_FILE") or state / "telegram-mcp" / "subscriptions.json"
    subscriptions = MessageSubscriptions(path)
    app.state.telegram_webhooks = subscriptions
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        try:
            if subscriptions.store.path.exists():
                await subscriptions.start()
            async with original_lifespan(app) as context:
                yield context
        finally:
            await subscriptions.close()

    app.router.lifespan_context = lifespan
    return app
