"""Tests for the BTicino WebRTC camera entity."""

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_webrtc_camera_created(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that a WebRTC camera entity is created."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    webrtc_entities = [s for s in states if "live_video" in s]
    assert len(webrtc_entities) == 1


async def test_webrtc_camera_has_stream_feature(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that the WebRTC camera supports STREAM feature."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    webrtc_entities = [s for s in states if "live_video" in s]
    assert len(webrtc_entities) == 1

    state = hass.states.get(webrtc_entities[0])
    supported = state.attributes.get("supported_features", 0)
    assert supported & CameraEntityFeature.STREAM


async def test_webrtc_camera_snapshot_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that snapshot and vignette cameras are also created."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    assert len(states) == 3  # snapshot + vignette + webrtc
    names = {hass.states.get(s).attributes.get("friendly_name", "") for s in states}
    assert any("Snapshot" in n for n in names)
    assert any("Vignette" in n for n in names)
    assert any("Live Video" in n for n in names)
