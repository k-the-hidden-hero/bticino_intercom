"""Every entity says which module it belongs to.

The whole home shares the bridge device, so without this a frontend cannot
tell which external unit a camera, a doorbell sensor, a lock or a light
belongs to, and a card with two entrances answers the wrong one.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from tests.conftest import (
    DOORLOCK_ID,
    EXTERNAL_UNIT_2_ID,
    EXTERNAL_UNIT_ID,
    STAIRCASE_LIGHT_ID,
)


@pytest.mark.parametrize(
    ("domain", "module_id"),
    [
        ("camera", EXTERNAL_UNIT_ID),
        ("camera", EXTERNAL_UNIT_2_ID),
        ("binary_sensor", EXTERNAL_UNIT_ID),
        ("lock", DOORLOCK_ID),
        ("light", STAIRCASE_LIGHT_ID),
    ],
)
async def test_entity_reports_its_module(
    hass: HomeAssistant,
    mock_setup_entry,
    domain: str,
    module_id: str,
) -> None:
    """At least one entity of each platform carries its own module id."""
    states = [state for state in hass.states.async_all(domain) if state.attributes.get("module_id") == module_id]
    assert states, f"no {domain} entity reports module {module_id}"


async def test_two_external_units_are_told_apart(hass: HomeAssistant, mock_setup_entry) -> None:
    """The two entrances must not collapse onto the same module id."""
    cameras = {
        state.entity_id: state.attributes.get("module_id")
        for state in hass.states.async_all("camera")
        if state.attributes.get("module_id")
    }
    assert EXTERNAL_UNIT_ID in cameras.values()
    assert EXTERNAL_UNIT_2_ID in cameras.values()
    assert len(set(cameras.values())) >= 2
