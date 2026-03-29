"""Tests for the BTicino light platform."""

from unittest.mock import AsyncMock

from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pybticino.exceptions import ApiError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import BRIDGE_MAC, LIGHT_MODULE_ID


async def test_light_entity_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test light entity is created for staircase light module."""
    states = hass.states.async_entity_ids(LIGHT_DOMAIN)
    assert len(states) >= 1


async def test_light_not_created_when_light_as_lock(
    hass: HomeAssistant,
    mock_setup_entry_light_as_lock: MockConfigEntry,
) -> None:
    """Test light entity is NOT created when light_as_lock is enabled."""
    states = hass.states.async_entity_ids(LIGHT_DOMAIN)
    assert len(states) == 0


async def test_light_initial_state_off(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test light entity initial state is off."""
    entity_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_OFF


async def test_light_turn_on(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test turning the light on via service call."""
    entity_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]

    await hass.services.async_call(
        LIGHT_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Verify the API was called with correct parameters
    mock_account.async_set_module_state.assert_called_once_with(
        home_id=mock_setup_entry.data["home_id"],
        module_id=LIGHT_MODULE_ID,
        bridge_id=BRIDGE_MAC,
        state={"on": True},
    )
    # Note: state may revert to off after coordinator refresh returns fixture data


async def test_light_turn_off(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test turning the light off via service call."""
    entity_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]

    # First turn on
    await hass.services.async_call(
        LIGHT_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    mock_account.async_set_module_state.reset_mock()

    # Then turn off
    await hass.services.async_call(
        LIGHT_DOMAIN,
        "turn_off",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF

    mock_account.async_set_module_state.assert_called_once_with(
        home_id=mock_setup_entry.data["home_id"],
        module_id=LIGHT_MODULE_ID,
        bridge_id=BRIDGE_MAC,
        state={"on": False},
    )


async def test_light_turn_on_api_error_reverts(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test that turn_on reverts to off on API error."""
    entity_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]

    mock_account.async_set_module_state.side_effect = ApiError(500, "Network error")

    await hass.services.async_call(
        LIGHT_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_OFF


async def test_light_extra_attributes(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test light entity has extra state attributes."""
    entity_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]
    state = hass.states.get(entity_id)

    assert state.attributes.get("reachable") is True


async def test_light_coordinator_update(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test light state updates from coordinator data."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    entity_id = hass.states.async_entity_ids(LIGHT_DOMAIN)[0]

    # Simulate coordinator update with light on
    new_data = dict(coordinator.data)
    new_data["modules"] = dict(new_data["modules"])
    new_data["modules"][LIGHT_MODULE_ID] = dict(new_data["modules"][LIGHT_MODULE_ID])
    new_data["modules"][LIGHT_MODULE_ID]["status"] = "on"
    coordinator.async_set_updated_data(new_data)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == STATE_ON
