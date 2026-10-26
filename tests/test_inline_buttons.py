"""Keyboard model, list_inline_buttons and press_inline_button, without Telegram connections."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import telethon
from telethon.tl import functions, types

from telegram_mcp.keyboards import keyboard_of
from telegram_mcp.tools import messages

DATE = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
ICON_ID = 5368324170671202286


def _inline(text, button_type, style=None):
    return types.KeyboardInlineButton(text=text, type=button_type, style=style)


def _inline_markup(*rows):
    return types.ReplyInlineMarkup(
        rows=[types.KeyboardInlineButtonRow(buttons=list(row)) for row in rows]
    )


def _message(msg_id, markup):
    return SimpleNamespace(id=msg_id, date=DATE, reply_markup=markup)


YES = _inline("Yes", types.InlineButtonTypeCallback(data=b"yes"))
NO = _inline("No", types.InlineButtonTypeCallback(data=b"no"))
SITE = _inline("Site", types.InlineButtonTypeUrl(url="https://example.com"))
VOTE = _inline_markup([YES, NO], [SITE])


def _records(message):
    return [button.record() for button in keyboard_of(message).buttons]


# --- the model ---


@pytest.mark.parametrize(
    "button_type, kind, extra",
    [
        (types.InlineButtonTypeCallback(data=b"x"), "callback", {"has_callback": True}),
        (
            types.InlineButtonTypeCallback(data=b"x", requires_password=True),
            "callback",
            {"has_callback": True, "requires_password": True},
        ),
        (types.InlineButtonTypeUrl(url="https://a.b"), "url", {"url": "https://a.b"}),
        (
            types.InlineButtonTypeSwitchInline(query="cats", same_peer=True),
            "switch_inline",
            {"query": "cats", "same_peer": True},
        ),
        (types.InlineButtonTypeGame(), "game", {}),
        (types.InlineButtonTypeBuy(), "buy", {}),
        (
            types.InlineButtonTypeUrlAuth(url="https://login.b", button_id=3),
            "url_auth",
            {"url": "https://login.b"},
        ),
        (types.InlineButtonTypeWebView(url="https://app.b"), "web_view", {"url": "https://app.b"}),
        (types.InlineButtonTypeCopy(copy_text="PROMO-42"), "copy", {"copy_text": "PROMO-42"}),
        (types.InlineButtonTypeUserProfile(user_id=777), "user_profile", {"user_id": 777}),
        (types.InlineButtonTypeDisabled(), "disabled", {}),
    ],
)
def test_inline_button_kinds(button_type, kind, extra):
    [record] = _records(_message(1, _inline_markup([_inline("B", button_type)])))
    expected = {"index": 0, "row": 0, "col": 0, "text": "B", "kind": kind}
    expected["has_callback"] = False
    expected.update(extra)
    assert record == expected


@pytest.mark.parametrize(
    "button_type, kind, extra",
    [
        (types.ButtonTypeDefault(), "text", {}),
        (types.ButtonTypeRequestPhone(), "request_phone", {}),
        (types.ButtonTypeRequestGeoLocation(), "request_location", {}),
        (types.ButtonTypeRequestPoll(quiz=True), "request_poll", {"quiz": True}),
        (
            types.ButtonTypeRequestPeer(
                button_id=1, peer_type=types.RequestPeerTypeUser(), max_quantity=1
            ),
            "request_peer",
            {},
        ),
        (types.ButtonTypeSimpleWebView(url="https://app.b"), "web_app", {"url": "https://app.b"}),
    ],
)
def test_reply_keyboard_button_kinds(button_type, kind, extra):
    markup = types.ReplyKeyboardMarkup(
        rows=[types.KeyboardButtonRow(buttons=[types.KeyboardButton(text="B", type=button_type)])]
    )
    keyboard = keyboard_of(_message(1, markup))
    assert keyboard.keyboard == "reply"
    assert keyboard.buttons[0].record() == {
        "index": 0,
        "row": 0,
        "col": 0,
        "text": "B",
        "kind": kind,
        "has_callback": False,
        **extra,
    }


def test_rows_columns_and_flat_index():
    assert [(r["index"], r["row"], r["col"], r["text"]) for r in _records(_message(1, VOTE))] == [
        (0, 0, 0, "Yes"),
        (1, 0, 1, "No"),
        (2, 1, 0, "Site"),
    ]


def test_style_and_icon():
    style = types.KeyboardButtonStyle(bg_danger=True, icon=ICON_ID)
    button = _inline("Delete", types.InlineButtonTypeCallback(data=b"d"), style=style)
    [record] = _records(_message(1, _inline_markup([button])))
    assert record["style"] == "danger"
    assert record["icon_emoji_id"] == str(ICON_ID)


@pytest.mark.parametrize(
    "markup",
    [
        None,
        types.ReplyKeyboardHide(),
        types.ReplyKeyboardForceReply(),
        types.ReplyInlineMarkup(rows=[]),
    ],
)
def test_no_buttons_means_no_keyboard(markup):
    assert keyboard_of(_message(1, markup)) is None


# --- the tools ---


@pytest.fixture
def client(monkeypatch):
    cl = AsyncMock()
    cl.history = []
    cl.by_id = {}

    async def get_messages(entity, ids=None, limit=None):
        if ids is not None:
            return cl.by_id.get(ids)
        return cl.history[:limit]

    cl.get_messages = AsyncMock(side_effect=get_messages)
    cl.return_value = types.messages.BotCallbackAnswer(cache_time=0)
    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "ensure_connected", AsyncMock())
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value="bot"))
    return cl


async def _list(**kw):
    return json.loads(await messages.list_inline_buttons(chat_id=42, **kw))


async def _press(**kw):
    return await messages.press_inline_button(chat_id=42, **kw)


def _sent_request(cl):
    return cl.await_args.args[0]


@pytest.mark.asyncio
async def test_list_reports_rows_kinds_and_keyboard(client):
    client.history = [_message(9, None), _message(8, VOTE)]
    result = await _list()
    assert result["message_id"] == 8
    assert result["keyboard"] == "inline"
    assert [(r["kind"], r["row"], r["col"]) for r in result["results"]] == [
        ("callback", 0, 0),
        ("callback", 0, 1),
        ("url", 1, 0),
    ]
    assert result["results"][2]["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_list_reports_a_reply_keyboard(client):
    markup = types.ReplyKeyboardMarkup(
        rows=[
            types.KeyboardButtonRow(
                buttons=[types.KeyboardButton(text="Menu", type=types.ButtonTypeDefault())]
            )
        ],
        single_use=True,
        placeholder="Pick one",
    )
    client.history = [_message(5, markup)]
    result = await _list()
    assert result["keyboard"] == "reply"
    assert result["single_use"] is True
    assert result["placeholder"] == "Pick one"
    assert result["results"][0]["kind"] == "text"


@pytest.mark.asyncio
async def test_list_by_id_falls_back_to_history_when_markup_is_missing(client):
    client.by_id = {8: _message(8, None)}
    client.history = [_message(8, VOTE)]
    result = await _list(message_id="8")
    assert result["message_id"] == 8
    assert len(result["results"]) == 3


@pytest.mark.asyncio
async def test_list_without_buttons(client):
    client.history = [_message(9, None)]
    assert (
        await messages.list_inline_buttons(chat_id=42) == "No message with inline buttons found."
    )
    client.by_id = {9: _message(9, None)}
    assert (
        await messages.list_inline_buttons(chat_id=42, message_id=9)
        == "Message 9 does not contain inline buttons."
    )


@pytest.mark.asyncio
async def test_press_sends_the_callback_data(client):
    client.history = [_message(8, VOTE)]
    client.return_value = types.messages.BotCallbackAnswer(cache_time=0, message="Counted")
    result = json.loads(await _press(button_text="  no "))
    request = _sent_request(client)
    assert isinstance(request, functions.messages.GetBotCallbackAnswerRequest)
    assert (request.msg_id, request.data, request.game) == (8, b"no", None)
    assert result == {"results": [], "response": "Counted", "alert": False}


@pytest.mark.asyncio
async def test_press_by_id_uses_the_history_copy_with_markup(client):
    # The fallback that a fetch by id needs in bot DMs: the button data is read
    # from the raw reply_markup of the message found in history.
    client.by_id = {8: _message(8, None)}
    client.history = [_message(8, VOTE)]
    await _press(message_id=8, button_index=0)
    assert _sent_request(client).data == b"yes"


@pytest.mark.asyncio
async def test_press_reports_alert_and_url(client):
    client.history = [_message(8, VOTE)]
    client.return_value = types.messages.BotCallbackAnswer(
        cache_time=0, alert=True, message="Sold out", url="https://t.me/bot?start=x"
    )
    result = json.loads(await _press(button_index=1))
    assert result["alert"] is True
    assert result["response"] == "Sold out Telegram displayed an alert to the user."
    assert result["url"] == "https://t.me/bot?start=x"


@pytest.mark.asyncio
async def test_press_a_game_button(client):
    game = _inline("Play", types.InlineButtonTypeGame())
    client.history = [_message(3, _inline_markup([game]))]
    await _press(button_text="Play")
    request = _sent_request(client)
    assert request.game is True and request.data is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "button, expected",
    [
        (SITE, "Selected button opens a URL instead of sending a callback: https://example.com"),
        (
            _inline("Code", types.InlineButtonTypeCopy(copy_text="PROMO-42")),
            "Selected button copies text instead of sending a callback: PROMO-42",
        ),
        (
            _inline("Transfer", types.InlineButtonTypeCallback(data=b"t", requires_password=True)),
            "two-step verification password",
        ),
        (
            _inline("Share", types.InlineButtonTypeSwitchInline(query="x")),
            "Selected button (switch_inline) does not send a callback to press.",
        ),
    ],
)
async def test_press_refuses_buttons_without_a_plain_callback(client, button, expected):
    client.history = [_message(3, _inline_markup([button]))]
    result = await _press(button_index=0)
    assert expected in result
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_press_points_reply_keyboard_text_to_send_message(client):
    markup = types.ReplyKeyboardMarkup(
        rows=[
            types.KeyboardButtonRow(
                buttons=[types.KeyboardButton(text="Menu", type=types.ButtonTypeDefault())]
            )
        ]
    )
    client.history = [_message(5, markup)]
    assert "Use send_message with the button text" in await _press(button_text="menu")
    client.assert_not_awaited()


@pytest.mark.asyncio
async def test_press_chooses_buttons_or_explains(client):
    client.history = [_message(8, VOTE)]
    assert await _press(button_index=3) == "button_index out of range. Valid indices: 0-2."
    assert await _press(button_text="Maybe") == (
        "Button not found. Available buttons: [0] Yes, [1] No, [2] Site"
    )
    assert await _press() == "Provide button_text or button_index to choose a button."
    assert await _press(button_index="x") == "button_index must be an integer."
    client.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, expected",
    [
        (telethon.errors.BotResponseTimeoutError(request=None), "did not answer the button press"),
        (telethon.errors.DataInvalidError(request=None), "Call list_inline_buttons again."),
        (
            telethon.errors.MessageIdInvalidError(request=None),
            "Message 8 is no longer in this chat.",
        ),
    ],
)
async def test_press_explains_callback_errors(client, error, expected):
    client.history = [_message(8, VOTE)]
    client.side_effect = error
    assert expected in await _press(button_index=0)
