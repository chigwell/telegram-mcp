"""Phase 3 tests — Telegram entity → ChannelDescriptor mapping."""

from types import SimpleNamespace

import pytest
from telethon.tl.types import (
    Channel,
    ChannelForbidden,
    Chat,
    ChatBannedRights,
    ChatForbidden,
    User,
)

from telegram_mcp.mcpl.channels import (
    channel_id_for,
    entity_to_descriptor,
)


def make_user(user_id: int, **overrides):
    base = dict(
        id=user_id,
        is_self=False,
        contact=False,
        mutual_contact=False,
        deleted=False,
        bot=False,
        bot_chat_history=False,
        bot_nochats=False,
        verified=False,
        restricted=False,
        min=False,
        bot_inline_geo=False,
        support=False,
        scam=False,
        apply_min_photo=False,
        fake=False,
        bot_attach_menu=False,
        premium=False,
        attach_menu_enabled=False,
        access_hash=None,
        first_name="Alice",
        last_name=None,
        username=None,
        phone=None,
        photo=None,
        status=None,
        bot_info_version=None,
        restriction_reason=None,
        bot_inline_placeholder=None,
        lang_code=None,
    )
    base.update(overrides)
    return User(**base)


def make_chat(chat_id: int, **overrides):
    base = dict(
        id=chat_id,
        title=f"Chat {chat_id}",
        photo=None,
        participants_count=2,
        date=None,
        version=1,
        creator=False,
        left=False,
        deactivated=False,
        call_active=False,
        call_not_empty=False,
        noforwards=False,
        migrated_to=None,
        admin_rights=None,
        default_banned_rights=None,
    )
    base.update(overrides)
    return Chat(**base)


def make_channel(chan_id: int, *, broadcast: bool, megagroup: bool, **overrides):
    base = dict(
        id=chan_id,
        title=f"Channel {chan_id}",
        photo=None,
        date=None,
        creator=False,
        left=False,
        broadcast=broadcast,
        verified=False,
        megagroup=megagroup,
        restricted=False,
        signatures=False,
        min=False,
        scam=False,
        has_link=False,
        has_geo=False,
        slowmode_enabled=False,
        call_active=False,
        call_not_empty=False,
        fake=False,
        gigagroup=False,
        noforwards=False,
        join_to_send=False,
        join_request=False,
        forum=False,
        access_hash=None,
        username=None,
        restriction_reason=None,
        admin_rights=None,
        banned_rights=None,
        default_banned_rights=None,
        participants_count=None,
        usernames=None,
        stories_hidden=False,
        stories_hidden_min=False,
        stories_unavailable=False,
        stories_max_id=None,
        color=None,
        profile_color=None,
        emoji_status=None,
        level=None,
        subscription_until_date=None,
    )
    base.update(overrides)
    return Channel(**base)


# ---------------------------------------------------------------------------
# channel_id_for
# ---------------------------------------------------------------------------


def test_channel_id_for_dm():
    assert channel_id_for("default", "dm", 12345) == "telegram:default:dm:12345"


def test_channel_id_for_supergroup():
    assert (
        channel_id_for("workacct", "supergroup", 998)
        == "telegram:workacct:supergroup:998"
    )


def test_channel_id_for_saved_ignores_peer_id():
    assert channel_id_for("default", "saved", 12345) == "telegram:default:saved"


# ---------------------------------------------------------------------------
# entity_to_descriptor
# ---------------------------------------------------------------------------


def test_user_becomes_dm_channel_bidirectional():
    user = make_user(42)
    desc = entity_to_descriptor(user, account_label="default", self_id=99)
    assert desc is not None
    assert desc["id"] == "telegram:default:dm:42"
    assert desc["type"] == "telegram"
    assert desc["direction"] == "bidirectional"
    assert desc["metadata"]["kind"] == "dm"
    assert desc["metadata"]["account"] == "default"
    assert desc["address"]["peerId"] == 42


def test_self_user_becomes_saved_messages():
    me = make_user(99, first_name="Me")
    desc = entity_to_descriptor(me, account_label="default", self_id=99)
    assert desc is not None
    assert desc["id"] == "telegram:default:saved"
    assert desc["metadata"]["kind"] == "saved"
    assert desc["direction"] == "bidirectional"


def test_chat_becomes_group_channel():
    chat = make_chat(1001)
    desc = entity_to_descriptor(chat, account_label="default")
    assert desc is not None
    assert desc["id"] == "telegram:default:group:1001"
    assert desc["metadata"]["kind"] == "group"
    assert desc["direction"] == "bidirectional"


def test_deactivated_chat_returns_none():
    chat = make_chat(1001, deactivated=True)
    assert entity_to_descriptor(chat, account_label="default") is None


def test_supergroup_becomes_supergroup_channel():
    chan = make_channel(2001, broadcast=False, megagroup=True, title="The Cool Group")
    desc = entity_to_descriptor(chan, account_label="default")
    assert desc is not None
    assert desc["id"] == "telegram:default:supergroup:2001"
    assert desc["metadata"]["kind"] == "supergroup"
    assert desc["direction"] == "bidirectional"
    assert desc["label"] == "The Cool Group"


def test_supergroup_with_forum_marked_in_metadata():
    chan = make_channel(2001, broadcast=False, megagroup=True, forum=True)
    desc = entity_to_descriptor(chan, account_label="default")
    assert desc is not None
    assert desc["metadata"].get("forum") is True


def test_broadcast_channel_subscriber_is_inbound_only():
    chan = make_channel(3001, broadcast=True, megagroup=False)
    desc = entity_to_descriptor(chan, account_label="default")
    assert desc is not None
    assert desc["id"] == "telegram:default:channel:3001"
    assert desc["direction"] == "inbound"


def test_broadcast_channel_creator_is_bidirectional():
    chan = make_channel(3001, broadcast=True, megagroup=False, creator=True)
    desc = entity_to_descriptor(chan, account_label="default")
    assert desc is not None
    assert desc["direction"] == "bidirectional"


def test_broadcast_channel_admin_is_bidirectional():
    # Anything truthy in admin_rights flips us to bidirectional
    chan = make_channel(
        3001,
        broadcast=True,
        megagroup=False,
        admin_rights=SimpleNamespace(post_messages=True),
    )
    desc = entity_to_descriptor(chan, account_label="default")
    assert desc is not None
    assert desc["direction"] == "bidirectional"


def test_username_propagates_to_metadata():
    user = make_user(42, username="alice", first_name="Alice")
    desc = entity_to_descriptor(user, account_label="default")
    assert desc is not None
    assert desc["metadata"].get("username") == "alice"


def test_label_falls_back_to_username_then_id():
    user = make_user(42, first_name="", username="alice")
    desc = entity_to_descriptor(user, account_label="default")
    assert desc is not None
    assert desc["label"] == "@alice"

    nameless = make_user(43, first_name="", username=None)
    desc2 = entity_to_descriptor(nameless, account_label="default")
    assert desc2 is not None
    assert desc2["label"] == "user:43"


def test_account_label_threads_through_to_id_and_metadata():
    user = make_user(42)
    desc = entity_to_descriptor(user, account_label="workacct")
    assert desc is not None
    assert desc["id"] == "telegram:workacct:dm:42"
    assert desc["metadata"]["account"] == "workacct"
    assert desc["address"]["account"] == "workacct"
