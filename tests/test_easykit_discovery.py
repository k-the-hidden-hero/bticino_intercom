"""Regression tests for EasyKit module discovery."""

from typing import Any

import pytest
from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.event import DOMAIN as EVENT_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.lock import DOMAIN as LOCK_DOMAIN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import (
    SUBTYPE_DOORLOCK,
    SUBTYPE_EXTERNAL_UNIT,
    SUBTYPE_STAIRCASE_LIGHT,
)
from custom_components.bticino_intercom.utils import get_module_subtype

from .conftest import BRIDGE_MAC, DOORLOCK_ID, EXTERNAL_UNIT_ID


@pytest.fixture
def mock_modules_data() -> dict[str, Any]:
    """Return an EasyKit topology without variant subtypes.

    This mirrors the BDIY topology in which the module type, rather than a
    variant string, identifies the external unit and door lock.
    """
    return {
        BRIDGE_MAC: {
            "id": BRIDGE_MAC,
            "type": "BDIY",
            "name": "EasyKit",
            "reachable": True,
            "variant": None,
        },
        EXTERNAL_UNIT_ID: {
            "id": EXTERNAL_UNIT_ID,
            "type": "BNEU",
            "name": "External unit",
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": None,
        },
        DOORLOCK_ID: {
            "id": DOORLOCK_ID,
            "type": "BNDL",
            "name": "Door lock",
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": None,
        },
        "d9a63b-0560-2ef633a2f7d3": {
            "id": "d9a63b-0560-2ef633a2f7d3",
            "type": "BNSL",
            "name": "Staircase light",
            "reachable": True,
            "bridge": BRIDGE_MAC,
            "variant": None,
        },
    }


@pytest.mark.parametrize(
    ("module_data", "expected_subtype"),
    [
        ({"type": "BNEU", "variant": "BNDL:bndl_doorlock"}, SUBTYPE_DOORLOCK),
        ({"type": "BNEU", "variant": "BNEU"}, SUBTYPE_EXTERNAL_UNIT),
        ({"type": "BNSL", "variant": None}, SUBTYPE_STAIRCASE_LIGHT),
    ],
)
def test_module_subtype_prefers_explicit_variant_then_falls_back_to_type(
    module_data: dict[str, str | None],
    expected_subtype: str,
) -> None:
    """Explicit variants win, while missing subtypes use the module type."""
    assert get_module_subtype(module_data) == expected_subtype


async def test_easykit_type_only_modules_create_supported_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """A BDIY topology discovers BNEU and BNDL entities without variants."""
    camera_entities = hass.states.async_entity_ids(CAMERA_DOMAIN)
    web_rtc_cameras = [
        entity_id
        for entity_id in camera_entities
        if "snapshot" not in entity_id and "vignette" not in entity_id and "call_home" not in entity_id
    ]

    assert len(web_rtc_cameras) == 1
    assert len(hass.states.async_entity_ids(EVENT_DOMAIN)) == 1
    assert len(hass.states.async_entity_ids(BINARY_SENSOR_DOMAIN)) == 2
    assert len(hass.states.async_entity_ids(LOCK_DOMAIN)) == 1
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) == 1

    call_sensor = next(
        entity
        for entity in hass.data["entity_components"][BINARY_SENSOR_DOMAIN].entities
        if entity.unique_id.endswith(f"_call_{EXTERNAL_UNIT_ID}")
    )
    assert call_sensor._associated_lock_ids == [DOORLOCK_ID]


async def test_easykit_type_only_staircase_light_can_be_represented_as_lock(
    hass: HomeAssistant,
    mock_setup_entry_light_as_lock: MockConfigEntry,
) -> None:
    """A type-only BNSL respects the existing light-as-lock option."""
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) == 0
    assert len(hass.states.async_entity_ids(LOCK_DOMAIN)) == 2
