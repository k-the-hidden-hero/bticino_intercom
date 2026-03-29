"""Integration tests for BTicino Intercom.

These tests verify end-to-end flows across multiple components:
coordinator, entities, device/entity registries, and lifecycle events.
"""

from datetime import timedelta
from unittest.mock import AsyncMock

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.lock import DOMAIN as LOCK_DOMAIN
from homeassistant.components.lock import LockState
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pybticino.exceptions import ApiError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bticino_intercom.const import (
    DOMAIN,
    EVENT_LOGBOOK_INCOMING_CALL,
    EVENT_LOGBOOK_TERMINATED,
)

from .conftest import BRIDGE_MAC, EXT_UNIT_MODULE_ID, LIGHT_MODULE_ID, LOCK_MODULE_ID

# ---------------------------------------------------------------------------
# Device & Entity Registry
# ---------------------------------------------------------------------------


async def test_device_registry_bridge_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that the bridge device is registered in the device registry."""
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device(identifiers={(DOMAIN, BRIDGE_MAC)})

    assert device is not None
    assert device.manufacturer == "BTicino"
    assert device.model == "BNCX"
    assert "Casa Test" in device.name


async def test_entity_registry_all_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that all expected entities are in the entity registry."""
    entity_reg = er.async_get(hass)
    entries = er.async_entries_for_config_entry(entity_reg, mock_setup_entry.entry_id)

    # Extract domains
    domains = {e.domain for e in entries}
    assert LOCK_DOMAIN in domains
    assert BINARY_SENSOR_DOMAIN in domains
    assert SENSOR_DOMAIN in domains
    assert LIGHT_DOMAIN in domains
    assert CAMERA_DOMAIN in domains


async def test_all_entities_share_bridge_device(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that all entities are linked to the same bridge device."""
    device_reg = dr.async_get(hass)
    entity_reg = er.async_get(hass)

    bridge_device = device_reg.async_get_device(identifiers={(DOMAIN, BRIDGE_MAC)})
    assert bridge_device is not None

    entries = er.async_entries_for_config_entry(entity_reg, mock_setup_entry.entry_id)
    for entry in entries:
        assert entry.device_id == bridge_device.id, f"Entity {entry.entity_id} not linked to bridge device"


# ---------------------------------------------------------------------------
# End-to-end: WebSocket call → entities react
# ---------------------------------------------------------------------------


async def test_websocket_call_updates_binary_sensor_and_event_sensor(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that a WebSocket call event updates both binary sensor and event sensor."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    binary_sensor_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]
    event_sensor_id = next(s for s in hass.states.async_entity_ids(SENSOR_DOMAIN) if "last_event_type" in s)

    # Verify initial states
    assert hass.states.get(binary_sensor_id).state == STATE_OFF

    # Simulate incoming call via websocket
    message = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "session_id": "sess_live_001",
                    "time": 1700100000,
                }
            },
        }
    }
    await coordinator._handle_websocket_message(message)
    await hass.async_block_till_done()

    # Binary sensor should be ON (call in progress)
    assert hass.states.get(binary_sensor_id).state == STATE_ON

    # Event sensor should show incoming_call
    assert hass.states.get(event_sensor_id).state == "incoming_call"


async def test_websocket_call_then_terminate_full_cycle(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test complete call lifecycle: call → terminate."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    binary_sensor_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]

    # 1. Incoming call
    call_msg = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "time": 1700100000,
                }
            },
        }
    }
    await coordinator._handle_websocket_message(call_msg)
    await hass.async_block_till_done()
    assert hass.states.get(binary_sensor_id).state == STATE_ON

    # 2. Call terminated
    terminate_msg = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "terminate",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "time": 1700100030,
                }
            },
        }
    }
    await coordinator._handle_websocket_message(terminate_msg)
    await hass.async_block_till_done()

    # Binary sensor should be OFF (call ended)
    assert hass.states.get(binary_sensor_id).state == STATE_OFF

    # Event sensor should show terminated
    event_sensor_id = next(s for s in hass.states.async_entity_ids(SENSOR_DOMAIN) if "last_event_type" in s)
    assert hass.states.get(event_sensor_id).state == "terminated"


async def test_websocket_call_fires_logbook_events(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that WebSocket events fire corresponding logbook events."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    incoming_events = []
    terminated_events = []
    hass.bus.async_listen(EVENT_LOGBOOK_INCOMING_CALL, lambda e: incoming_events.append(e))
    hass.bus.async_listen(EVENT_LOGBOOK_TERMINATED, lambda e: terminated_events.append(e))

    # Call
    call_msg = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                }
            },
        }
    }
    await coordinator._handle_websocket_message(call_msg)
    await hass.async_block_till_done()
    assert len(incoming_events) == 1

    # Terminate
    term_msg = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "terminate",
                    "module_id": EXT_UNIT_MODULE_ID,
                }
            },
        }
    }
    await coordinator._handle_websocket_message(term_msg)
    await hass.async_block_till_done()
    assert len(terminated_events) == 1


# ---------------------------------------------------------------------------
# End-to-end: Coordinator polling updates entities
# ---------------------------------------------------------------------------


async def test_coordinator_refresh_updates_all_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test that a coordinator refresh propagates to all entity platforms."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    lock_id = next(
        s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light" not in s.lower() or "lock" in s.lower()
    )
    light_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]

    # Initial states
    assert hass.states.get(lock_id).state == LockState.LOCKED
    assert hass.states.get(light_id).state == STATE_OFF

    # Simulate updated status from API: lock unlocked, light on
    mock_account.async_get_home_status.return_value = {
        "body": {
            "home": {
                "modules": [
                    {"id": LOCK_MODULE_ID, "lock": False},
                    {"id": LIGHT_MODULE_ID, "status": "on"},
                ]
            }
        }
    }

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(lock_id).state == LockState.UNLOCKED
    assert hass.states.get(light_id).state == STATE_ON


async def test_coordinator_api_failure_makes_entities_unavailable(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test that API failure makes bridge sensors unavailable."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    mock_account.async_update_topology.side_effect = ApiError(500, "Server down")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    # Bridge sensors should become unavailable
    uptime_id = next(s for s in hass.states.async_entity_ids(SENSOR_DOMAIN) if "uptime" in s)
    assert hass.states.get(uptime_id).state == "unavailable"


async def test_coordinator_recovers_after_api_failure(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test that entities recover when API starts working again."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    # Fail
    mock_account.async_update_topology.side_effect = ApiError(500, "Server down")
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    uptime_id = next(s for s in hass.states.async_entity_ids(SENSOR_DOMAIN) if "uptime" in s)
    assert hass.states.get(uptime_id).state == "unavailable"

    # Recover
    mock_account.async_update_topology.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(uptime_id)
    assert state.state != "unavailable"
    assert state.state == "86400"


# ---------------------------------------------------------------------------
# Lifecycle: setup → unload → re-setup
# ---------------------------------------------------------------------------


async def test_unload_and_reload(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client,
) -> None:
    """Test unloading and re-setting up the integration."""
    assert mock_setup_entry.state is ConfigEntryState.LOADED

    # Unload
    assert await hass.config_entries.async_unload(mock_setup_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_setup_entry.state is ConfigEntryState.NOT_LOADED

    # Entities should be unavailable after unload
    lock_states = hass.states.async_entity_ids(LOCK_DOMAIN)
    for lock_id in lock_states:
        assert hass.states.get(lock_id).state == "unavailable"

    # Re-setup
    assert await hass.config_entries.async_setup(mock_setup_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_setup_entry.state is ConfigEntryState.LOADED

    # Entities should be available again
    for lock_id in hass.states.async_entity_ids(LOCK_DOMAIN):
        assert hass.states.get(lock_id).state != "unavailable"


async def test_options_change_triggers_reload(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client,
) -> None:
    """Test that changing options triggers a reload of the integration."""
    assert mock_setup_entry.state is ConfigEntryState.LOADED

    # light_as_lock is False initially → light entity exists, no light_as_lock entity
    light_entities = hass.states.async_entity_ids(LIGHT_DOMAIN)
    assert len(light_entities) == 1

    lock_entities = hass.states.async_entity_ids(LOCK_DOMAIN)
    lock_count_before = len(lock_entities)

    # Change option via options flow
    result = await hass.config_entries.options.async_init(mock_setup_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"light_as_lock": True},
    )
    await hass.async_block_till_done()

    # After reload with light_as_lock=True:
    # - light entity should be unavailable (entity stays in registry but platform doesn't create it)
    # - an additional lock entity (light_as_lock) should exist and be available
    assert mock_setup_entry.state is ConfigEntryState.LOADED
    lock_entities_after = hass.states.async_entity_ids(LOCK_DOMAIN)
    available_locks = [s for s in lock_entities_after if hass.states.get(s).state != "unavailable"]
    assert len(available_locks) == lock_count_before + 1


# ---------------------------------------------------------------------------
# light_as_lock: mutually exclusive entity creation
# ---------------------------------------------------------------------------


async def test_light_as_lock_excludes_light_entity(
    hass: HomeAssistant,
    mock_setup_entry_light_as_lock: MockConfigEntry,
) -> None:
    """Test that light_as_lock creates lock but not light for the staircase module."""
    # No light entities
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) == 0

    # Two lock entities: doorlock + staircase-as-lock
    lock_ids = hass.states.async_entity_ids(LOCK_DOMAIN)
    assert len(lock_ids) == 2


async def test_light_as_lock_off_creates_light_not_lock(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that without light_as_lock, staircase module creates light, not lock."""
    # One light entity
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) == 1

    # One lock entity (only doorlock, no staircase)
    lock_ids = hass.states.async_entity_ids(LOCK_DOMAIN)
    assert len(lock_ids) == 1


# ---------------------------------------------------------------------------
# Concurrent actions: lock unlock + coordinator refresh
# ---------------------------------------------------------------------------


async def test_lock_optimistic_state_survives_coordinator_refresh(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test that lock keeps optimistic state during relock timer despite coordinator refresh."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    lock_id = next(
        s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light" not in s.lower() or "lock" in s.lower()
    )

    # Unlock (sets optimistic unlocked + starts relock timer)
    await hass.services.async_call(
        LOCK_DOMAIN,
        "unlock",
        {ATTR_ENTITY_ID: lock_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(lock_id).state == LockState.UNLOCKED

    # Coordinator refresh comes in with lock=True (API still shows locked)
    # The relock timer is active, so the coordinator update should NOT override
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    # Optimistic state should survive
    assert hass.states.get(lock_id).state == LockState.UNLOCKED


async def test_lock_relock_timer_restores_locked(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
    freezer,
) -> None:
    """Test that the relock timer automatically relocks after delay."""
    lock_id = next(
        s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light" not in s.lower() or "lock" in s.lower()
    )

    await hass.services.async_call(
        LOCK_DOMAIN,
        "unlock",
        {ATTR_ENTITY_ID: lock_id},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get(lock_id).state == LockState.UNLOCKED

    # Advance past relock delay (5 seconds)
    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(lock_id).state == LockState.LOCKED


# ---------------------------------------------------------------------------
# Camera: event update propagates URL change
# ---------------------------------------------------------------------------


async def test_websocket_event_updates_camera_url(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that a new websocket event updates the camera snapshot URL."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    snapshot_id = next(s for s in hass.states.async_entity_ids(CAMERA_DOMAIN) if "snapshot" in s)

    old_url = hass.states.get(snapshot_id).attributes.get("image_url")
    assert old_url == "https://example.com/snapshot.jpg"

    # Simulate a websocket event that will update last_event with new image data
    coordinator.data["last_event"] = {
        "type": "incoming_call",
        "module_id": EXT_UNIT_MODULE_ID,
        "time": 1700200000,
        "subevents": [
            {
                "type": "missed_call",
                "time": 1700200050,
                "snapshot": {
                    "url": "https://example.com/new_snapshot.jpg",
                    "expires_at": 1700300000,
                },
                "vignette": {
                    "url": "https://example.com/new_vignette.jpg",
                    "expires_at": 1700300000,
                },
            }
        ],
    }
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    new_url = hass.states.get(snapshot_id).attributes.get("image_url")
    assert new_url == "https://example.com/new_snapshot.jpg"


# ---------------------------------------------------------------------------
# Polling: verify periodic refresh triggers
# ---------------------------------------------------------------------------


async def test_polling_refresh_updates_bridge_sensors(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
    freezer,
) -> None:
    """Test that the 5-minute polling interval updates bridge sensor values."""
    wifi_id = next(s for s in hass.states.async_entity_ids(SENSOR_DOMAIN) if "wifi_strength" in s)

    # Initial: -65
    assert int(hass.states.get(wifi_id).state) == -65

    # Update mock to return new wifi strength
    mock_account.async_get_home_status.return_value = {
        "body": {
            "home": {
                "modules": [
                    {"id": BRIDGE_MAC, "wifi_strength": 42},
                    {"id": LOCK_MODULE_ID, "lock": True},
                    {"id": LIGHT_MODULE_ID, "status": "off"},
                ]
            }
        }
    }

    # Advance time to trigger coordinator refresh
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert int(hass.states.get(wifi_id).state) == -42
