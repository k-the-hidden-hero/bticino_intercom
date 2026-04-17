"""Tests for the BTicino coordinator."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from pybticino.exceptions import ApiError, AuthError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bticino_intercom.const import (
    DATA_LAST_EVENT,
    DOMAIN,
    EVENT_LOGBOOK_INCOMING_CALL,
    EVENT_TYPE_ANSWERED_ELSEWHERE,
    EVENT_TYPE_INCOMING_CALL,
    EVENT_TYPE_TERMINATED,
    SIGNAL_CALL_RECEIVED,
)

from .conftest import BRIDGE_MAC, EXT_UNIT_MODULE_ID, HOME_ID


async def test_coordinator_initial_data(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test coordinator has correct initial data structure."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    assert coordinator.data is not None
    assert BRIDGE_MAC in coordinator.data["modules"]
    assert coordinator.main_device_id == BRIDGE_MAC
    assert coordinator.home_id == HOME_ID
    assert coordinator.home_name is not None


async def test_coordinator_home_name(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test coordinator correctly sets home name properties."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    assert coordinator.home_name == "Casa Test"
    assert coordinator.normalized_home_name == "casa_test"


async def test_coordinator_refresh_auth_error(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test coordinator handles auth errors during refresh."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    mock_account.async_update_topology.side_effect = AuthError("Token expired")

    await coordinator.async_refresh()

    assert coordinator.last_update_success is False


async def test_coordinator_refresh_api_error_returns_stale_data(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test coordinator returns last known data on transient API errors."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    mock_account.async_update_topology.side_effect = ApiError(500, "Server error")

    await coordinator.async_refresh()

    # Resilient coordinator: returns stale data, reports success
    assert coordinator.last_update_success is True
    assert coordinator.data is not None
    assert coordinator.data.get("modules") is not None


async def test_coordinator_polling_interval(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
    freezer,
) -> None:
    """Test coordinator polls at the configured 5-minute interval."""
    hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    mock_account.async_update_topology.reset_mock()

    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert mock_account.async_update_topology.call_count >= 1


# --- WebSocket event processing tests ---


async def test_websocket_call_event(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test processing a WebSocket call event dispatches signal and updates state."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    call_signals = []

    def _handle_signal(is_calling, module_id):
        call_signals.append((is_calling, module_id))

    async_dispatcher_connect(hass, SIGNAL_CALL_RECEIVED, _handle_signal)

    message = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "session_id": "sess_123",
                    "time": 1700000000,
                }
            },
        }
    }

    updated = coordinator._process_websocket_event(message)
    await hass.async_block_till_done()

    assert updated is True
    assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_INCOMING_CALL
    assert coordinator.data[DATA_LAST_EVENT]["module_id"] == EXT_UNIT_MODULE_ID
    assert len(call_signals) == 1
    assert call_signals[0] == (True, EXT_UNIT_MODULE_ID)


async def test_websocket_terminate_event(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test processing a WebSocket terminate event."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    call_signals = []

    def _handle_signal(is_calling, module_id):
        call_signals.append((is_calling, module_id))

    async_dispatcher_connect(hass, SIGNAL_CALL_RECEIVED, _handle_signal)

    message = {
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

    updated = coordinator._process_websocket_event(message)
    await hass.async_block_till_done()

    assert updated is True
    assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_TERMINATED
    # terminate dispatches False
    assert len(call_signals) == 1
    assert call_signals[0] == (False, EXT_UNIT_MODULE_ID)


async def test_websocket_rescind_event(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test processing a WebSocket rescind (answered elsewhere) event."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "rescind",
                    "module_id": EXT_UNIT_MODULE_ID,
                }
            },
        }
    }

    updated = coordinator._process_websocket_event(message)

    assert updated is True
    assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_ANSWERED_ELSEWHERE


async def test_websocket_unknown_module_ignored(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that events for unknown modules are ignored."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {
        "extra_params": {
            "device_id": "unknown_module_xyz",
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": "unknown_module_xyz",
                }
            },
        }
    }

    updated = coordinator._process_websocket_event(message)

    assert updated is False


async def test_websocket_no_module_id_ignored(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that events without module ID are ignored."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {"extra_params": {"data": {"session_description": {"type": "call"}}}}

    updated = coordinator._process_websocket_event(message)

    assert updated is False


async def test_websocket_generic_state_update(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test generic state update for non-RTC events."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "reachable": False,
            },
        }
    }

    updated = coordinator._process_websocket_event(message)

    assert updated is True
    assert coordinator.data["modules"][EXT_UNIT_MODULE_ID]["reachable"] is False


async def test_handle_websocket_message_triggers_refresh(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test _handle_websocket_message calls async_set_updated_data and refresh."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    with (
        patch.object(coordinator, "async_set_updated_data") as mock_set_data,
        patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock) as mock_refresh,
    ):
        message = {
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
        await coordinator._handle_websocket_message(message)

        mock_set_data.assert_called_once()
        mock_refresh.assert_awaited_once()


async def test_handle_websocket_message_no_update_skips_refresh(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test _handle_websocket_message skips refresh when no data changed."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    with (
        patch.object(coordinator, "async_set_updated_data") as mock_set_data,
        patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock) as mock_refresh,
    ):
        # Event for unknown module - should not trigger update
        message = {
            "extra_params": {
                "device_id": "unknown_module",
                "data": {
                    "session_description": {
                        "type": "call",
                        "module_id": "unknown_module",
                    }
                },
            }
        }
        await coordinator._handle_websocket_message(message)

        mock_set_data.assert_not_called()
        mock_refresh.assert_not_awaited()


async def test_coordinator_fires_logbook_events(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that websocket events fire logbook events."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    fired_events = []

    def _capture_event(event):
        fired_events.append(event)

    hass.bus.async_listen(EVENT_LOGBOOK_INCOMING_CALL, _capture_event)

    message = {
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

    coordinator._process_websocket_event(message)
    await hass.async_block_till_done()

    assert len(fired_events) == 1
    assert fired_events[0].data["module_id"] == EXT_UNIT_MODULE_ID


# --- incoming_call push (snapshot/vignette) tests ---


async def test_incoming_call_push_updates_last_event_with_snapshot(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test incoming_call push creates last_event with snapshot/vignette URLs."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {
        "push_type": "BNC1-incoming_call",
        "category": "incoming_call",
        "extra_params": {
            "event_type": "incoming_call",
            "device_id": BRIDGE_MAC,
            "home_id": HOME_ID,
            "session_id": "sess_456",
            "snapshot_url": "https://example.com/realtime_snapshot.jpg",
            "vignette_url": "https://example.com/realtime_vignette.jpg",
        },
    }

    updated = coordinator._process_websocket_event(message)

    assert updated is True
    last = coordinator.data[DATA_LAST_EVENT]
    assert last["type"] == EVENT_TYPE_INCOMING_CALL
    assert last["module_id"] == BRIDGE_MAC
    assert len(last["subevents"]) == 1
    assert last["subevents"][0]["snapshot"]["url"] == "https://example.com/realtime_snapshot.jpg"
    assert last["subevents"][0]["vignette"]["url"] == "https://example.com/realtime_vignette.jpg"


async def test_incoming_call_push_enriches_existing_rtc_event(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test incoming_call push enriches an existing RTC call event with image URLs."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    # First: simulate RTC call event (no snapshot)
    rtc_message = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "session_id": "sess_789",
                }
            },
        }
    }
    coordinator._process_websocket_event(rtc_message)

    assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_INCOMING_CALL
    assert not coordinator.data[DATA_LAST_EVENT].get("subevents")

    # Then: incoming_call push arrives with snapshot
    incoming_message = {
        "push_type": "BNC1-incoming_call",
        "category": "incoming_call",
        "extra_params": {
            "device_id": BRIDGE_MAC,
            "snapshot_url": "https://example.com/snap.jpg",
            "vignette_url": "https://example.com/vig.jpg",
        },
    }
    updated = coordinator._process_websocket_event(incoming_message)

    assert updated is True
    last = coordinator.data[DATA_LAST_EVENT]
    # Should still be the original call event, enriched
    assert last["type"] == EVENT_TYPE_INCOMING_CALL
    assert last["module_id"] == EXT_UNIT_MODULE_ID
    assert last["subevents"][0]["snapshot"]["url"] == "https://example.com/snap.jpg"
    assert last["subevents"][0]["vignette"]["url"] == "https://example.com/vig.jpg"


async def test_incoming_call_push_without_urls_ignored(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test incoming_call push without snapshot/vignette URLs is ignored."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {
        "push_type": "BNC1-incoming_call",
        "category": "incoming_call",
        "extra_params": {
            "event_type": "incoming_call",
            "device_id": BRIDGE_MAC,
        },
    }

    updated = coordinator._process_websocket_event(message)

    assert updated is False


async def test_rtc_call_preserves_subevents_from_prior_incoming_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test RTC call event preserves snapshot subevents from a prior incoming_call push."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    # First: incoming_call push arrives with snapshot
    incoming_message = {
        "push_type": "BNC1-incoming_call",
        "category": "incoming_call",
        "extra_params": {
            "device_id": BRIDGE_MAC,
            "snapshot_url": "https://example.com/early_snap.jpg",
            "vignette_url": "https://example.com/early_vig.jpg",
        },
    }
    coordinator._process_websocket_event(incoming_message)

    # Then: RTC call event arrives (without subevents)
    rtc_message = {
        "extra_params": {
            "device_id": EXT_UNIT_MODULE_ID,
            "data": {
                "session_description": {
                    "type": "call",
                    "module_id": EXT_UNIT_MODULE_ID,
                    "session_id": "sess_abc",
                }
            },
        }
    }
    coordinator._process_websocket_event(rtc_message)
    await hass.async_block_till_done()

    last = coordinator.data[DATA_LAST_EVENT]
    assert last["type"] == EVENT_TYPE_INCOMING_CALL
    assert last["module_id"] == EXT_UNIT_MODULE_ID
    # Snapshot subevents from incoming_call should be preserved
    assert last["subevents"][0]["snapshot"]["url"] == "https://example.com/early_snap.jpg"
    assert last["subevents"][0]["vignette"]["url"] == "https://example.com/early_vig.jpg"


async def test_incoming_call_push_only_snapshot(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test incoming_call push with only snapshot_url (no vignette)."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    message = {
        "push_type": "BNC1-incoming_call",
        "category": "incoming_call",
        "extra_params": {
            "device_id": BRIDGE_MAC,
            "snapshot_url": "https://example.com/only_snap.jpg",
        },
    }

    updated = coordinator._process_websocket_event(message)

    assert updated is True
    last = coordinator.data[DATA_LAST_EVENT]
    assert last["subevents"][0]["snapshot"]["url"] == "https://example.com/only_snap.jpg"
    assert "vignette" not in last["subevents"][0]


async def test_incoming_call_push_triggers_refresh(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test _handle_websocket_message triggers update for incoming_call push."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    with (
        patch.object(coordinator, "async_set_updated_data") as mock_set_data,
        patch.object(coordinator, "async_request_refresh", new_callable=AsyncMock) as mock_refresh,
    ):
        message = {
            "push_type": "BNC1-incoming_call",
            "category": "incoming_call",
            "extra_params": {
                "device_id": BRIDGE_MAC,
                "snapshot_url": "https://example.com/snap.jpg",
            },
        }
        await coordinator._handle_websocket_message(message)

        mock_set_data.assert_called_once()
        mock_refresh.assert_awaited_once()
