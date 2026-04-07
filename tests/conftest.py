import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def mock_telegram_client():
    """
    Automatically injects an AsyncMock in place of the TelegramClient
    across all tests. This completely isolates the test suite from
    the real Telegram API and local session securely.
    """
    with patch("telegram_mcp.client.client", new_callable=AsyncMock) as mock_client:
        # Provide common dummy returns for basic client interactions.
        mock_client.is_connected = lambda: True

        # When domain modules do `await client.get_entity("123")`
        async def dummy_get_entity(entity):
            class DummyEntity:
                id = 12345
                title = "DummyEntity"
                username = "dummy_user"
                first_name = "Dummy"
                last_name = "User"
                phone = "123456789"
                bot = False

            # If the code tries to access `.id`, it'll work
            return DummyEntity()

        mock_client.get_entity = AsyncMock(side_effect=dummy_get_entity)

        # We need mock_client to also support standard await responses
        # Most methods like `send_message` or `__call__` will default to returning another AsyncMock
        yield mock_client
