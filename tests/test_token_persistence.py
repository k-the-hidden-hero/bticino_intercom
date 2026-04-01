"""Tests for token persistence in BTicino integration."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import HOME_ID, HOME_NAME


@pytest.fixture
def mock_config_entry_for_tokens() -> MockConfigEntry:
    """Return a mock config entry for token tests."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOME_NAME,
        data={
            "username": "user@example.com",
            "password": "secret123",
            "home_id": HOME_ID,
        },
        options={"light_as_lock": False},
        unique_id="user@example.com",
        version=1,
    )


@pytest.fixture
def mock_token_store() -> MagicMock:
    """Return a mock Store instance for token persistence."""
    store = MagicMock()
    store.async_load = AsyncMock(return_value=None)
    store.async_save = AsyncMock()
    store.async_remove = AsyncMock()
    return store


async def test_tokens_saved_after_auth(
    hass: HomeAssistant,
    mock_config_entry_for_tokens: MockConfigEntry,
    mock_account: AsyncMock,
    mock_websocket_client: MagicMock,
    mock_token_store: MagicMock,
) -> None:
    """Verify that after setup, when AuthHandler authenticates, the token_callback saves tokens to Store."""
    captured_callback = None
    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(return_value="mock-access-token")
    mock_auth.close_session = AsyncMock()
    mock_auth.set_tokens = MagicMock()

    def auth_factory(*args, **kwargs):
        nonlocal captured_callback
        captured_callback = kwargs.get("token_callback")
        return mock_auth

    with (
        patch("custom_components.bticino_intercom.Store", return_value=mock_token_store),
        patch("custom_components.bticino_intercom.AuthHandler", side_effect=auth_factory),
    ):
        mock_config_entry_for_tokens.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry_for_tokens.entry_id)
        await hass.async_block_till_done()

    # Verify the callback was captured
    assert captured_callback is not None

    # Simulate AuthHandler calling the callback with token data
    token_data = {
        "access_token": "new-access-token",
        "refresh_token": "new-refresh-token",
        "expires_at": 1700100000,
    }
    await captured_callback(token_data)

    # Verify tokens were saved to the store
    mock_token_store.async_save.assert_awaited_once_with(token_data)


async def test_tokens_restored_on_setup(
    hass: HomeAssistant,
    mock_config_entry_for_tokens: MockConfigEntry,
    mock_account: AsyncMock,
    mock_websocket_client: MagicMock,
    mock_token_store: MagicMock,
) -> None:
    """Verify that when Store has saved tokens, set_tokens is called on the AuthHandler before get_access_token."""
    saved_tokens = {
        "access_token": "saved-access-token",
        "refresh_token": "saved-refresh-token",
        "expires_at": 1700100000,
    }
    mock_token_store.async_load = AsyncMock(return_value=saved_tokens)

    mock_auth = MagicMock()
    mock_auth.close_session = AsyncMock()
    mock_auth.set_tokens = MagicMock()

    # Track call order
    call_order = []
    mock_auth.set_tokens.side_effect = lambda **kwargs: call_order.append("set_tokens")

    async def track_get_access_token():
        call_order.append("get_access_token")
        return "mock-access-token"

    mock_auth.get_access_token = AsyncMock(side_effect=track_get_access_token)

    with (
        patch("custom_components.bticino_intercom.Store", return_value=mock_token_store),
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
    ):
        mock_config_entry_for_tokens.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry_for_tokens.entry_id)
        await hass.async_block_till_done()

    # Verify set_tokens was called with the saved token data
    mock_auth.set_tokens.assert_called_once_with(
        access_token="saved-access-token",
        refresh_token="saved-refresh-token",
        expires_at=1700100000,
    )

    # Verify set_tokens was called BEFORE get_access_token
    assert call_order.index("set_tokens") < call_order.index("get_access_token")


async def test_setup_works_without_saved_tokens(
    hass: HomeAssistant,
    mock_config_entry_for_tokens: MockConfigEntry,
    mock_account: AsyncMock,
    mock_websocket_client: MagicMock,
    mock_token_store: MagicMock,
) -> None:
    """Verify that first-time setup (no stored tokens) still works with full auth."""
    # async_load returns None (no saved tokens)
    mock_token_store.async_load = AsyncMock(return_value=None)

    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(return_value="mock-access-token")
    mock_auth.close_session = AsyncMock()
    mock_auth.set_tokens = MagicMock()

    with (
        patch("custom_components.bticino_intercom.Store", return_value=mock_token_store),
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
    ):
        mock_config_entry_for_tokens.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry_for_tokens.entry_id)
        await hass.async_block_till_done()

    # set_tokens should NOT have been called (no saved tokens)
    mock_auth.set_tokens.assert_not_called()

    # get_access_token should still be called for full auth
    mock_auth.get_access_token.assert_awaited()


async def test_tokens_cleaned_on_remove(
    hass: HomeAssistant,
    mock_config_entry_for_tokens: MockConfigEntry,
    mock_account: AsyncMock,
    mock_websocket_client: MagicMock,
    mock_token_store: MagicMock,
) -> None:
    """Verify that async_remove_entry calls store.async_remove()."""
    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(return_value="mock-access-token")
    mock_auth.close_session = AsyncMock()
    mock_auth.set_tokens = MagicMock()

    with (
        patch("custom_components.bticino_intercom.Store", return_value=mock_token_store),
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
    ):
        mock_config_entry_for_tokens.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_config_entry_for_tokens.entry_id)
        await hass.async_block_till_done()

        # Now remove the entry
        await hass.config_entries.async_remove(mock_config_entry_for_tokens.entry_id)
        await hass.async_block_till_done()

    # Verify async_remove was called on the store
    mock_token_store.async_remove.assert_awaited_once()
