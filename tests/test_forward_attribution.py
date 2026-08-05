import datetime

from telegram_mcp.tools import messages
from telegram_mcp.tools.messages import message_to_dict


class _FwdHeader:
    """telethon.tl.types.MessageFwdHeader, trimmed to what the code reads."""

    def __init__(self, date=None, from_name=None, channel_post=None, post_author=None):
        self.date = date
        self.from_name = from_name
        self.channel_post = channel_post
        self.post_author = post_author
        self.from_id = object()  # a Peer; the code never inspects it directly


class _Chat:
    def __init__(self, title=None, username=None, first_name=None, last_name=None):
        self.title = title
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


class _Forward:
    """telethon.tl.custom.Forward — resolves the peer from response entities."""

    def __init__(self, chat=None, chat_id=None, sender=None):
        self.chat = chat
        self.chat_id = chat_id
        self.sender = sender


class _Msg:
    def __init__(self, fwd_from=None, forward=None):
        self.id = 184
        self.date = datetime.datetime(2026, 8, 5, 16, 38, 3, tzinfo=datetime.timezone.utc)
        self.message = "post body"
        self.sender = None
        self.fwd_from = fwd_from
        self.forward = forward


FWD_DATE = datetime.datetime(2026, 7, 15, 12, 1, 2, tzinfo=datetime.timezone.utc)


def test_public_channel_forward_keeps_attribution_and_builds_permalink():
    """from_name is empty on an ordinary channel forward — the origin is in from_id.

    Reading only from_name loses everything the Telegram UI shows as
    "Forwarded from ...", which is the common case rather than an edge one.
    """
    msg = _Msg(
        fwd_from=_FwdHeader(date=FWD_DATE, channel_post=6279),
        forward=_Forward(
            chat=_Chat(title="Полезный Парфун", username="ParfunA"), chat_id=-1001626974925
        ),
    )

    fwd = message_to_dict(msg)["forwarded"]

    assert fwd["date"] == FWD_DATE
    assert fwd["from_chat"] == "Полезный Парфун"
    assert fwd["from_username"] == "ParfunA"
    assert fwd["from_chat_id"] == -1001626974925
    assert fwd["channel_post"] == 6279
    assert fwd["post_link"] == "https://t.me/ParfunA/6279"


def test_private_channel_forward_uses_the_members_only_link_form():
    msg = _Msg(
        fwd_from=_FwdHeader(date=FWD_DATE, channel_post=42),
        forward=_Forward(chat=_Chat(title="Private notes"), chat_id=-1001626974925),
    )

    fwd = message_to_dict(msg)["forwarded"]

    assert "from_username" not in fwd
    assert fwd["post_link"] == "https://t.me/c/1626974925/42"


def test_user_forward_reports_the_sender_name():
    msg = _Msg(
        fwd_from=_FwdHeader(date=FWD_DATE),
        forward=_Forward(sender=_Chat(first_name="Ada", last_name="Lovelace"), chat_id=None),
    )

    fwd = message_to_dict(msg)["forwarded"]

    assert fwd["from_user"] == "Ada Lovelace"
    assert "post_link" not in fwd


def test_hidden_profile_forward_still_falls_back_to_from_name():
    """Telegram sets from_name only when the original author hides their profile."""
    msg = _Msg(
        fwd_from=_FwdHeader(date=FWD_DATE, from_name="Someone"),
        forward=_Forward(),
    )

    fwd = message_to_dict(msg)["forwarded"]

    assert fwd["from_name"] == "Someone"
    assert "from_chat" not in fwd


def test_message_without_forward_header_is_unaffected():
    assert "forwarded" not in message_to_dict(_Msg())


def test_link_domain_is_overridable(monkeypatch):
    """t.me was unreachable for a day in July 2026; the domain must not be hardcoded."""
    monkeypatch.setattr(messages, "LINK_DOMAIN", "telegram.me")
    msg = _Msg(
        fwd_from=_FwdHeader(date=FWD_DATE, channel_post=6279),
        forward=_Forward(chat=_Chat(title="Полезный Парфун", username="ParfunA")),
    )

    assert message_to_dict(msg)["forwarded"]["post_link"] == "https://telegram.me/ParfunA/6279"
