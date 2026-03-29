"""Fixtures for BTicino Intercom tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

# --- Test data constants ---

BRIDGE_MAC = "AA:BB:CC:DD:EE:FF"
HOME_ID = "home_test_123"
HOME_NAME = "Casa Test"
LOCK_MODULE_ID = "module_lock_1"
LIGHT_MODULE_ID = "module_light_1"
EXT_UNIT_MODULE_ID = "module_ext_unit_1"


def _build_modules_data() -> dict:
    """Build a realistic modules dictionary for coordinator data."""
    return {
        BRIDGE_MAC: {
            "id": BRIDGE_MAC,
            "type": "BNCX",
            "firmware_name": "2.1.0",
            "firmware_revision": 42,
            "name": "Bridge",
            "uptime": 86400,
            "wifi_strength": 65,
            "websocket_connected": True,
            "local_ipv4": "192.168.1.100",
            "last_configs_update": 1700000000,
            "last_seen": 1700000000,
            "reachable": True,
        },
        LOCK_MODULE_ID: {
            "id": LOCK_MODULE_ID,
            "type": "BNDL",
            "variant": "xxx:bndl_doorlock",
            "name": "Front Door Lock",
            "bridge": BRIDGE_MAC,
            "lock": True,
            "reachable": True,
            "configured": True,
            "firmware_revision": "1.0",
            "uptime": 3600,
            "last_user_interaction": 1700000000,
            "appliance_type": "doorlock",
        },
        EXT_UNIT_MODULE_ID: {
            "id": EXT_UNIT_MODULE_ID,
            "type": "BNEU",
            "variant": "xxx:bneu_external_unit",
            "name": "Front Door",
            "bridge": BRIDGE_MAC,
            "reachable": True,
            "configured": True,
            "firmware_revision": "1.0",
            "uptime": 3600,
        },
        LIGHT_MODULE_ID: {
            "id": LIGHT_MODULE_ID,
            "type": "BNSL",
            "variant": "xxx:bnsl_staircase_light",
            "name": "Staircase Light",
            "bridge": BRIDGE_MAC,
            "status": "off",
            "reachable": True,
            "configured": True,
            "firmware_revision": "1.0",
            "uptime": 3600,
        },
    }


def _build_coordinator_data() -> dict:
    """Build complete coordinator data."""
    return {
        "homes": {
            HOME_ID: {
                "id": HOME_ID,
                "name": HOME_NAME,
            },
        },
        "modules": _build_modules_data(),
        "events_history": {
            HOME_ID: [
                {
                    "id": "evt_1",
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "time": 1700000000,
                    "end": 1700000060,
                    "subevents": [
                        {
                            "type": "missed_call",
                            "time": 1700000050,
                            "message": "Missed call",
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
                },
            ],
        },
        "last_event": {},
    }


# --- Fixtures ---


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a mock config entry."""
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
def mock_config_entry_light_as_lock() -> MockConfigEntry:
    """Return a mock config entry with light_as_lock enabled."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=HOME_NAME,
        data={
            "username": "user@example.com",
            "password": "secret123",
            "home_id": HOME_ID,
        },
        options={"light_as_lock": True},
        unique_id="user@example.com",
        version=1,
    )


@pytest.fixture
def mock_auth_handler() -> Generator[AsyncMock]:
    """Mock the pybticino AuthHandler."""
    with patch(
        "custom_components.bticino_intercom.AuthHandler",
        autospec=True,
    ) as mock_auth_class:
        mock_auth = mock_auth_class.return_value
        mock_auth.get_access_token = AsyncMock(return_value="mock-access-token")
        mock_auth.close_session = AsyncMock()
        yield mock_auth


@pytest.fixture
def mock_account() -> Generator[AsyncMock]:
    """Mock the pybticino AsyncAccount."""
    with patch(
        "custom_components.bticino_intercom.AsyncAccount",
        autospec=True,
    ) as mock_account_class:
        mock_acct = mock_account_class.return_value

        # Build mock home with modules
        mock_bridge = MagicMock()
        mock_bridge.id = BRIDGE_MAC
        mock_bridge.raw_data = _build_modules_data()[BRIDGE_MAC]

        mock_lock = MagicMock()
        mock_lock.id = LOCK_MODULE_ID
        mock_lock.raw_data = _build_modules_data()[LOCK_MODULE_ID]

        mock_ext_unit = MagicMock()
        mock_ext_unit.id = EXT_UNIT_MODULE_ID
        mock_ext_unit.raw_data = _build_modules_data()[EXT_UNIT_MODULE_ID]

        mock_light = MagicMock()
        mock_light.id = LIGHT_MODULE_ID
        mock_light.raw_data = _build_modules_data()[LIGHT_MODULE_ID]

        mock_home = MagicMock()
        mock_home.id = HOME_ID
        mock_home.name = HOME_NAME
        mock_home.raw_data = {"id": HOME_ID, "name": HOME_NAME}
        mock_home.modules = [mock_bridge, mock_lock, mock_ext_unit, mock_light]

        mock_acct.homes = {HOME_ID: mock_home}
        mock_acct.async_update_topology = AsyncMock()
        mock_acct.async_get_home_status = AsyncMock(
            return_value={
                "body": {
                    "home": {
                        "modules": [
                            {"id": LOCK_MODULE_ID, "lock": True},
                            {"id": LIGHT_MODULE_ID, "status": "off"},
                        ]
                    }
                }
            }
        )
        mock_acct.async_get_events = AsyncMock(
            return_value={
                "body": {
                    "home": {
                        "events": [
                            {
                                "id": "evt_1",
                                "type": "call",
                                "module_id": EXT_UNIT_MODULE_ID,
                                "time": 1700000000,
                                "end": 1700000060,
                                "subevents": [
                                    {
                                        "type": "missed_call",
                                        "time": 1700000050,
                                        "message": "Missed call",
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
                            },
                        ]
                    }
                }
            }
        )
        mock_acct.async_set_module_state = AsyncMock()

        yield mock_acct


@pytest.fixture
def mock_websocket_client() -> Generator[MagicMock]:
    """Mock the pybticino WebsocketClient."""
    with patch(
        "custom_components.bticino_intercom.WebsocketClient",
        autospec=True,
    ) as mock_ws_class:
        mock_ws = mock_ws_class.return_value
        mock_ws.connect = AsyncMock()
        mock_ws.disconnect = AsyncMock()
        mock_ws.get_listener_task = MagicMock(return_value=None)
        yield mock_ws


@pytest.fixture
async def mock_setup_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with mocked dependencies."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry


@pytest.fixture
async def mock_setup_entry_light_as_lock(
    hass: HomeAssistant,
    mock_config_entry_light_as_lock: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: MagicMock,
) -> MockConfigEntry:
    """Set up the integration with light_as_lock enabled."""
    mock_config_entry_light_as_lock.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry_light_as_lock.entry_id)
    await hass.async_block_till_done()
    return mock_config_entry_light_as_lock
