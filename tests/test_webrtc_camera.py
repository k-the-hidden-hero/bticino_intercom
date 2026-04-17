"""Tests for the BTicino WebRTC camera entity."""

from unittest.mock import AsyncMock

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN


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


def _get_webrtc_camera(hass, mock_setup_entry):
    """Helper to retrieve the WebRTC camera entity object."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    webrtc_entity_id = next(s for s in states if "live_video" in s)
    entity_comp = hass.data["entity_components"]["camera"]
    return next(e for e in entity_comp.entities if e.entity_id == webrtc_entity_id)


async def test_answer_mode_when_active_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """When an active call with SDP exists, send_answer is called (not send_offer)."""
    camera = _get_webrtc_camera(hass, mock_setup_entry)

    # Set up an active call on the coordinator
    camera.coordinator._active_call = {
        "session_id": "sess_incoming",
        "tag_id": "tag123",
        "device_id": "00:03:50:d9:a6:3b",
        "module_id": "d9a63b-a06f-2ef633a2f733",
        "sdp": "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\n",
    }

    # Mock signaling
    signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]
    signaling.send_answer = AsyncMock()
    signaling.send_offer = AsyncMock(return_value="sess_new")
    signaling._is_connected = True
    signaling.is_connected = True

    offer_sdp = "v=0\r\no=- 9 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

    messages = []
    await camera.async_handle_async_webrtc_offer(offer_sdp, "test_session", messages.append)

    # send_answer should have been called, not send_offer
    signaling.send_answer.assert_called_once()
    signaling.send_offer.assert_not_called()

    # The SDP passed to send_answer should have actpass converted to active
    answer_sdp = signaling.send_answer.call_args[0][0]
    assert "a=setup:active" in answer_sdp
    assert "a=setup:actpass" not in answer_sdp

    # Verify WebRTCAnswer was sent to browser with device's SDP
    from homeassistant.components.camera.webrtc import WebRTCAnswer

    answer_messages = [m for m in messages if isinstance(m, WebRTCAnswer)]
    assert len(answer_messages) == 1
    assert answer_messages[0].answer == "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\n"


async def test_offer_mode_when_no_active_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """When no active call exists, send_offer is called (not send_answer)."""
    camera = _get_webrtc_camera(hass, mock_setup_entry)

    # Ensure no active call
    camera.coordinator._active_call = None

    # Mock signaling
    signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]
    signaling.send_answer = AsyncMock()
    signaling.send_offer = AsyncMock(return_value="sess_new")
    signaling._is_connected = True
    signaling.is_connected = True

    offer_sdp = "v=0\r\no=- 9 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"

    messages = []
    await camera.async_handle_async_webrtc_offer(offer_sdp, "test_session", messages.append)

    # send_offer should have been called, not send_answer
    signaling.send_offer.assert_called_once()
    signaling.send_answer.assert_not_called()
