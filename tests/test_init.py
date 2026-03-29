"""Tests for BTicino integration setup and unload."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pybticino.exceptions import ApiError, AuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import HOME_ID


async def test_setup_entry_success(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test successful setup of a config entry."""
    assert mock_setup_entry.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data
    assert mock_setup_entry.entry_id in hass.data[DOMAIN]
    assert "coordinator" in hass.data[DOMAIN][mock_setup_entry.entry_id]


async def test_setup_entry_auth_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_websocket_client,
    mock_account,
) -> None:
    """Test setup fails with ConfigEntryAuthFailed on auth error."""
    with patch(
        "custom_components.bticino_intercom.AuthHandler",
        autospec=True,
    ) as mock_auth_class:
        mock_auth = mock_auth_class.return_value
        mock_auth.get_access_token = AsyncMock(side_effect=AuthError("Bad credentials"))

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # ConfigEntryAuthFailed triggers reauth. Since async_step_reauth is not
    # implemented, HA sets the entry state to SETUP_ERROR.
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_api_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_websocket_client,
    mock_account,
) -> None:
    """Test setup raises ConfigEntryNotReady on API error."""
    with patch(
        "custom_components.bticino_intercom.AuthHandler",
        autospec=True,
    ) as mock_auth_class:
        mock_auth = mock_auth_class.return_value
        mock_auth.get_access_token = AsyncMock(side_effect=ApiError(503, "Service unavailable"))

        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test unloading a config entry."""
    assert mock_setup_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_setup_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_setup_entry.state is ConfigEntryState.NOT_LOADED
    # Domain data should be cleaned up
    assert mock_setup_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_coordinator_has_data_after_setup(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test coordinator data is populated after setup."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    assert coordinator.data is not None
    assert "modules" in coordinator.data
    assert "homes" in coordinator.data
    assert coordinator.home_id == HOME_ID
    assert coordinator.main_device_id is not None
