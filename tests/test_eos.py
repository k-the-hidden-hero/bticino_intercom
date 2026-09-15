"""Integration tests for the Classe 300 EOS (bridge BNCX, modules without 'variant').

These verify that modules identified only by their raw 'type' (no 'variant'
field) still produce the full set of entities — the gap the EOS support closes.
"""

from homeassistant.components.binary_sensor import DOMAIN as BINARY_SENSOR_DOMAIN
from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.event import DOMAIN as EVENT_DOMAIN
from homeassistant.components.light import DOMAIN as LIGHT_DOMAIN
from homeassistant.components.lock import DOMAIN as LOCK_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import EXTERNAL_UNIT_ID


async def test_eos_lock_created(hass: HomeAssistant, mock_setup_entry_eos: MockConfigEntry) -> None:
    """A BNDL module without 'variant' still yields a lock entity."""
    assert len(hass.states.async_entity_ids(LOCK_DOMAIN)) >= 1


async def test_eos_light_created(hass: HomeAssistant, mock_setup_entry_eos: MockConfigEntry) -> None:
    """A BNSL module without 'variant' still yields a light entity."""
    assert len(hass.states.async_entity_ids(LIGHT_DOMAIN)) >= 1


async def test_eos_binary_sensor_created(hass: HomeAssistant, mock_setup_entry_eos: MockConfigEntry) -> None:
    """A BNEU module without 'variant' yields a *call* binary sensor.

    Asserts the specific call-sensor entity rather than just a non-empty
    binary_sensor domain — the bridge "busy" sensor is always created
    (driven by the bridge MAC, not by the module subtype), so a bare count
    would pass even without the EOS fix.
    """
    ent_reg = er.async_get(hass)
    call_unique_id = f"{mock_setup_entry_eos.entry_id}_call_{EXTERNAL_UNIT_ID}"
    entity_id = ent_reg.async_get_entity_id(BINARY_SENSOR_DOMAIN, "bticino_intercom", call_unique_id)
    assert entity_id is not None


async def test_eos_doorbell_event_created(hass: HomeAssistant, mock_setup_entry_eos: MockConfigEntry) -> None:
    """A BNEU module without 'variant' yields a DOORBELL event entity.

    This is the entity the original file-replacement patch failed to create on
    EOS (it never updated event.py).
    """
    assert len(hass.states.async_entity_ids(EVENT_DOMAIN)) >= 1


async def test_eos_webrtc_camera_created(hass: HomeAssistant, mock_setup_entry_eos: MockConfigEntry) -> None:
    """A WebRTC camera is created for the BNEU external unit (no 'variant')."""
    ent_reg = er.async_get(hass)
    webrtc_unique_id = f"{mock_setup_entry_eos.entry_id}_webrtc_{EXTERNAL_UNIT_ID}"
    entity_id = ent_reg.async_get_entity_id(CAMERA_DOMAIN, "bticino_intercom", webrtc_unique_id)
    assert entity_id is not None
