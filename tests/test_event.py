"""Tests for the BTicino doorbell event entity."""

from homeassistant.components.event import DOMAIN as EVENT_DOMAIN
from homeassistant.components.event import EventDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import SIGNAL_CALL_RECEIVED

from .conftest import EXTERNAL_UNIT_ID


async def test_event_entity_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that a doorbell event entity is created for external unit."""
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    # There should be at least one event entity (for the external unit)
    assert len(states) >= 1


async def test_event_fires_on_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that event entity fires when a call is received."""
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    assert len(states) >= 1
    entity_id = states[0]

    # Simulate incoming call via dispatcher (state=True means call started)
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, True, EXTERNAL_UNIT_ID)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.attributes.get("event_type") == "ring"


async def test_event_does_not_fire_on_hangup(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that event entity does NOT fire on call end (state=False)."""
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    assert len(states) >= 1
    entity_id = states[0]

    # Simulate call end (state=False) — should NOT trigger event
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, False, EXTERNAL_UNIT_ID)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    # event_type should still be None (no event fired)
    assert state.attributes.get("event_type") is None


async def test_event_has_doorbell_device_class(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that the event entity has DOORBELL device class."""
    states = hass.states.async_entity_ids(EVENT_DOMAIN)
    assert len(states) >= 1
    entity_id = states[0]
    state = hass.states.get(entity_id)
    assert state.attributes.get("device_class") == EventDeviceClass.DOORBELL
