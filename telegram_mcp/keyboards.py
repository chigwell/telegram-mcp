"""One model of a message's keyboard, shared by the tools that read and press buttons.

Since layer 229 a button carries its behaviour in button.type (data, url, query)
and its look in button.style. The model reads both straight from the raw
reply_markup, so it does not depend on Telethon's MessageButton wrapper, which a
message fetched by id may lack.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from telethon.tl import types

from sanitize import sanitize_user_content

INLINE_KEYBOARD = "inline"
REPLY_KEYBOARD = "reply"

# What pressing the button does, by button type. Inline buttons sit under a
# message; reply-keyboard buttons replace the user's keyboard.
_KINDS = {
    types.InlineButtonTypeCallback: "callback",
    types.InlineButtonTypeUrl: "url",
    types.InlineButtonTypeSwitchInline: "switch_inline",
    types.InlineButtonTypeGame: "game",
    types.InlineButtonTypeBuy: "buy",
    types.InlineButtonTypeUrlAuth: "url_auth",
    types.InlineButtonTypeWebView: "web_view",
    types.InlineButtonTypeCopy: "copy",
    types.InlineButtonTypeUserProfile: "user_profile",
    types.InlineButtonTypeDisabled: "disabled",
    types.ButtonTypeDefault: "text",
    types.ButtonTypeRequestPhone: "request_phone",
    types.ButtonTypeRequestGeoLocation: "request_location",
    types.ButtonTypeRequestPoll: "request_poll",
    types.ButtonTypeRequestPeer: "request_peer",
    types.ButtonTypeSimpleWebView: "web_app",
}

# Kinds whose press is a callback query to the bot (messages.getBotCallbackAnswer).
CALLBACK_KINDS = frozenset({"callback", "game"})

_STYLES = (("bg_primary", "primary"), ("bg_danger", "danger"), ("bg_success", "success"))
_TEXT_LIMIT = 256


@dataclass(frozen=True)
class Button:
    """One button: its place, what it does, and the raw button for pressing it."""

    index: int
    row: int
    col: int
    kind: str
    raw: Any

    @property
    def text(self) -> str:
        return getattr(self.raw, "text", "") or ""

    @property
    def requires_password(self) -> bool:
        return bool(getattr(self.raw.type, "requires_password", False))

    def record(self) -> Dict[str, Any]:
        """JSON record of the button. User-controlled text is sanitized."""
        button_type = self.raw.type
        record = {
            "index": self.index,
            "row": self.row,
            "col": self.col,
            "text": sanitize_user_content(self.text or "<no text>", max_length=_TEXT_LIMIT),
            "kind": self.kind,
            # Kept from earlier releases: callback data is present.
            "has_callback": self.kind == "callback",
        }
        url = getattr(button_type, "url", None)
        if url:
            record["url"] = url
        if self.kind == "callback" and self.requires_password:
            record["requires_password"] = True
        if self.kind == "switch_inline":
            record["query"] = sanitize_user_content(
                button_type.query or "", max_length=_TEXT_LIMIT
            )
            record["same_peer"] = bool(button_type.same_peer)
        if self.kind == "copy":
            record["copy_text"] = sanitize_user_content(
                button_type.copy_text, max_length=_TEXT_LIMIT
            )
        if self.kind == "user_profile":
            record["user_id"] = button_type.user_id
        if self.kind == "request_poll" and button_type.quiz is not None:
            record["quiz"] = bool(button_type.quiz)
        if self.kind == "unknown":
            record["type"] = type(button_type).__name__
        record.update(_style_fields(getattr(self.raw, "style", None)))
        return record


@dataclass(frozen=True)
class Keyboard:
    """A message's keyboard: inline (under the message) or reply (the user's keyboard)."""

    keyboard: str
    buttons: List[Button]
    markup: Any

    def metadata(self) -> Dict[str, Any]:
        """Keyboard-level fields for a tool result."""
        fields: Dict[str, Any] = {"keyboard": self.keyboard}
        if self.keyboard == REPLY_KEYBOARD:
            placeholder = getattr(self.markup, "placeholder", None)
            if placeholder:
                fields["placeholder"] = sanitize_user_content(placeholder, max_length=_TEXT_LIMIT)
            fields["single_use"] = bool(getattr(self.markup, "single_use", False))
        return fields


def _style_fields(style) -> Dict[str, Any]:
    if style is None:
        return {}
    fields = {}
    for attribute, name in _STYLES:
        if getattr(style, attribute, None):
            fields["style"] = name
            break
    if getattr(style, "icon", None):
        # A custom emoji document id, like a forum topic's icon_emoji_id.
        fields["icon_emoji_id"] = str(style.icon)
    return fields


def keyboard_of(message) -> Optional[Keyboard]:
    """The message's keyboard, or None when it has no buttons to show."""
    markup = getattr(message, "reply_markup", None)
    if isinstance(markup, types.ReplyInlineMarkup):
        keyboard = INLINE_KEYBOARD
    elif isinstance(markup, types.ReplyKeyboardMarkup):
        keyboard = REPLY_KEYBOARD
    else:
        return None  # no markup, or one that only hides the keyboard or forces a reply
    buttons = []
    for row_index, row in enumerate(markup.rows or []):
        for col_index, raw in enumerate(row.buttons or []):
            kind = _KINDS.get(type(getattr(raw, "type", None)), "unknown")
            buttons.append(Button(len(buttons), row_index, col_index, kind, raw))
    if not buttons:
        return None
    return Keyboard(keyboard, buttons, markup)
