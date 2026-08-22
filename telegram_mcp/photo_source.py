"""Resolve peer photos from every source Telegram offers them through."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from telethon.tl import functions, types
from telethon.tl.types import Channel, Chat, User

AVATAR_SOURCE = "avatars"
MESSAGE_SOURCE = "messages"
PHOTO_SOURCES = (AVATAR_SOURCE, MESSAGE_SOURCE)
THUMBNAIL_TARGET_PIXELS = 320


class UnknownPhotoSource(ValueError):
    """Raised when a caller asks for a source that does not exist."""


@dataclass(frozen=True)
class PhotoReference:
    """One retrievable photo plus the identifier used to open it again."""

    identifier: int
    photo: Any
    is_current: bool
    taken_at: Optional[Any] = None
    caption: str = ""

    def describe(self) -> dict:
        described = {
            "id": self.identifier,
            "date": self.taken_at,
            "is_current": self.is_current,
        }
        if self.caption:
            described["caption"] = self.caption
        return described


def validate_source(source: str) -> str:
    normalised_source = (source or AVATAR_SOURCE).strip().lower()
    if normalised_source not in PHOTO_SOURCES:
        raise UnknownPhotoSource(
            f"Unknown photo source '{source}'. Expected one of: {', '.join(PHOTO_SOURCES)}."
        )
    return normalised_source


def _current_photo_id(entity: Any) -> Optional[int]:
    entity_photo = getattr(entity, "photo", None)
    return getattr(entity_photo, "photo_id", None)


def _peer_supports_native_avatar_history(entity: Any) -> bool:
    return isinstance(entity, User)


async def _list_user_avatar_references(client, entity, limit: int) -> List[PhotoReference]:
    retrieved = await client(
        functions.photos.GetUserPhotosRequest(user_id=entity, offset=0, max_id=0, limit=limit)
    )
    current_photo_id = _current_photo_id(entity)
    return [
        PhotoReference(
            identifier=photo.id,
            photo=photo,
            is_current=photo.id == current_photo_id,
            taken_at=getattr(photo, "date", None),
        )
        for photo in retrieved.photos
    ]


async def _list_chat_avatar_references(client, entity, limit: int) -> List[PhotoReference]:
    service_messages = await client.get_messages(
        entity, limit=limit, filter=types.InputMessagesFilterChatPhotos()
    )
    current_photo_id = _current_photo_id(entity)

    references: List[PhotoReference] = []
    for message in service_messages:
        changed_photo = getattr(getattr(message, "action", None), "photo", None)
        if changed_photo is None:
            continue
        references.append(
            PhotoReference(
                identifier=changed_photo.id,
                photo=changed_photo,
                is_current=changed_photo.id == current_photo_id,
                taken_at=getattr(message, "date", None),
            )
        )
    return references


async def _list_message_photo_references(client, entity, limit: int) -> List[PhotoReference]:
    photo_messages = await client.get_messages(
        entity, limit=limit, filter=types.InputMessagesFilterPhotos()
    )
    return [
        PhotoReference(
            identifier=message.id,
            photo=message.photo,
            is_current=False,
            taken_at=getattr(message, "date", None),
            caption=getattr(message, "message", "") or "",
        )
        for message in photo_messages
        if getattr(message, "photo", None) is not None
    ]


async def list_photo_references(client, entity, source: str, limit: int) -> List[PhotoReference]:
    """List the newest ``limit`` photos of ``entity`` from the requested source."""
    resolved_source = validate_source(source)

    if resolved_source == MESSAGE_SOURCE:
        return await _list_message_photo_references(client, entity, limit)
    if _peer_supports_native_avatar_history(entity):
        return await _list_user_avatar_references(client, entity, limit)
    return await _list_chat_avatar_references(client, entity, limit)


async def find_photo_reference(
    client, entity, source: str, identifier: Optional[int], search_depth: int
) -> Optional[PhotoReference]:
    """Find one photo by identifier, or the current avatar when none is given."""
    references = await list_photo_references(client, entity, source, search_depth)
    if not references:
        return None
    if identifier is None:
        current_avatar = next((each for each in references if each.is_current), None)
        return current_avatar or references[0]
    return next((each for each in references if each.identifier == identifier), None)


def _thumbnail_size_index(photo: Any) -> Optional[int]:
    available_sizes = getattr(photo, "sizes", None) or []
    widths = [(index, getattr(size, "w", 0) or 0) for index, size in enumerate(available_sizes)]
    within_target = [index for index, width in widths if 0 < width <= THUMBNAIL_TARGET_PIXELS]
    if within_target:
        return within_target[-1]
    return None


async def download_photo_bytes(
    client, reference: PhotoReference, thumbnail: bool = False
) -> bytes:
    """Download one referenced photo straight to memory, never to disk."""
    if thumbnail:
        size_index = _thumbnail_size_index(reference.photo)
        if size_index is not None:
            downloaded = await client.download_media(reference.photo, file=bytes, thumb=size_index)
            if downloaded:
                return downloaded
    return await client.download_media(reference.photo, file=bytes)


def peer_supports_source(entity: Any, source: str) -> bool:
    """Whether the peer can serve the source at all, used to explain empty results."""
    if validate_source(source) == MESSAGE_SOURCE:
        return isinstance(entity, (Chat, Channel, User))
    return True
