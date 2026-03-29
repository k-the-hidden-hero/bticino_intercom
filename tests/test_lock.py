"""Tests for the BTicino lock platform."""

from unittest.mock import AsyncMock

import pytest
from homeassistant.components.lock import DOMAIN as LOCK_DOMAIN
from homeassistant.components.lock import LockState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from pybticino.exceptions import ApiError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import BRIDGE_MAC, LIGHT_MODULE_ID, LOCK_MODULE_ID


def _get_coordinator(hass: HomeAssistant, entry: MockConfigEntry):
    """Get the coordinator from hass data."""
    return hass.data[DOMAIN][entry.entry_id]["coordinator"]


async def test_lock_entity_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test lock entity is created for door lock module."""
    states = hass.states.async_entity_ids(LOCK_DOMAIN)
    assert len(states) >= 1
    # Find the lock entity (not the light_as_lock one)
    lock_states = [s for s in states if "lock" in s and "light_as_lock" not in s]
    assert len(lock_states) == 1


async def test_lock_initial_state_locked(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test lock entity initial state is locked."""
    entity_id = next(s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light_as_lock" not in s)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == LockState.LOCKED


async def test_lock_unlock_service(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test unlocking the lock via service call."""
    entity_id = next(s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light_as_lock" not in s)

    await hass.services.async_call(
        LOCK_DOMAIN,
        "unlock",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    # Optimistic update: lock should show as unlocked
    state = hass.states.get(entity_id)
    assert state.state == LockState.UNLOCKED

    # Verify API was called
    mock_account.async_set_module_state.assert_called_once_with(
        home_id=pytest.approx(mock_setup_entry.data["home_id"]),
        module_id=LOCK_MODULE_ID,
        bridge_id=BRIDGE_MAC,
        state={"lock": False},
        timezone=hass.config.time_zone,
    )


async def test_lock_lock_service(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test locking the lock via service call."""
    entity_id = next(s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light_as_lock" not in s)

    # First unlock
    await hass.services.async_call(
        LOCK_DOMAIN,
        "unlock",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    mock_account.async_set_module_state.reset_mock()

    # Then lock
    await hass.services.async_call(
        LOCK_DOMAIN,
        "lock",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == LockState.LOCKED

    mock_account.async_set_module_state.assert_called_once_with(
        home_id=mock_setup_entry.data["home_id"],
        module_id=LOCK_MODULE_ID,
        bridge_id=BRIDGE_MAC,
        state={"lock": True},
        timezone=hass.config.time_zone,
    )


async def test_lock_unlock_api_error_reverts(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test that unlock reverts to locked on API error."""
    entity_id = next(s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light_as_lock" not in s)

    mock_account.async_set_module_state.side_effect = ApiError(500, "Network error")

    await hass.services.async_call(
        LOCK_DOMAIN,
        "unlock",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == LockState.LOCKED


async def test_lock_extra_attributes(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test lock entity has extra state attributes."""
    entity_id = next(s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light_as_lock" not in s)
    state = hass.states.get(entity_id)

    assert state.attributes.get("reachable") is True


async def test_lock_coordinator_update(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test lock state updates from coordinator data."""
    coordinator = _get_coordinator(hass, mock_setup_entry)
    entity_id = next(s for s in hass.states.async_entity_ids(LOCK_DOMAIN) if "light_as_lock" not in s)

    # Simulate coordinator update with unlocked state
    new_data = dict(coordinator.data)
    new_data["modules"] = dict(new_data["modules"])
    new_data["modules"][LOCK_MODULE_ID] = dict(new_data["modules"][LOCK_MODULE_ID])
    new_data["modules"][LOCK_MODULE_ID]["lock"] = False
    coordinator.async_set_updated_data(new_data)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == LockState.UNLOCKED


# --- Light as Lock tests ---


async def test_light_as_lock_entity_created(
    hass: HomeAssistant,
    mock_setup_entry_light_as_lock: MockConfigEntry,
) -> None:
    """Test light_as_lock entity is created when option is enabled.

    With light_as_lock=True, we expect 2 lock entities:
    - BticinoLock for the doorlock module
    - BticinoLightAsLock for the staircase light module
    """
    states = hass.states.async_entity_ids(LOCK_DOMAIN)
    assert len(states) == 2


async def test_light_as_lock_unlock_sends_on(
    hass: HomeAssistant,
    mock_setup_entry_light_as_lock: MockConfigEntry,
    mock_account: AsyncMock,
) -> None:
    """Test unlocking light_as_lock sends on=True to the API."""
    # With light_as_lock, there are 2 lock entities. Find the one for the light module.
    all_locks = hass.states.async_entity_ids(LOCK_DOMAIN)
    # The light_as_lock entity has "staircase_light" in its entity_id (from module name)
    light_lock_entities = [s for s in all_locks if "staircase" in s or "light" in s.lower()]
    assert len(light_lock_entities) == 1
    entity_id = light_lock_entities[0]

    await hass.services.async_call(
        LOCK_DOMAIN,
        "unlock",
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state == LockState.UNLOCKED

    mock_account.async_set_module_state.assert_called_once_with(
        home_id=mock_setup_entry_light_as_lock.data["home_id"],
        module_id=LIGHT_MODULE_ID,
        bridge_id=BRIDGE_MAC,
        state={"on": True},
        timezone=hass.config.time_zone,
    )
