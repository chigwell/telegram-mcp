"""Legacy forum wire adapters, kept byte-for-byte compatible during refactoring.

Replacing these with SDK requests is a separate protocol migration.
"""

import struct

from telethon.tl.tlobject import TLObject, TLRequest


class GetForumTopicsRequest(TLRequest):
    """Raw request for channels.getForumTopics missing in Telethon 1.42-1.43."""

    CONSTRUCTOR_ID = 0x0DE560D1
    SUBCLASS_OF_ID = 0x0

    def __init__(self, channel, offset_date, offset_id, offset_topic, limit, q=None):
        self.channel = channel
        self.q = q
        self.offset_date = offset_date
        self.offset_id = offset_id
        self.offset_topic = offset_topic
        self.limit = limit

    async def resolve(self, client, utils):
        self.channel = utils.get_input_channel(await client.get_input_entity(self.channel))

    def to_dict(self):
        return {
            "_": "GetForumTopicsRequest",
            "channel": (
                self.channel.to_dict() if isinstance(self.channel, TLObject) else self.channel
            ),
            "q": self.q,
            "offset_date": self.offset_date,
            "offset_id": self.offset_id,
            "offset_topic": self.offset_topic,
            "limit": self.limit,
        }

    def _bytes(self):
        flags = 0 if self.q is None or self.q is False else 1
        return b"".join(
            (
                struct.pack("<I", self.CONSTRUCTOR_ID),
                struct.pack("<I", flags),
                self.channel._bytes(),
                b"" if self.q is None or self.q is False else self.serialize_bytes(self.q),
                struct.pack("<i", self.offset_date),
                struct.pack("<i", self.offset_id),
                struct.pack("<i", self.offset_topic),
                struct.pack("<i", self.limit),
            )
        )

    @classmethod
    def from_reader(cls, reader):
        flags = reader.read_int()
        channel = reader.tgread_object()
        q = reader.tgread_string() if flags & 1 else None
        offset_date = reader.read_int()
        offset_id = reader.read_int()
        offset_topic = reader.read_int()
        limit = reader.read_int()
        return cls(
            channel=channel,
            offset_date=offset_date,
            offset_id=offset_id,
            offset_topic=offset_topic,
            limit=limit,
            q=q,
        )


class CreateForumTopicRequest(TLRequest):
    """Raw request for messages.createForumTopic missing in Telethon 1.42."""

    CONSTRUCTOR_ID = 0x2F98C3D5
    SUBCLASS_OF_ID = 0x0

    def __init__(
        self,
        peer,
        title,
        random_id,
        icon_color=None,
        icon_emoji_id=None,
        send_as=None,
    ):
        self.peer = peer
        self.title = title
        self.icon_color = icon_color
        self.icon_emoji_id = icon_emoji_id
        self.random_id = random_id
        self.send_as = send_as

    async def resolve(self, client, utils):
        self.peer = utils.get_input_peer(await client.get_input_entity(self.peer))
        if self.send_as is not None:
            self.send_as = utils.get_input_peer(await client.get_input_entity(self.send_as))

    def to_dict(self):
        return {
            "_": "CreateForumTopicRequest",
            "peer": self.peer.to_dict() if isinstance(self.peer, TLObject) else self.peer,
            "title": self.title,
            "icon_color": self.icon_color,
            "icon_emoji_id": self.icon_emoji_id,
            "random_id": self.random_id,
            "send_as": (
                self.send_as.to_dict() if isinstance(self.send_as, TLObject) else self.send_as
            ),
        }

    def _bytes(self):
        flags = 0
        if self.icon_color is not None:
            flags |= 1 << 0
        if self.send_as is not None:
            flags |= 1 << 2
        if self.icon_emoji_id is not None:
            flags |= 1 << 3

        return b"".join(
            (
                struct.pack("<I", self.CONSTRUCTOR_ID),
                struct.pack("<I", flags),
                self.peer._bytes(),
                self.serialize_bytes(self.title),
                b"" if self.icon_color is None else struct.pack("<i", self.icon_color),
                b"" if self.icon_emoji_id is None else struct.pack("<q", self.icon_emoji_id),
                struct.pack("<q", self.random_id),
                b"" if self.send_as is None else self.send_as._bytes(),
            )
        )

    @classmethod
    def from_reader(cls, reader):
        flags = reader.read_int()
        peer = reader.tgread_object()
        title = reader.tgread_string()
        icon_color = reader.read_int() if flags & (1 << 0) else None
        icon_emoji_id = reader.read_long() if flags & (1 << 3) else None
        random_id = reader.read_long()
        send_as = reader.tgread_object() if flags & (1 << 2) else None
        return cls(
            peer=peer,
            title=title,
            random_id=random_id,
            icon_color=icon_color,
            icon_emoji_id=icon_emoji_id,
            send_as=send_as,
        )
