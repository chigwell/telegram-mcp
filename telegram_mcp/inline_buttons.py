"""Keyboard inspection shared by inline-button tools.

Fetching and selecting a message stays in each tool: pressing a button has an
additional recent-message fallback that listing deliberately does not use.
"""


def has_inline_buttons(message):
    if getattr(message, "buttons", None):
        return True
    markup = getattr(message, "reply_markup", None)
    return bool(markup and hasattr(markup, "rows"))


def flatten_inline_buttons(message):
    buttons = getattr(message, "buttons", None)
    if buttons:
        return [button for row in buttons for button in row]
    markup = getattr(message, "reply_markup", None)
    if markup and hasattr(markup, "rows"):
        return [button for row in markup.rows for button in row.buttons]
    return []
