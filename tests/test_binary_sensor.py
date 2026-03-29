"""Tests for the BTicino binary sensor platform."""

from datetime import timedelta

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.bticino_intercom.const import (
    CALL_SENSOR_TIMEOUT,
    DOMAIN,
    SIGNAL_CALL_RECEIVED,
)

from .conftest import EXT_UNIT_MODULE_ID


async def test_binary_sensor_entity_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor entity is created for external unit."""
    states = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)
    assert len(states) >= 1


async def test_binary_sensor_initial_state_off(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor initial state is off."""
    states = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)
    entity_id = states[0]
    state = hass.states.get(entity_id)
    assert state is not None
    # Initially off (no active call)
    assert state.state == STATE_OFF


async def test_binary_sensor_call_on(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor turns on when call signal is received."""
    entity_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]

    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, True, EXT_UNIT_MODULE_ID)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_ON


async def test_binary_sensor_call_off(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor turns off when terminate signal is received."""
    entity_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]

    # Turn on
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, True, EXT_UNIT_MODULE_ID)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON

    # Turn off
    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, False, EXT_UNIT_MODULE_ID)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF


async def test_binary_sensor_auto_off_timeout(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    freezer,
) -> None:
    """Test binary sensor automatically turns off after timeout."""
    entity_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]

    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, True, EXT_UNIT_MODULE_ID)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_ON

    # Advance time past the auto-off timeout
    freezer.tick(timedelta(seconds=CALL_SENSOR_TIMEOUT + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF


async def test_binary_sensor_ignores_other_module(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor ignores signals for other modules."""
    entity_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]

    async_dispatcher_send(hass, SIGNAL_CALL_RECEIVED, True, "some_other_module")
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF


async def test_binary_sensor_extra_attributes(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor has expected extra state attributes."""
    entity_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]
    state = hass.states.get(entity_id)

    assert state.attributes.get("reachable") is True


async def test_binary_sensor_coordinator_update(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test binary sensor updates from coordinator data."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    entity_id = hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)[0]

    # Simulate module becoming unreachable
    new_data = dict(coordinator.data)
    new_data["modules"] = dict(new_data["modules"])
    new_data["modules"][EXT_UNIT_MODULE_ID] = dict(new_data["modules"][EXT_UNIT_MODULE_ID])
    new_data["modules"][EXT_UNIT_MODULE_ID]["reachable"] = False
    coordinator.async_set_updated_data(new_data)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    # When reachable is False, entity becomes unavailable
    assert state.state == "unavailable"
