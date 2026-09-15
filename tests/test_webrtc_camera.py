"""Tests for the BTicino WebRTC camera entity."""

from unittest.mock import AsyncMock, patch

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import BRIDGE_MAC


def _get_all_webrtc_cameras(hass, mock_setup_entry):
    """Helper to retrieve the external-unit WebRTC cameras (excluding Call Home).

    Call Home is a WebRTC camera too — it dials the bridge's own module id — so
    it has to be filtered out by unique_id, not by a missing module_id.
    """
    entity_comp = hass.data["entity_components"]["camera"]
    return [
        e
        for e in entity_comp.entities
        if getattr(e, "_module_id", None) is not None and not str(e.unique_id).endswith("_call_home")
    ]


def _get_webrtc_camera(hass, mock_setup_entry):
    """Helper to retrieve the first WebRTC camera entity object."""
    cameras = _get_all_webrtc_cameras(hass, mock_setup_entry)
    return cameras[0] if cameras else None


async def test_webrtc_cameras_created_per_external_unit(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """One WebRTC camera per BNEU external unit."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    # Find cameras that are NOT snapshot/vignette
    non_event_cameras = [s for s in states if "snapshot" not in s and "vignette" not in s and "call_home" not in s]
    assert len(non_event_cameras) == 2  # Two BNEU modules


async def test_webrtc_camera_has_stream_feature(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that all WebRTC cameras support STREAM feature."""
    cameras = _get_all_webrtc_cameras(hass, mock_setup_entry)
    assert len(cameras) == 2

    for camera in cameras:
        state = hass.states.get(camera.entity_id)
        supported = state.attributes.get("supported_features", 0)
        assert supported & CameraEntityFeature.STREAM


async def test_webrtc_camera_snapshot_entities(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test that snapshot camera is created alongside WebRTC cameras."""
    states = hass.states.async_entity_ids(CAMERA_DOMAIN)
    assert len(states) == 4  # snapshot + 2 webrtc + call_home
    names = {hass.states.get(s).attributes.get("friendly_name", "") for s in states}
    assert any("Snapshot" in n for n in names)


def _get_call_home_camera(hass):
    """Return the Call Home camera entity object."""
    entity_comp = hass.data["entity_components"]["camera"]
    return next(e for e in entity_comp.entities if str(e.unique_id).endswith("_call_home"))


async def test_call_home_dials_the_indoor_unit(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Call Home must address the bridge's own module id.

    The app has no dedicated call-home message: it sends the ordinary offer and
    the call becomes a "call home" because the dialled module is not an external
    unit. A missing module_id instead selects the default external unit, which
    duplicates the doorbell camera and exhausts the device's peer slots.
    """
    camera = _get_call_home_camera(hass)
    assert camera._module_id == BRIDGE_MAC

    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_home")

    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", [].append)
    camera._cancel_answer_watchdog()

    assert signaling.send_offer.call_args.kwargs["module_id"] == BRIDGE_MAC


async def test_call_home_exists_on_voice_only_intercom(
    hass: HomeAssistant,
    mock_setup_entry_voice_only: MockConfigEntry,
) -> None:
    """A home without an external unit still gets Call Home."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(CAMERA_DOMAIN, DOMAIN, f"{mock_setup_entry_voice_only.entry_id}_call_home")
    assert entity_id is not None
    assert hass.states.get(entity_id) is not None


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


async def test_answer_mode_only_for_matching_module(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Answer mode only engages when active_call module_id matches this camera."""
    from tests.conftest import EXTERNAL_UNIT_2_ID, EXTERNAL_UNIT_ID

    cameras = _get_all_webrtc_cameras(hass, mock_setup_entry)
    # Find camera for unit 1
    camera_1 = next(c for c in cameras if c._module_id == EXTERNAL_UNIT_ID)

    # Set active call for unit 2 (NOT this camera's module)
    camera_1.coordinator._active_call = {
        "session_id": "sess_other",
        "tag_id": "tag",
        "device_id": "00:03:50:d9:a6:3b",
        "module_id": EXTERNAL_UNIT_2_ID,
        "sdp": "v=0\r\ndevice sdp\r\n",
    }

    signaling = hass.data[DOMAIN][mock_setup_entry.entry_id]["signaling_client"]
    signaling.send_offer = AsyncMock(return_value="sess_new")
    signaling.send_answer = AsyncMock()
    signaling._is_connected = True
    signaling.is_connected = True

    messages = []
    await camera_1.async_handle_async_webrtc_offer("v=0\r\na=setup:actpass\r\n", "test_sess", messages.append)

    # Should use OFFER mode (not answer) because call is for different module
    signaling.send_offer.assert_called_once()
    signaling.send_answer.assert_not_called()


async def test_answer_mode_uses_device_sdp(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """When active_call exists, camera should use answer mode."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    device_sdp = "v=0\r\no=- 123 IN IP4 0.0.0.0\r\ns=-\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=setup:actpass\r\n"
    coordinator._active_call = {
        "session_id": "test-session",
        "module_id": "00:03:50:d9:a6:3b",
        "sdp": device_sdp,
        "tag_id": "tag",
        "correlation_id": "corr",
        "device_id": "dev",
    }

    assert coordinator.active_call is not None
    assert coordinator.active_call["sdp"] == device_sdp


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


def _prep_offer_signaling(hass, entry, *, session_id: str):
    """Configure the shared mock signaling client for an offer-mode test."""
    signaling = hass.data[DOMAIN][entry.entry_id]["signaling_client"]
    signaling.ensure_connected = AsyncMock()
    signaling.resubscribe = AsyncMock()
    signaling.send_offer = AsyncMock(return_value=session_id)
    signaling.send_terminate = AsyncMock()
    signaling.session_id = session_id
    return signaling


_OFFER_SDP = "v=0\r\no=- 9 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


async def test_offer_session_terminated_on_close_without_answer(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Issue #58/#60 leak: an offer that never receives an answer must still be
    terminated when the stream closes, otherwise the device peer slot leaks and
    eventually every view fails with 'Max number of peers reached'."""
    camera = _get_webrtc_camera(hass, mock_setup_entry)
    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_leak")

    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", [].append)

    # Device never answered; the frontend tears the stream down.
    camera.close_webrtc_session("ha_sess")
    await hass.async_block_till_done()

    signaling.send_terminate.assert_called_once()


async def test_offer_session_cleaned_up_after_device_terminate(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Issue #58/#60 leak: when the device rejects the offer with a terminate
    event ('Max number of peers reached'), the session must still be cleaned up
    on close instead of leaking the peer."""
    from homeassistant.components.camera.webrtc import WebRTCError

    camera = _get_webrtc_camera(hass, mock_setup_entry)
    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_rej")

    messages = []
    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", messages.append)

    # Device rejects the offer (Max peers) via the on_event callback.
    await signaling._on_event("sess_rej", "terminate", {"data": {"error": {"message": "Max number of peers reached"}}})
    assert any(isinstance(m, WebRTCError) for m in messages)

    # Stream torn down -> the session must be terminated, not leaked.
    camera.close_webrtc_session("ha_sess")
    await hass.async_block_till_done()
    signaling.send_terminate.assert_called_once()


async def test_offer_session_terminated_on_close_after_answer(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Regression guard: the normal answered-then-closed path still terminates."""
    from homeassistant.components.camera.webrtc import WebRTCAnswer

    camera = _get_webrtc_camera(hass, mock_setup_entry)
    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_ok")

    messages = []
    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", messages.append)

    answer_sdp = "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\na=setup:active\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    await signaling._on_answer("sess_ok", answer_sdp)
    assert any(isinstance(m, WebRTCAnswer) for m in messages)

    camera.close_webrtc_session("ha_sess")
    await hass.async_block_till_done()
    signaling.send_terminate.assert_called_once()


async def test_unanswered_offer_is_terminated_by_watchdog(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """An offer the device never answers must not hold its peer slot until the
    frontend happens to give up: the watchdog hands the slot back and reports
    the failure, so the next view does not hit 'Max number of peers reached'."""
    from homeassistant.components.camera.webrtc import WebRTCError

    camera = _get_webrtc_camera(hass, mock_setup_entry)
    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_hang")

    messages = []
    with patch("custom_components.bticino_intercom.camera.ANSWER_TIMEOUT_SECONDS", 0):
        await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", messages.append)
        await hass.async_block_till_done()

    signaling.send_terminate.assert_called_once()
    assert any(isinstance(m, WebRTCError) and m.code == "no_answer" for m in messages)
    assert camera._signaling_session_id is None


async def test_own_stale_session_released_before_new_offer(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """A second attempt on the same camera hands back the slot the first attempt
    left allocated instead of asking the device for one more peer."""
    camera = _get_webrtc_camera(hass, mock_setup_entry)
    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_first")

    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", [].append)
    camera._cancel_answer_watchdog()
    assert camera._signaling_session_id == "sess_first"

    signaling.send_offer = AsyncMock(return_value="sess_second")
    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess2", [].append)
    camera._cancel_answer_watchdog()

    signaling.send_terminate.assert_called_once()
    assert camera._signaling_session_id == "sess_second"


async def test_stale_session_kept_during_active_call(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """The session of a call in progress belongs to the device, not to us —
    opening a camera must never hang up the doorbell."""
    camera = _get_webrtc_camera(hass, mock_setup_entry)
    camera.coordinator._active_call = None
    signaling = _prep_offer_signaling(hass, mock_setup_entry, session_id="sess_first")

    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess", [].append)
    camera._cancel_answer_watchdog()

    # A call arrives on the other external unit before the retry.
    camera.coordinator._active_call = {"session_id": "sess_call", "module_id": "other", "sdp": ""}
    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha_sess2", [].append)
    camera._cancel_answer_watchdog()

    signaling.send_terminate.assert_not_called()
