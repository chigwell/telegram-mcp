"""message_to_dict reports which formatting a message carries.

A reader that must tell plain text from quoted, struck-through or code text
cannot see that in the text itself.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from telethon.tl import types

from telegram_mcp.tools import messages

DATE = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _msg(text, entities=None):
    msg = types.Message(
        id=1, peer_id=types.PeerUser(42), date=DATE, message=text, entities=entities
    )
    msg._sender = SimpleNamespace(first_name="Ann", last_name=None, username=None)
    return msg


def test_plain_text_has_no_formatting_field():
    assert "formatting" not in messages.message_to_dict(_msg("send hello"))
    assert "formatting" not in messages.message_to_dict(_msg("send hello", []))


@pytest.mark.parametrize(
    "entity,kind",
    [
        (types.MessageEntityBlockquote(0, 4), "blockquote"),
        (types.MessageEntityBlockquote(0, 4, collapsed=True), "blockquote"),
        (types.MessageEntityCode(0, 4), "code"),
        (types.MessageEntityPre(0, 4, "python"), "pre"),
        (types.MessageEntityStrike(0, 4), "strike"),
        (types.MessageEntitySpoiler(0, 4), "spoiler"),
        (types.MessageEntityBold(0, 4), "bold"),
        (types.MessageEntityTextUrl(0, 4, "https://example.com"), "text_url"),
        (types.MessageEntityMentionName(0, 4, 7), "mention_name"),
        (types.MessageEntityBankCard(0, 4), "bank_card"),
        (types.MessageEntityUnknown(0, 4), "unknown"),
    ],
)
def test_each_kind_is_reported(entity, kind):
    assert messages.message_to_dict(_msg("send hello", [entity]))["formatting"] == [kind]


def test_kinds_are_sorted_and_unique():
    record = messages.message_to_dict(
        _msg(
            "send hello",
            [
                types.MessageEntityStrike(0, 4),
                types.MessageEntityBlockquote(0, 10),
                types.MessageEntityStrike(5, 5),
            ],
        )
    )
    assert record["formatting"] == ["blockquote", "strike"]
