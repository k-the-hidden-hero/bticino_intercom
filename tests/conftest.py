"""Shared fixtures for BTicino Intercom tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN
from custom_components.bticino_intercom.coordinator import BticinoIntercomCoordinator

# --- Constants matching real captured data ---

HOME_ID = "67bdb3ed5b5e8e648a0b80f6"
BRIDGE_MAC = "00:03:50:d9:a6:3b"
EXTERNAL_UNIT_ID = "d9a63b-a06f-2ef633a2f733"
EXTERNAL_UNIT_NAME = "Citofono Strada"
DOORLOCK_ID = "d9a63b-a06f-2ef633a2f785"
STAIRCASE_LIGHT_ID = "d9a63b-0560-2ef633a2f7d3"
EXTERNAL_UNIT_2_ID = "d9a63b-a16f-2ef633a2f85f"
EXTERNAL_UNIT_2_NAME = "Citofono Ingresso"
SESSION_ID = "5ba94f7d-b300-4d8e-9508-13c3424b8034"

# Aliases used by other test modules
EXT_UNIT_MODULE_ID = EXTERNAL_UNIT_ID
LOCK_MODULE_ID = DOORLOCK_ID
LIGHT_MODULE_ID = STAIRCASE_LIGHT_ID


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="BTicino Test",
        data={
            "home_id": HOME_ID,
            "username": "test@example.com",
            "password": "testpass",
        },
        unique_id=HOME_ID,
    )


@pytest.fixture
def mock_modules_data() -> dict[str, Any]:
    """Return module data matching the real topology."""
    return {
        BRIDGE_MAC: {
            "id": BRIDGE_MAC,
            "type": "BNC1",
            "name": "BTicino Intercom",
            "firmware_name": "1.2.3",
            "reachable": True,
            "variant": "BNC1:bnc1_bridge",
            "uptime": 3600,
            "wifi_strength": 65,
            "websocket_connected": True,
            "local_ipv4": "192.168.1.100",
        },
        EXTERNAL_UNIT_ID: {
            "id": EXTERNAL_UNIT_ID,
            "type": "BNEU",
            "name": EXTERNAL_UNIT_NAME,
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": "BNEU:bneu_external_unit",
        },
        EXTERNAL_UNIT_2_ID: {
            "id": EXTERNAL_UNIT_2_ID,
            "type": "BNEU",
            "name": EXTERNAL_UNIT_2_NAME,
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": "BNEU:bneu_external_unit",
        },
        DOORLOCK_ID: {
            "id": DOORLOCK_ID,
            "type": "BNDL",
            "name": "Porta Esterna",
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": "BNDL:bndl_doorlock",
        },
        STAIRCASE_LIGHT_ID: {
            "id": STAIRCASE_LIGHT_ID,
            "type": "BNSL",
            "name": "Luci Scale",
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": "BNSL:bnsl_staircase_light",
        },
    }


@pytest.fixture
def mock_events_history() -> list[dict[str, Any]]:
    """Return event history data for sensors."""
    return [
        {
            "id": "evt_1",
            "type": "outdoor",
            "time": 1700000100,
            "module_id": EXTERNAL_UNIT_ID,
            "subevents": [
                {
                    "type": "missed_call",
                    "time": 1700000200,
                    "message": "Missed call from Citofono Strada",
                    "session_id": SESSION_ID,
                    "snapshot": {
                        "url": "https://example.com/snapshot.jpg",
                        "expires_at": 1700100000,
                    },
                    "vignette": {
                        "url": "https://example.com/vignette.jpg",
                        "expires_at": 1700100000,
                    },
                }
            ],
        }
    ]


@pytest.fixture
def coordinator(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_modules_data: dict[str, Any],
) -> BticinoIntercomCoordinator:
    """Create a coordinator with pre-populated module data."""
    mock_config_entry.add_to_hass(hass)

    mock_account = AsyncMock()
    mock_ws_client = AsyncMock()

    coord = BticinoIntercomCoordinator(
        hass=hass,
        entry=mock_config_entry,
        account=mock_account,
        websocket_client=mock_ws_client,
    )

    # Pre-populate data as if a successful poll had already occurred
    coord.data = {
        "homes": {HOME_ID: {"name": "Casella"}},
        "modules": mock_modules_data,
        "last_event": {},
        "events_history": {HOME_ID: []},
    }
    coord._main_device_id = BRIDGE_MAC
    coord._home_name = "Casella"

    return coord


# --- WebSocket event fixtures (from real captured data) ---


@pytest.fixture
def ws_rtc_offer() -> dict[str, Any]:
    """RTC offer event (Format A) — incoming call with SDP."""
    return {
        "push_type": "BNC1-rtc",
        "extra_params": {
            "tag_id": "dgo4dB6RqEk=",
            "correlation_id": "1499514006757899539",
            "session_id": SESSION_ID,
            "data": {
                "type": "offer",
                "session_description": {
                    "type": "call",
                    "sdp": "v=0\r\no=- 123 0 IN IP4 0.0.0.0\r\ns=-\r\n",
                    "module_id": EXTERNAL_UNIT_ID,
                    "modules": [
                        {"type": "BNEU", "id": EXTERNAL_UNIT_ID, "name": EXTERNAL_UNIT_NAME},
                        {"type": "BNDL", "id": DOORLOCK_ID, "name": "Porta Esterna"},
                    ],
                },
            },
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
        },
    }


@pytest.fixture
def ws_rtc_terminate() -> dict[str, Any]:
    """RTC terminate event (Format A) — no session_description."""
    return {
        "push_type": "BNC1-rtc",
        "extra_params": {
            "tag_id": "EWilQ7ZfZr8=",
            "correlation_id": "9875885496113046747",
            "session_id": SESSION_ID,
            "data": {
                "type": "terminate",
            },
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
        },
    }


@pytest.fixture
def ws_rtc_rescind() -> dict[str, Any]:
    """RTC rescind event (Format A) — call answered elsewhere, no session_description."""
    return {
        "push_type": "BNC1-rtc",
        "extra_params": {
            "tag_id": "TEZ/E0cg8pk=",
            "correlation_id": "6607934281568223233",
            "session_id": SESSION_ID,
            "data": {
                "type": "rescind",
            },
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
        },
    }


@pytest.fixture
def ws_incoming_call() -> dict[str, Any]:
    """Status incoming_call event (Format B) — with snapshot URLs."""
    return {
        "push_type": "BNC1-incoming_call",
        "extra_params": {
            "event_type": "incoming_call",
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
            "home_name": "Casella",
            "timestamp": 1774877242,
            "camera_id": BRIDGE_MAC,
            "event_id": "69ca7a3aeeefc6e8a1cf8cef",
            "subevent_id": "69ca7a3a59c54a2c490395f4",
            "snapshot_url": "https://example.com/snapshot.jpg",
            "vignette_url": "https://example.com/vignette.jpg",
            "session_id": SESSION_ID,
        },
    }


@pytest.fixture
def ws_missed_call() -> dict[str, Any]:
    """Status missed_call event (Format B)."""
    return {
        "push_type": "BNC1-missed_call",
        "extra_params": {
            "event_type": "missed_call",
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
            "home_name": "Casella",
            "timestamp": 1774877268,
            "camera_id": BRIDGE_MAC,
            "event_id": "69ca7a3aeeefc6e8a1cf8cef",
            "subevent_id": "69ca7a541d2e1e08640ff8ba",
            "session_id": SESSION_ID,
        },
    }


@pytest.fixture
def ws_accepted_call() -> dict[str, Any]:
    """Status accepted_call event (Format B)."""
    return {
        "push_type": "BNC1-accepted_call",
        "extra_params": {
            "event_type": "accepted_call",
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
            "home_name": "Casella",
            "timestamp": 1774877289,
            "camera_id": BRIDGE_MAC,
            "event_id": "69ca7a5fa249cd0e436e9bc4",
            "subevent_id": "69ca7a69b130821bfe0d2516",
            "session_id": "f26a5d46-9670-45f7-98b9-c2362c0729d7",
        },
    }


# --- Persistent mock fixtures for production objects ---


@pytest.fixture
def mock_auth_handler() -> AsyncMock:
    """Create a persistent mock AuthHandler."""
    mock_auth = AsyncMock()
    mock_auth.get_access_token = AsyncMock(return_value="fake_token")
    mock_auth.set_tokens = MagicMock()
    mock_auth.close_session = AsyncMock()
    return mock_auth


@pytest.fixture
def mock_account(mock_modules_data, mock_events_history) -> AsyncMock:
    """Create a persistent mock AsyncAccount."""
    mock_acct = AsyncMock()
    mock_acct.homes = {
        HOME_ID: MagicMock(
            id=HOME_ID,
            name="Test Home",
            raw_data={"name": "Test Home", "id": HOME_ID},
            modules=[MagicMock(id=mid, raw_data=mdata) for mid, mdata in mock_modules_data.items()],
        )
    }
    mock_acct.async_update_topology = AsyncMock()
    mock_acct.async_get_home_status = AsyncMock(
        return_value={
            "body": {"home": {"modules": list(mock_modules_data.values())}},
        }
    )
    mock_acct.async_get_events = AsyncMock(
        return_value={
            "body": {"home": {"events": mock_events_history}},
        }
    )
    mock_acct.async_get_turn_servers = AsyncMock(return_value=[])
    mock_acct.async_set_module_state = AsyncMock()
    return mock_acct


@pytest.fixture
def mock_websocket_client() -> AsyncMock:
    """Create a persistent mock WebsocketClient."""
    mock_ws = AsyncMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.get_listener_task = MagicMock(return_value=None)
    return mock_ws


@pytest.fixture
def mock_signaling_client() -> AsyncMock:
    """Create a persistent mock SignalingClient."""
    mock_sig = AsyncMock()
    mock_sig.is_connected = False
    # set_session_from_push is a sync method in pybticino — use MagicMock
    # to avoid "coroutine never awaited" warnings from AsyncMock
    mock_sig.set_session_from_push = MagicMock()
    return mock_sig


async def _setup_integration(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
) -> MockConfigEntry:
    """Shared helper to set up the integration with mocked pybticino."""
    config_entry.add_to_hass(hass)

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth_handler),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=mock_account),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_websocket_client),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling_client),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
        patch("custom_components.bticino_intercom.history.Store") as mock_history_store_cls,
    ):
        for cls in (mock_store_cls, mock_history_store_cls):
            cls.return_value.async_load = AsyncMock(return_value=None)
            cls.return_value.async_save = AsyncMock()
            cls.return_value.async_remove = AsyncMock()
        await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    return config_entry


@pytest.fixture
async def mock_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> MockConfigEntry:
    """Set up the integration with mocked pybticino."""
    return await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )


@pytest.fixture
def mock_config_entry_light_as_lock() -> MockConfigEntry:
    """Create a mock config entry with light_as_lock enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="BTicino Test",
        data={
            "home_id": HOME_ID,
            "username": "test@example.com",
            "password": "testpass",
        },
        options={"light_as_lock": True},
        unique_id=f"{HOME_ID}_lal",
    )


@pytest.fixture
async def mock_setup_entry_light_as_lock(
    hass: HomeAssistant,
    mock_config_entry_light_as_lock: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> MockConfigEntry:
    """Set up the integration with light_as_lock=True."""
    return await _setup_integration(
        hass,
        mock_config_entry_light_as_lock,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
