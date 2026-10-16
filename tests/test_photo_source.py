import datetime

import pytest
from telethon.tl import types

from telegram_mcp import photo_source


class _Photo:
    def __init__(self, photo_id, sizes=None, date=None):
        self.id = photo_id
        self.sizes = sizes or []
        self.date = date


class _Size:
    def __init__(self, width):
        self.w = width


class _EntityPhoto:
    def __init__(self, photo_id):
        self.photo_id = photo_id


class _User(types.User):
    def __init__(self, photo_id=None):
        super().__init__(id=7)
        self.photo = _EntityPhoto(photo_id) if photo_id else None


class _Channel(types.Channel):
    def __init__(self, photo_id=None):
        super().__init__(id=9, title="t", photo=None, date=None)
        self.photo = _EntityPhoto(photo_id) if photo_id else None


class _ServiceMessage:
    def __init__(self, photo, date=None):
        self.action = types.MessageActionChatEditPhoto(photo=photo)
        self.date = date


class _PhotoMessage:
    def __init__(self, message_id, photo, caption="", date=None, media=None):
        self.id = message_id
        self.photo = photo
        self.media = media if media is not None else types.MessageMediaPhoto(photo=photo)
        self.message = caption
        self.date = date


class _Client:
    def __init__(self, user_photos=None, messages=None):
        self._user_photos = user_photos or []
        self._messages = messages or []
        self.requested_filter = None
        self.requested_thumb = "unset"
        self.message_requests = []

    async def __call__(self, request):
        class _Result:
            photos = self._user_photos

        return _Result()

    async def get_messages(self, entity, limit=None, filter=None, ids=None):
        self.message_requests.append((entity, limit, filter, ids))
        self.requested_filter = filter
        if ids is not None:
            return next((message for message in self._messages if message.id == ids), None)
        return self._messages[:limit]

    async def download_media(self, photo, file=None, thumb=None):
        self.requested_thumb = thumb
        return b"bytes-for-%d" % photo.id


@pytest.mark.asyncio
async def test_user_avatars_use_the_native_photo_history_api():
    newest, older = _Photo(200), _Photo(100)
    client = _Client(user_photos=[newest, older])

    references = await photo_source.list_photo_references(
        client, _User(photo_id=200), photo_source.AVATAR_SOURCE, 10
    )

    assert [each.identifier for each in references] == [200, 100]
    assert [each.is_current for each in references] == [True, False]


@pytest.mark.asyncio
async def test_channel_avatars_fall_back_to_the_chat_photo_service_messages():
    client = _Client(messages=[_ServiceMessage(_Photo(55)), _ServiceMessage(_Photo(44))])

    references = await photo_source.list_photo_references(
        client, _Channel(photo_id=55), photo_source.AVATAR_SOURCE, 10
    )

    assert isinstance(client.requested_filter, types.InputMessagesFilterChatPhotos)
    assert [each.identifier for each in references] == [55, 44]
    assert references[0].is_current is True


@pytest.mark.asyncio
async def test_service_messages_without_a_photo_are_skipped():
    client = _Client(messages=[_ServiceMessage(None), _ServiceMessage(_Photo(44))])

    references = await photo_source.list_photo_references(
        client, _Channel(), photo_source.AVATAR_SOURCE, 10
    )

    assert [each.identifier for each in references] == [44]


@pytest.mark.asyncio
async def test_message_photos_are_addressed_by_message_id_and_keep_captions():
    client = _Client(messages=[_PhotoMessage(9001, _Photo(1), caption="on the balcony")])

    references = await photo_source.list_photo_references(
        client, _Channel(), photo_source.MESSAGE_SOURCE, 10
    )

    assert isinstance(client.requested_filter, types.InputMessagesFilterPhotos)
    assert references[0].identifier == 9001
    assert references[0].describe()["caption"] == "on the balcony"


@pytest.mark.asyncio
async def test_message_source_applies_to_users_too_without_hitting_avatar_history():
    client = _Client(messages=[_PhotoMessage(5, _Photo(1))])

    references = await photo_source.list_photo_references(
        client, _User(), photo_source.MESSAGE_SOURCE, 10
    )

    assert [each.identifier for each in references] == [5]


@pytest.mark.asyncio
async def test_find_by_identifier_selects_the_matching_photo():
    client = _Client(user_photos=[_Photo(200), _Photo(100)])

    found = await photo_source.find_photo_reference(
        client, _User(photo_id=200), photo_source.AVATAR_SOURCE, 100, 20
    )

    assert found.identifier == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("entity", [_User(), _Channel()])
async def test_find_message_photo_fetches_only_the_requested_id_beyond_search_depth(entity):
    when = datetime.datetime(2026, 8, 22, tzinfo=datetime.timezone.utc)
    target = _PhotoMessage(9001, _Photo(1), caption="target", date=when)
    client = _Client(messages=[_PhotoMessage(i, _Photo(i)) for i in range(200)] + [target])

    found = await photo_source.find_photo_reference(
        client, entity, photo_source.MESSAGE_SOURCE, 9001, 100
    )

    assert found.photo is target.photo
    assert found.describe() == {"id": 9001, "date": when, "is_current": False, "caption": "target"}
    assert client.message_requests == [(entity, None, None, 9001)]


@pytest.mark.asyncio
@pytest.mark.parametrize("messages", [[], [_PhotoMessage(9001, None)]])
async def test_find_missing_or_non_photo_message_does_not_scan_history(messages):
    entity = _Channel()
    client = _Client(messages=messages)

    found = await photo_source.find_photo_reference(
        client, entity, photo_source.MESSAGE_SOURCE, 9001, 100
    )

    assert found is None
    assert client.message_requests == [(entity, None, None, 9001)]


@pytest.mark.asyncio
async def test_web_preview_image_is_not_returned_as_a_message_photo():
    """A link preview is outside the InputMessagesFilterPhotos id space (#304)."""
    webpage = types.WebPage(
        id=1, url="https://youtu.be/x", display_url="youtube.com", hash=0, photo=_Photo(777)
    )
    # Telethon's Message.photo exposes the web-preview image; list_photos,
    # which defines the message-id space, does not list such messages.
    message = _PhotoMessage(
        42,
        webpage.photo,
        media=types.MessageMediaWebPage(webpage=webpage),
    )
    entity = _Channel()
    client = _Client(messages=[message])

    found = await photo_source.find_photo_reference(
        client, entity, photo_source.MESSAGE_SOURCE, 42, 100
    )

    assert found is None
    assert client.message_requests == [(entity, None, None, 42)]


@pytest.mark.asyncio
async def test_find_message_photo_propagates_access_failure_without_fallback():
    class DeniedClient:
        calls = 0

        async def get_messages(self, entity, *, ids):
            self.calls += 1
            raise PermissionError("access denied")

    client = DeniedClient()
    with pytest.raises(PermissionError, match="access denied"):
        await photo_source.find_photo_reference(
            client, _Channel(), photo_source.MESSAGE_SOURCE, 9001, 100
        )
    assert client.calls == 1


@pytest.mark.asyncio
async def test_find_without_identifier_prefers_the_current_avatar():
    client = _Client(user_photos=[_Photo(200), _Photo(300)])

    found = await photo_source.find_photo_reference(
        client, _User(photo_id=300), photo_source.AVATAR_SOURCE, None, 20
    )

    assert found.identifier == 300


@pytest.mark.asyncio
async def test_find_returns_none_when_the_identifier_is_absent():
    client = _Client(user_photos=[_Photo(200)])

    found = await photo_source.find_photo_reference(
        client, _User(), photo_source.AVATAR_SOURCE, 999, 20
    )

    assert found is None


@pytest.mark.asyncio
async def test_empty_history_returns_no_references_rather_than_raising():
    references = await photo_source.list_photo_references(
        _Client(), _Channel(), photo_source.AVATAR_SOURCE, 10
    )

    assert references == []


@pytest.mark.asyncio
async def test_thumbnail_download_picks_the_largest_size_within_target():
    client = _Client()
    photo = _Photo(1, sizes=[_Size(90), _Size(320), _Size(1280)])
    reference = photo_source.PhotoReference(identifier=1, photo=photo, is_current=True)

    await photo_source.download_photo_bytes(client, reference, thumbnail=True)

    assert client.requested_thumb == 1


@pytest.mark.asyncio
async def test_full_download_requests_no_thumbnail():
    client = _Client()
    photo = _Photo(1, sizes=[_Size(90)])
    reference = photo_source.PhotoReference(identifier=1, photo=photo, is_current=True)

    await photo_source.download_photo_bytes(client, reference)

    assert client.requested_thumb is None


@pytest.mark.asyncio
async def test_thumbnail_falls_back_to_full_download_when_no_size_fits():
    client = _Client()
    photo = _Photo(1, sizes=[_Size(1280)])
    reference = photo_source.PhotoReference(identifier=1, photo=photo, is_current=True)

    await photo_source.download_photo_bytes(client, reference, thumbnail=True)

    assert client.requested_thumb is None


def test_unknown_source_is_rejected_with_the_accepted_values():
    with pytest.raises(photo_source.UnknownPhotoSource) as rejected:
        photo_source.validate_source("stories")

    assert "avatars" in str(rejected.value) and "messages" in str(rejected.value)


def test_source_defaults_to_avatars_and_ignores_casing():
    assert photo_source.validate_source(None) == photo_source.AVATAR_SOURCE
    assert photo_source.validate_source("  MESSAGES ") == photo_source.MESSAGE_SOURCE


def test_describe_omits_caption_when_absent_and_carries_date():
    when = datetime.datetime(2026, 8, 22, tzinfo=datetime.timezone.utc)
    described = photo_source.PhotoReference(
        identifier=5, photo=_Photo(5), is_current=False, taken_at=when
    ).describe()

    assert "caption" not in described
    assert described == {"id": 5, "date": when, "is_current": False}
