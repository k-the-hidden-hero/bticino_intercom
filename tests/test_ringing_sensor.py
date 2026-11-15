"""Current ringing state is readable without a custom-event subscription."""

import logging
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from custom_components.bticino_intercom.const import (
    CALL_RETRANSMIT_WINDOW,
    CALL_SENSOR_TIMEOUT,
    DOMAIN,
    EVENT_CALL,
    SIGNAL_CALL_RECEIVED,
)

from .conftest import DOORLOCK_ID, EXTERNAL_UNIT_2_ID, EXTERNAL_UNIT_ID, SESSION_ID


@pytest.fixture
def live_ring(hass, mock_setup_entry):
    """Use the real integration and its actual sensor state."""
    coord = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    # Image downloading/persistent history are covered separately; never fetch
    # the example snapshot URLs while testing entity state transitions.
    coord.history = None
    entity_id = next(
        state.entity_id
        for state in hass.states.async_all("binary_sensor")
        if state.attributes.get("module_id") == EXTERNAL_UNIT_ID
    )
    return coord, entity_id


async def test_current_metadata_is_separate_from_history(hass, live_ring, ws_rtc_offer, ws_incoming_call):
    coord, entity_id = live_ring
    coord.data["events_history"][coord.home_id] = [
        {
            "type": "call",
            "module_id": EXTERNAL_UNIT_ID,
            "end": True,
            "subevents": [{"session_id": "historical", "snapshot": {"url": "https://example.com/old.jpg"}}],
        }
    ]
    coord.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["session_id"] == "historical"
    assert hass.states.get(entity_id).attributes["ringing"] is None
    await coord._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "on"
    assert state.attributes["ringing"] == {"session_id": SESSION_ID}
    started = state.last_changed
    await coord._process_websocket_event(ws_incoming_call)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.last_changed == started
    assert state.attributes["ringing"] == {
        "session_id": SESSION_ID,
        "snapshot_url": "https://example.com/snapshot.jpg",
        "vignette_url": "https://example.com/vignette.jpg",
    }
    assert state.attributes["session_id"] == "historical"
    # A metadata-free event must neither erase the image nor expose signaling.
    coord._fire_call_event("ring", EXTERNAL_UNIT_ID)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["ringing"] == state.attributes["ringing"]


@pytest.mark.parametrize(
    "outcome", ["ws_accepted_call", "ws_missed_call", "ws_rtc_terminate", "ws_rtc_rescind", "answer", "reject"]
)
async def test_ring_outcomes_clear_metadata(hass, live_ring, ws_rtc_offer, request, outcome):
    coord, entity_id = live_ring
    await coord._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()
    if outcome == "answer":
        coord.mark_call_answered(EXTERNAL_UNIT_ID)
    elif outcome == "reject":
        await hass.services.async_call(DOMAIN, "reject_call", {"entry_id": coord.entry.entry_id}, blocking=True)
    else:
        await coord._process_websocket_event(request.getfixturevalue(outcome))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.state == "off"
    assert state.attributes["ringing"] is None


@pytest.mark.parametrize("timeout", [CALL_RETRANSMIT_WINDOW, CALL_SENSOR_TIMEOUT])
async def test_sensor_timeout_is_the_only_expiry(hass, live_ring, ws_rtc_offer, ws_incoming_call, freezer, timeout):
    coord, entity_id = live_ring
    del coord.data["modules"][EXTERNAL_UNIT_2_ID]
    await coord._process_websocket_event(ws_incoming_call if timeout == CALL_SENSOR_TIMEOUT else ws_rtc_offer)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=timeout + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["ringing"] is None


@pytest.mark.parametrize(
    "outcome", ["ws_rtc_rescind", "ws_rtc_terminate", "ws_missed_call", "reject", "timeout", "answer"]
)
async def test_late_enrichment_preserves_event_without_reopening_sensor(
    hass, live_ring, ws_rtc_offer, ws_incoming_call, outcome, request
):
    coord, entity_id = live_ring
    del coord.data["modules"][EXTERNAL_UNIT_2_ID]
    await coord._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()
    if outcome == "reject":
        await hass.services.async_call(DOMAIN, "reject_call", {"entry_id": coord.entry.entry_id}, blocking=True)
    elif outcome == "timeout":
        coord.fire_call_timeout(EXTERNAL_UNIT_ID)
    elif outcome == "answer":
        coord.mark_call_answered(EXTERNAL_UNIT_ID)
    else:
        await coord._process_websocket_event(request.getfixturevalue(outcome))
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_CALL)
    await coord._process_websocket_event(ws_incoming_call)
    await hass.async_block_till_done()
    assert any(event.data["type"] == "ring" for event in events)
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["ringing"] is None


async def test_status_only_ring_can_be_rejected(hass, live_ring, ws_incoming_call, mock_signaling_client):
    coord, entity_id = live_ring
    del coord.data["modules"][EXTERNAL_UNIT_2_ID]
    await coord._process_websocket_event(ws_incoming_call)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["ringing"]["session_id"] == SESSION_ID
    assert coord.active_call is None
    await hass.services.async_call(DOMAIN, "reject_call", {"entry_id": coord.entry.entry_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["ringing"] is None
    mock_signaling_client.send_terminate.assert_not_called()


async def test_status_only_reject_after_timeout_is_a_no_op(hass, live_ring, ws_incoming_call):
    coord, entity_id = live_ring
    del coord.data["modules"][EXTERNAL_UNIT_2_ID]
    await coord._process_websocket_event(ws_incoming_call)
    await hass.async_block_till_done()
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, False, EXTERNAL_UNIT_ID)
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_CALL)
    await hass.services.async_call(DOMAIN, "reject_call", {"entry_id": coord.entry.entry_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "off"
    assert not events


async def test_next_ring_does_not_inherit_a_session_or_image(hass, live_ring, ws_incoming_call):
    coord, entity_id = live_ring
    del coord.data["modules"][EXTERNAL_UNIT_2_ID]
    await coord._process_websocket_event(ws_incoming_call)
    await hass.async_block_till_done()
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, False, EXTERNAL_UNIT_ID)
    await hass.async_block_till_done()
    new = deepcopy(ws_incoming_call)
    for key in ("session_id", "subevent_id", "snapshot_url", "vignette_url"):
        new["extra_params"].pop(key)
    await coord._process_websocket_event(new)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"
    assert hass.states.get(entity_id).attributes["ringing"] == {}


async def test_foreign_and_closed_metadata_cannot_replace_current_ring(hass, live_ring, ws_rtc_offer):
    coord, entity_id = live_ring
    await coord._process_websocket_event(ws_rtc_offer)
    await hass.async_block_till_done()
    original = hass.states.get(entity_id).attributes["ringing"]
    for extra in ({"entry_id": "other-home"}, {"module_id": EXTERNAL_UNIT_2_ID}, {"session_id": "closed"}):
        coord._mark_session_closed("closed")
        hass.bus.async_fire(
            EVENT_CALL,
            {
                "type": "ring",
                "entry_id": coord.entry.entry_id,
                "module_id": EXTERNAL_UNIT_ID,
                "snapshot_url": "wrong",
                **extra,
            },
        )
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["ringing"] == original


async def test_new_session_replaces_images_and_ignores_an_old_end(hass, live_ring, ws_rtc_offer, ws_incoming_call):
    coord, entity_id = live_ring
    await coord._process_websocket_event(ws_rtc_offer)
    await coord._process_websocket_event(ws_incoming_call)
    await hass.async_block_till_done()
    next_offer = deepcopy(ws_rtc_offer)
    next_offer["extra_params"]["session_id"] = "new-session"
    await coord._process_websocket_event(next_offer)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["ringing"] == {"session_id": "new-session"}
    coord._fire_call_event("end", EXTERNAL_UNIT_ID, session_id=SESSION_ID)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "on"


@pytest.fixture
def debug_logging(caplog):
    """The test-ring service exists only while the integration logs at DEBUG."""
    caplog.set_level(logging.DEBUG, logger="custom_components.bticino_intercom")


@pytest.fixture
def test_ring(hass, debug_logging, live_ring):
    """Both external units are present, so a push alone can't name one."""
    coord, _ = live_ring
    assert EXTERNAL_UNIT_2_ID in coord.data["modules"]
    coord.history = AsyncMock()

    def module_entity(domain, module_id):
        return next(
            state.entity_id for state in hass.states.async_all(domain) if state.attributes.get("module_id") == module_id
        )

    return coord, module_entity


async def test_test_ring_rings_the_named_entrance(hass, test_ring):
    coord, module_entity = test_ring
    sensor_2 = module_entity("binary_sensor", EXTERNAL_UNIT_2_ID)
    events = async_capture_events(hass, EVENT_CALL)
    await hass.services.async_call(
        DOMAIN,
        "inject_test_event",
        {"camera": module_entity("camera", EXTERNAL_UNIT_2_ID), "session_id": "test-1"},
        blocking=True,
    )
    await hass.async_block_till_done()
    state = hass.states.get(sensor_2)
    assert state.state == "on"
    assert state.attributes["ringing"]["session_id"] == "test-1"
    assert hass.states.get(module_entity("binary_sensor", EXTERNAL_UNIT_ID)).state == "off"
    assert [(e.data["type"], e.data["module_id"]) for e in events] == [("ring", EXTERNAL_UNIT_2_ID)]
    coord.history.async_record_call.assert_not_awaited()


async def test_test_ring_records_history_only_when_asked(hass, test_ring):
    coord, module_entity = test_ring
    await hass.services.async_call(
        DOMAIN,
        "inject_test_event",
        {"camera": module_entity("camera", EXTERNAL_UNIT_ID), "record_history": True},
        blocking=True,
    )
    coord.history.async_record_call.assert_awaited_once()


async def test_test_ring_needs_an_entrance_camera(hass, test_ring):
    _, module_entity = test_ring
    lock = module_entity("lock", DOORLOCK_ID)
    # ServiceNotFound is a ServiceValidationError too: match the message.
    with pytest.raises(ServiceValidationError, match="is not an entrance"):
        await hass.services.async_call(DOMAIN, "inject_test_event", {"camera": lock}, blocking=True)


async def test_status_only_ring_is_rejected_among_several_units(hass, test_ring, mock_signaling_client):
    coord, module_entity = test_ring
    sensor_2 = module_entity("binary_sensor", EXTERNAL_UNIT_2_ID)
    await hass.services.async_call(
        DOMAIN,
        "inject_test_event",
        {"camera": module_entity("camera", EXTERNAL_UNIT_2_ID), "session_id": "test-2"},
        blocking=True,
    )
    await hass.async_block_till_done()
    events = async_capture_events(hass, EVENT_CALL)
    await hass.services.async_call(DOMAIN, "reject_call", {"entry_id": coord.entry.entry_id}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(sensor_2).state == "off"
    assert [(e.data["type"], e.data["module_id"], e.data["session_id"], e.data["reason"]) for e in events] == [
        ("end", EXTERNAL_UNIT_2_ID, "test-2", "rejected")
    ]
    mock_signaling_client.send_terminate.assert_not_called()
