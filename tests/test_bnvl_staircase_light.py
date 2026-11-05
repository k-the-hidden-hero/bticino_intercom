"""Regression tests for #103: the Classe 300X staircase light is a BNVL module.

The new 344742 generation reports its staircase light as type ``BNVL`` with
variant ``BNVL:bnvl_video_doorentry_light``, not the ``bnsl_staircase_light``
of the BNSL module, so it was not discovered at all.
"""

from typing import Any

import pytest
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.lock import DOMAIN as LOCK_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import SUBTYPE_STAIRCASE_LIGHT
from custom_components.bticino_intercom.utils import get_module_subtype

from .conftest import STAIRCASE_LIGHT_ID


@pytest.fixture
def mock_modules_data(mock_modules_data: dict[str, Any]) -> dict[str, Any]:
    """Return the default topology with the staircase light as a BNVL module."""
    bnvl_light = {
        **mock_modules_data[STAIRCASE_LIGHT_ID],
        "type": "BNVL",
        "variant": "BNVL:bnvl_video_doorentry_light",
    }
    return {**mock_modules_data, STAIRCASE_LIGHT_ID: bnvl_light}


@pytest.mark.parametrize(
    "module_data",
    [
        {"type": "BNVL", "variant": "BNVL:bnvl_video_doorentry_light"},
        {"type": "BNVL", "variant": None},
    ],
)
def test_bnvl_module_is_a_staircase_light(module_data: dict[str, str | None]) -> None:
    """Both the variant and the bare type identify the staircase light."""
    assert get_module_subtype(module_data) == SUBTYPE_STAIRCASE_LIGHT


async def test_bnvl_staircase_light_creates_a_light(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """The BNVL light gets a light entity, like a BNSL one."""
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) == 1


async def test_bnvl_staircase_light_can_be_represented_as_lock(
    hass: HomeAssistant,
    mock_setup_entry_light_as_lock: MockConfigEntry,
) -> None:
    """The light_as_lock option applies to the BNVL light as well."""
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) == 0
    assert len(hass.states.async_entity_ids(LOCK_DOMAIN)) == 2
