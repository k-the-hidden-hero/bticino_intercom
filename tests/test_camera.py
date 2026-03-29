"""Tests for the BTicino camera platform."""

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import EXT_UNIT_MODULE_ID


async def test_camera_entities_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test snapshot and vignette camera entities are created."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    assert len(states) == 2

    snapshot_entities = [s for s in states if "snapshot" in s]
    vignette_entities = [s for s in states if "vignette" in s]
    assert len(snapshot_entities) == 1
    assert len(vignette_entities) == 1


async def test_camera_has_image_url(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test camera entity has image_url attribute from event data."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    snapshot_entity = next(s for s in states if "snapshot" in s)

    state = hass.states.get(snapshot_entity)
    assert state is not None
    assert state.attributes.get("image_url") == "https://example.com/snapshot.jpg"


async def test_camera_vignette_has_url(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test vignette camera entity has correct URL."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    vignette_entity = next(s for s in states if "vignette" in s)

    state = hass.states.get(vignette_entity)
    assert state is not None
    assert state.attributes.get("image_url") == "https://example.com/vignette.jpg"


async def test_camera_url_changes_clears_cache(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that cache is cleared when image URL changes."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    snapshot_entity = next(s for s in states if "snapshot" in s)

    # Update coordinator with new event having a different snapshot URL
    new_data = dict(coordinator.data)
    new_data["last_event"] = {
        "type": "incoming_call",
        "module_id": EXT_UNIT_MODULE_ID,
        "time": 1700002000,
        "subevents": [
            {
                "type": "missed_call",
                "time": 1700002050,
                "snapshot": {
                    "url": "https://example.com/snapshot_new.jpg",
                    "expires_at": 1700200000,
                },
                "vignette": {
                    "url": "https://example.com/vignette_new.jpg",
                    "expires_at": 1700200000,
                },
            }
        ],
    }
    coordinator.async_set_updated_data(new_data)
    await hass.async_block_till_done()

    state = hass.states.get(snapshot_entity)
    assert state.attributes.get("image_url") == "https://example.com/snapshot_new.jpg"


async def test_camera_unavailable_without_url(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test camera is unavailable when there is no image URL."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    # Update coordinator with event without image data
    new_data = dict(coordinator.data)
    new_data["events_history"] = {}
    new_data["last_event"] = {}
    coordinator.async_set_updated_data(new_data)
    await hass.async_block_till_done()

    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    snapshot_entity = next(s for s in states if "snapshot" in s)
    state = hass.states.get(snapshot_entity)
    assert state.state == "unavailable"


async def test_camera_falls_back_to_history_when_last_event_has_no_image(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test camera finds image in event history when last_event has no snapshot."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    snapshot_entity = next(s for s in states if "snapshot" in s)

    # Set last_event to a disconnection (no snapshot) but keep history with images
    new_data = dict(coordinator.data)
    new_data["last_event"] = {
        "type": "disconnection",
        "module_id": "00:03:50:d9:a6:3b",
        "time": 1700300000,
    }
    new_data["events_history"] = {
        coordinator.home_id: [
            {
                "id": "evt_disconnect",
                "type": "disconnection",
                "time": 1700300000,
            },
            {
                "id": "evt_call",
                "type": "call",
                "module_id": EXT_UNIT_MODULE_ID,
                "time": 1700200000,
                "subevents": [
                    {
                        "type": "missed_call",
                        "time": 1700200050,
                        "snapshot": {
                            "url": "https://example.com/fallback_snapshot.jpg",
                            "expires_at": 1700400000,
                        },
                    }
                ],
            },
        ],
    }
    coordinator.async_set_updated_data(new_data)
    await hass.async_block_till_done()

    state = hass.states.get(snapshot_entity)
    # Camera should fall back to the call event with snapshot from history
    assert state.state != "unavailable"
    assert state.attributes.get("image_url") == "https://example.com/fallback_snapshot.jpg"
