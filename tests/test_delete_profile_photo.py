from types import SimpleNamespace

import pytest
from telethon import functions

from telegram_mcp import runtime
from telegram_mcp.tools import profile

# Real avatar ids are this large; as JSON numbers they lose precision past 2^53.
CURRENT = 5949397220094317026
OLDER = 5929457005493948524


class _Client:
    def __init__(self, photo_ids):
        self.photos = [SimpleNamespace(id=photo_id) for photo_id in photo_ids]
        self.requests = []
        self.deleted = []

    async def __call__(self, request):
        self.requests.append(request)
        if isinstance(request, functions.photos.GetUserPhotosRequest):
            page = self.photos[request.offset : request.offset + request.limit]
            return SimpleNamespace(photos=page)
        if isinstance(request, functions.photos.DeletePhotosRequest):
            self.deleted.extend(photo.id for photo in request.id)
            return [photo.id for photo in request.id]
        raise AssertionError(f"unexpected request {request!r}")


@pytest.fixture
def client(monkeypatch):
    def _install(photo_ids):
        fake = _Client(photo_ids)
        # Single-account mode, whatever accounts the developer's .env configures.
        monkeypatch.setattr(runtime, "clients", {"default": fake})
        monkeypatch.setattr(profile, "get_client", lambda account=None: fake)

        async def _connected(cl):
            return None

        monkeypatch.setattr(profile, "ensure_connected", _connected)
        return fake

    return _install


@pytest.mark.asyncio
async def test_without_photo_id_the_current_photo_is_deleted(client):
    fake = client([CURRENT, OLDER])

    result = await profile.delete_profile_photo()

    assert result == "Profile photo deleted."
    assert fake.deleted == [CURRENT]


@pytest.mark.asyncio
async def test_photo_id_deletes_only_that_photo(client):
    fake = client([CURRENT, OLDER])

    result = await profile.delete_profile_photo(photo_id=str(OLDER))

    assert result == f"Profile photo {OLDER} deleted."
    assert fake.deleted == [OLDER]


@pytest.mark.asyncio
async def test_photo_id_accepts_an_int(client):
    fake = client([CURRENT, OLDER])

    await profile.delete_profile_photo(photo_id=OLDER)

    assert fake.deleted == [OLDER]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [f"+{OLDER}", f'"{OLDER}"', f" {OLDER} "])
async def test_photo_id_tolerates_how_clients_send_large_ids(client, raw):
    fake = client([CURRENT, OLDER])

    await profile.delete_profile_photo(photo_id=raw)

    assert fake.deleted == [OLDER]


@pytest.mark.asyncio
async def test_photo_id_is_found_past_the_first_page(client, monkeypatch):
    monkeypatch.setattr(profile, "_PHOTO_PAGE_SIZE", 2)
    fake = client([CURRENT, 3, 2, OLDER, 1])

    await profile.delete_profile_photo(photo_id=str(OLDER))

    assert fake.deleted == [OLDER]
    offsets = [
        r.offset for r in fake.requests if isinstance(r, functions.photos.GetUserPhotosRequest)
    ]
    assert offsets == [0, 2]


@pytest.mark.asyncio
async def test_unknown_photo_id_deletes_nothing(client):
    fake = client([CURRENT])

    result = await profile.delete_profile_photo(photo_id="123")

    assert result == "No profile photo with id 123."
    assert fake.deleted == []


@pytest.mark.asyncio
async def test_non_numeric_photo_id_is_rejected_before_calling_telegram(client):
    fake = client([CURRENT])

    result = await profile.delete_profile_photo(photo_id="current")

    assert "Invalid photo_id" in result
    assert fake.requests == []
