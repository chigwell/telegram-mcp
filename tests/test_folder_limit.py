"""create_folder must not pre-emptively cap folder creation at a hardcoded count.

Telegram's actual folder limit differs by account (10 for regular accounts, 20 for
Premium) and can change server-side. create_folder must let the real API call be
the source of truth and only surface a friendly message when Telegram itself
reports that the account is at its limit (DIALOG_FILTERS_TOO_MUCH), instead of
refusing client-side once a hardcoded count is reached.
"""

from types import SimpleNamespace

import pytest
from telethon.errors.rpcerrorlist import BadRequestError

from telegram_mcp.tools import folders


class _FakeClient:
    """Fake Telegram client returning `filter_count` existing folders.

    Raises BadRequestError("DIALOG_FILTERS_TOO_MUCH") on the actual
    UpdateDialogFilterRequest call when `reject_update` is True, mimicking a
    real Telegram account that has reached its (server-defined) folder limit.
    """

    def __init__(self, filter_count: int, reject_update: bool = False):
        self.filter_count = filter_count
        self.reject_update = reject_update

    async def __call__(self, request):
        request_name = type(request).__name__
        if request_name == "GetDialogFiltersRequest":
            filters = [
                folders.DialogFilter(
                    id=2 + i,
                    title=folders.TextWithEntities(text=f"Folder {i}", entities=[]),
                    pinned_peers=[],
                    include_peers=[],
                    exclude_peers=[],
                )
                for i in range(self.filter_count)
            ]
            return SimpleNamespace(filters=filters)
        if request_name == "UpdateDialogFilterRequest":
            if self.reject_update:
                raise BadRequestError(request=request, message="DIALOG_FILTERS_TOO_MUCH")
            return SimpleNamespace()
        raise AssertionError(f"Unexpected request: {request_name}")


@pytest.mark.asyncio
async def test_create_folder_does_not_pre_emptively_block_at_ten(monkeypatch):
    """Regression test: previously create_folder refused client-side once 10
    folders existed, even though Premium accounts can have up to 20. With the
    hardcoded pre-check removed, an account with 10 existing folders whose
    UpdateDialogFilterRequest call actually succeeds (e.g. a Premium account)
    must be able to create an 11th folder.
    """
    client = _FakeClient(filter_count=10, reject_update=False)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)

    result = await folders.create_folder(title="Eleventh Folder")

    assert "Cannot create folder" not in result
    assert '"success": true' in result.lower()


@pytest.mark.asyncio
async def test_create_folder_surfaces_real_telegram_limit_error(monkeypatch):
    """When Telegram itself rejects the folder creation because the account is
    genuinely at its limit, create_folder must return a clear, actionable
    message rather than a generic error.
    """
    client = _FakeClient(filter_count=10, reject_update=True)
    monkeypatch.setattr(folders, "get_client", lambda account=None: client)

    result = await folders.create_folder(title="One Folder Too Many")

    assert "Cannot create folder" in result
    assert "folder limit" in result
    assert "10 for regular accounts, 20 for Premium" in result
