"""Phase 4 tests — Telegram message → McplContentBlock conversion."""

from types import SimpleNamespace

from telegram_mcp.mcpl.content import (
    media_kind,
    message_to_content_blocks,
    message_uri,
)


def test_message_uri_format():
    assert (
        message_uri("default", 1234, 567)
        == "telegram://message/default/1234/567"
    )
    # Negative chat IDs (channels in Telegram) survive verbatim
    assert (
        message_uri("workacct", -100123, 999)
        == "telegram://message/workacct/-100123/999"
    )


def test_media_kind_no_media():
    msg = SimpleNamespace(media=None)
    assert media_kind(msg) is None


def test_media_kind_photo():
    class MessageMediaPhoto:
        pass
    msg = SimpleNamespace(media=MessageMediaPhoto())
    assert media_kind(msg) == "photo"


def test_media_kind_voice_note():
    class DocumentAttributeAudio:
        def __init__(self, voice):
            self.voice = voice

    class Document:
        attributes = [DocumentAttributeAudio(voice=True)]

    class MessageMediaDocument:
        document = Document()

    msg = SimpleNamespace(media=MessageMediaDocument())
    assert media_kind(msg) == "voice"


def test_media_kind_sticker():
    class DocumentAttributeSticker:
        pass

    class Document:
        attributes = [DocumentAttributeSticker()]

    class MessageMediaDocument:
        document = Document()

    msg = SimpleNamespace(media=MessageMediaDocument())
    assert media_kind(msg) == "sticker"


def test_message_to_content_blocks_text_only():
    msg = SimpleNamespace(message="hello world", media=None, id=42)
    blocks = message_to_content_blocks(msg, account_label="default", chat_id=100)
    assert blocks == [{"type": "text", "text": "hello world"}]


def test_message_to_content_blocks_media_only_yields_placeholder_and_resource():
    class _MMP:
        pass
    _MMP.__name__ = "MessageMediaPhoto"
    msg = SimpleNamespace(message="", media=_MMP(), id=42)
    blocks = message_to_content_blocks(msg, account_label="default", chat_id=100)
    assert blocks == [
        {"type": "text", "text": "[photo]"},
        {"type": "resource", "uri": "telegram://message/default/100/42"},
    ]


def test_message_to_content_blocks_text_plus_media():
    class _MMP:
        pass
    _MMP.__name__ = "MessageMediaPhoto"
    msg = SimpleNamespace(message="check this out", media=_MMP(), id=42)
    blocks = message_to_content_blocks(msg, account_label="default", chat_id=100)
    assert blocks == [
        {"type": "text", "text": "check this out"},
        {"type": "resource", "uri": "telegram://message/default/100/42"},
    ]


def test_message_to_content_blocks_empty_falls_back():
    msg = SimpleNamespace(message="", media=None, id=42)
    blocks = message_to_content_blocks(msg, account_label="default", chat_id=100)
    assert blocks == [{"type": "text", "text": "[message]"}]
