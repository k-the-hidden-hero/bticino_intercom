"""Tests for BTicino integration setup and unload."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pybticino.exceptions import ApiError, AuthError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import EXTERNAL_UNIT_ID, HOME_ID, _setup_integration

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


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
) -> None:
    """Test setup fails with ConfigEntryAuthFailed on auth error."""
    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(side_effect=AuthError("Bad credentials"))
    mock_auth.set_tokens = MagicMock()

    mock_ws = AsyncMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.get_listener_task = MagicMock(return_value=None)

    mock_signaling = AsyncMock()
    mock_signaling.is_connected = False

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=AsyncMock()),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_ws),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # ConfigEntryAuthFailed triggers reauth. Since async_step_reauth is
    # implemented, HA sets the entry state to SETUP_ERROR.
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_api_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setup raises ConfigEntryNotReady on API error."""
    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(side_effect=ApiError(503, "Service unavailable"))
    mock_auth.set_tokens = MagicMock()

    mock_ws = AsyncMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.get_listener_task = MagicMock(return_value=None)

    mock_signaling = AsyncMock()
    mock_signaling.is_connected = False

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=AsyncMock()),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_ws),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
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


async def test_reject_call_service(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """Test reject_call service terminates the active call."""
    entry = await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    # Simulate an active call
    coordinator._active_call = {
        "session_id": "test-session",
        "module_id": EXTERNAL_UNIT_ID,
    }

    events = async_capture_events(hass, "bticino_intercom_call")

    await hass.services.async_call(DOMAIN, "reject_call", blocking=True)
    await hass.async_block_till_done()

    assert coordinator._active_call is None
    signaling_client.send_terminate.assert_called_once()
    end_events = [e for e in events if e.data.get("type") == "end"]
    assert len(end_events) == 1
    assert end_events[0].data["reason"] == "rejected"


async def test_reject_call_service_no_active_call(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """Test reject_call service is a no-op when no call is active."""
    entry = await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    events = async_capture_events(hass, "bticino_intercom_call")

    await hass.services.async_call(DOMAIN, "reject_call", blocking=True)
    await hass.async_block_till_done()

    signaling_client.send_terminate.assert_not_called()
    assert len(events) == 0
