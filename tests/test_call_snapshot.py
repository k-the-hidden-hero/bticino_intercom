"""Pending-call snapshots for cards attached after the doorbell rings."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.bticino_intercom.const import DOMAIN, EVENT_CALL
from custom_components.bticino_intercom.websocket_api import async_register_websocket_api, ws_get_active_calls

from .conftest import EXTERNAL_UNIT_ID, SESSION_ID


@pytest.fixture
def camera_entity(hass, coordinator):
    """Register the actual live-camera unique ID."""
    return er.async_get(hass).async_get_or_create(
        "camera",
        DOMAIN,
        f"{coordinator.entry.entry_id}_webrtc_{EXTERNAL_UNIT_ID}",
        config_entry=coordinator.entry,
        suggested_object_id="front",
    )


async def test_snapshot_tracks_offer_enrichment_and_rename(hass, coordinator, camera_entity, ws_rtc_offer):
    """The public snapshot contains no signaling secrets and keeps its original deadline."""
    assert coordinator.ringing_call is None
    events = async_capture_events(hass, EVENT_CALL)
    await coordinator._process_websocket_event(ws_rtc_offer)
    first = coordinator.ringing_call
    assert first == events[0].data
    assert set(first) == {
        "type",
        "entry_id",
        "module_id",
        "module_name",
        "session_id",
        "camera_entity_id",
        "expires_at",
        "snapshot_url",
        "vignette_url",
    }
    assert first["camera_entity_id"] == camera_entity.entity_id
    assert first["expires_at"] == pytest.approx(datetime.now(UTC).timestamp() + 30, abs=1)
    first["module_name"] = "mutated client copy"
    coordinator._fire_call_event(
        "ring", EXTERNAL_UNIT_ID, snapshot_url="/local/image.jpg", vignette_url="/local/small.jpg"
    )
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID)
    assert coordinator.ringing_call["expires_at"] == first["expires_at"]
    assert coordinator.ringing_call["snapshot_url"] == "/local/image.jpg"
    assert coordinator.ringing_call["vignette_url"] == "/local/small.jpg"
    assert coordinator.ringing_call["module_name"] != first["module_name"]
    er.async_get(hass).async_update_entity(camera_entity.entity_id, new_entity_id="camera.renamed")
    assert coordinator.ringing_call["camera_entity_id"] == "camera.renamed"


async def test_snapshot_expiry_does_not_extend_on_enrichment(coordinator, freezer):
    """Duplicates and the short retransmission watchdog never extend ringing."""
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id=SESSION_ID)
    expiry = coordinator.ringing_call["expires_at"]
    freezer.tick(timedelta(seconds=16))
    coordinator._end_call_session(EXTERNAL_UNIT_ID, reason="inactivity")
    assert coordinator.ringing_call["expires_at"] == expiry
    freezer.tick(timedelta(seconds=15))
    assert coordinator.ringing_call is None
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id=SESSION_ID)
    assert coordinator.ringing_call is None
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id="new-call")
    assert coordinator.ringing_call["session_id"] == "new-call"


@pytest.mark.parametrize(
    "closing_fixture", ["ws_rtc_terminate", "ws_rtc_rescind", "ws_missed_call", "ws_accepted_call"]
)
async def test_closed_or_answered_call_not_returned(coordinator, ws_rtc_offer, closing_fixture, request):
    """Every incoming call outcome clears the public pending-call snapshot."""
    await coordinator._process_websocket_event(ws_rtc_offer)
    assert coordinator.ringing_call
    closing = request.getfixturevalue(closing_fixture)
    closing["extra_params"]["session_id"] = SESSION_ID
    await coordinator._process_websocket_event(closing)
    assert coordinator.ringing_call is None


async def test_status_only_ring_and_timeout(coordinator, ws_incoming_call):
    """A status-only push has its own session ID even without a signaling offer."""
    # Resolve the unambiguous external unit in a home without an RTC offer.
    coordinator.data["modules"] = {EXTERNAL_UNIT_ID: coordinator.data["modules"][EXTERNAL_UNIT_ID]}
    await coordinator._process_websocket_event(ws_incoming_call)
    assert coordinator.active_call is None
    assert coordinator.ringing_call["session_id"] == SESSION_ID
    coordinator.fire_call_timeout(EXTERNAL_UNIT_ID)
    assert coordinator.ringing_call is None


async def test_clear_only_matching_session_and_missing_module(coordinator):
    """An old answer cannot clear a newer pending call."""
    coordinator._fire_call_event("ring", session_id=SESSION_ID)
    assert coordinator.ringing_call["camera_entity_id"] is None
    coordinator.clear_ringing_call("old-call")
    assert coordinator.ringing_call
    coordinator.clear_ringing_call()
    assert coordinator.ringing_call is None
    coordinator.clear_ringing_call()


async def test_websocket_snapshot(
    hass, hass_ws_client, hass_read_only_access_token, coordinator, camera_entity, ws_rtc_offer
):
    """The actual authenticated command works after the ring, without replaying events."""
    hass.data[DOMAIN] = {coordinator.entry.entry_id: {"coordinator": coordinator}}
    async_register_websocket_api(hass)
    client = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    await coordinator._process_websocket_event(ws_rtc_offer)
    events = async_capture_events(hass, EVENT_CALL)
    for message_id, expected in enumerate((True, False), start=1):
        await client.send_json(
            {
                "id": message_id,
                "type": "bticino_intercom/get_active_calls",
                "entity_ids": [camera_entity.entity_id, camera_entity.entity_id],
            }
        )
        result = await client.receive_json()
        assert result["success"], result
        assert len(result["result"]["calls"]) == int(expected)
        coordinator.clear_ringing_call()
    assert not events


async def test_websocket_filters_permissions_and_entities(hass, coordinator, camera_entity):
    """Unauthorized, unrelated and unloaded cameras reveal no calls."""
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID)
    hass.data[DOMAIN] = {coordinator.entry.entry_id: {"coordinator": coordinator}}
    registry = er.async_get(hass)
    other = registry.async_get_or_create("camera", "other", "other-camera")
    sensor = registry.async_get_or_create("sensor", DOMAIN, "sensor")
    connection = MagicMock()
    msg = {"id": 1, "entity_ids": ["camera.missing", other.entity_id, sensor.entity_id, camera_entity.entity_id]}
    connection.user.permissions.check_entity.return_value = False
    ws_get_active_calls(hass, connection, msg)
    connection.send_result.assert_called_with(1, {"calls": []})
    connection.user.permissions.check_entity.assert_called_with(camera_entity.entity_id, "read")
    connection.user.permissions.check_entity.return_value = True
    hass.data[DOMAIN] = {}
    ws_get_active_calls(hass, connection, msg)
    connection.send_result.assert_called_with(1, {"calls": []})
    connection.user = None
    ws_get_active_calls(hass, connection, msg)
    connection.send_error.assert_called_once()


async def test_late_image_push_does_not_reannounce_answered_call(coordinator):
    """A late enrichment for a spent offer must not resurrect Answer controls."""
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id=SESSION_ID)
    coordinator.clear_ringing_call(SESSION_ID)
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id=SESSION_ID, snapshot_url="/local/late.jpg")
    assert coordinator.ringing_call is None
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id="next-call")
    assert coordinator.ringing_call["session_id"] == "next-call"


async def test_api_filters_other_cameras_and_keeps_homes_separate(hass, hass_ws_client, coordinator, camera_entity):
    """Only each requested live camera can expose its own home's pending ring."""
    from unittest.mock import AsyncMock

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.bticino_intercom.coordinator import BticinoIntercomCoordinator

    entry = MockConfigEntry(domain=DOMAIN, title="Other home", data={"home_id": "other-home"}, unique_id="other-home")
    entry.add_to_hass(hass)
    other = BticinoIntercomCoordinator(hass, entry, AsyncMock(), AsyncMock())
    other_camera = er.async_get(hass).async_get_or_create(
        "camera", DOMAIN, f"{entry.entry_id}_webrtc_other-module", config_entry=entry, suggested_object_id="other"
    )
    wrong_camera = er.async_get(hass).async_get_or_create(
        "camera", DOMAIN, f"{entry.entry_id}_last_snapshot", config_entry=entry, suggested_object_id="snapshot"
    )
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID, session_id=SESSION_ID)
    other._fire_call_event("ring", "other-module", session_id="other-call")
    hass.data[DOMAIN] = {
        coordinator.entry.entry_id: {"coordinator": coordinator},
        entry.entry_id: {"coordinator": other},
    }
    async_register_websocket_api(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/get_active_calls", "entity_ids": [wrong_camera.entity_id]})
    assert (await client.receive_json())["result"] == {"calls": []}
    await client.send_json(
        {"id": 2, "type": f"{DOMAIN}/get_active_calls", "entity_ids": [camera_entity.entity_id, other_camera.entity_id]}
    )
    result = await client.receive_json()
    assert [call["session_id"] for call in result["result"]["calls"]] == [SESSION_ID, "other-call"]
    assert [call["entry_id"] for call in result["result"]["calls"]] == [coordinator.entry.entry_id, entry.entry_id]


async def test_rejected_call_is_not_restored(hass, mock_setup_entry):
    """The existing reject service clears the pending display state through its end event."""
    data = hass.data[DOMAIN][mock_setup_entry.entry_id]
    coordinator = data["coordinator"]
    coordinator._active_call = {"session_id": SESSION_ID, "module_id": EXTERNAL_UNIT_ID}
    coordinator._fire_call_event("ring", EXTERNAL_UNIT_ID)
    assert coordinator.ringing_call
    await hass.services.async_call(DOMAIN, "reject_call", {"entry_id": mock_setup_entry.entry_id}, blocking=True)
    assert coordinator.ringing_call is None
