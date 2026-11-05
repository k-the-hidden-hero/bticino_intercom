"""Tests for native two-way audio (Home Assistant 2026.11) and session handling.

The 2026.11 frontend has its own talk button. Pressing it does not renegotiate:
the player closes the running stream and immediately sends a new offer that
carries the microphone track. That is exactly the back-to-back close + offer
that #94 showed the camera mishandling, so these tests cover both the
capability and the session bookkeeping that the talk button depends on.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from homeassistant.components import camera as camera_component
from homeassistant.components.camera.webrtc import WebRTCAnswer, WebRTCError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from webrtc_models import RTCIceCandidateInit

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import EXTERNAL_UNIT_ID

_OFFER_SDP = "v=0\r\no=- 9 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\na=recvonly\r\n"
_ANSWER_SDP = "v=0\r\no=- 1 0 IN IP4 0.0.0.0\r\na=setup:active\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"


def _camera(hass: HomeAssistant, unique_suffix: str):
    """Return the camera entity whose unique_id ends with unique_suffix."""
    return next(
        e for e in hass.data["entity_components"]["camera"].entities if str(e.unique_id).endswith(unique_suffix)
    )


def _external_unit_camera(hass: HomeAssistant):
    camera = _camera(hass, f"_webrtc_{EXTERNAL_UNIT_ID}")
    camera.coordinator._active_call = None
    return camera


def _signaling(hass: HomeAssistant, entry: MockConfigEntry, *, offer_sessions: list[str]):
    """Mock signaling client: each send_offer acks with the next session id."""
    signaling = hass.data[DOMAIN][entry.entry_id]["signaling_client"]
    signaling.ensure_connected = AsyncMock()
    signaling.resubscribe = AsyncMock()
    signaling.send_offer = AsyncMock(side_effect=list(offer_sessions))
    signaling.send_terminate = AsyncMock()
    signaling.send_candidate = AsyncMock()
    signaling.send_answer = AsyncMock()
    # The shared client's "current" session belongs to whoever offered last;
    # the camera must never fall back to it.
    signaling.session_id = "someone-elses-session"
    return signaling


async def _open(camera, ha_session: str, messages: list | None = None) -> None:
    sink = messages if messages is not None else []
    await camera.async_handle_async_webrtc_offer(_OFFER_SDP, ha_session, sink.append)
    camera._cancel_answer_watchdog()


# --- Capability ------------------------------------------------------------


@pytest.mark.skipif(
    not hasattr(camera_component, "CameraEntityCapabilities"),
    reason="CameraEntityCapabilities exists from Home Assistant 2026.11",
)
async def test_live_cameras_declare_two_way_audio(hass: HomeAssistant, mock_setup_entry: MockConfigEntry) -> None:
    """External units and Call Home carry audio both ways; the snapshot does not."""
    assert _camera(hass, f"_webrtc_{EXTERNAL_UNIT_ID}").camera_entity_capabilities.two_way_audio is True
    assert _camera(hass, "_call_home").camera_entity_capabilities.two_way_audio is True
    assert _camera(hass, "_snapshot").camera_entity_capabilities.two_way_audio is False


# --- Terminate addresses the camera's own session (#94) ----------------------


async def test_close_terminates_the_cameras_own_session(hass: HomeAssistant, mock_setup_entry: MockConfigEntry) -> None:
    """The terminate names the session, instead of pointing shared state at it."""
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-1"])
    await _open(camera, "ha-1")

    camera.close_webrtc_session("ha-1")
    await hass.async_block_till_done()

    signaling.send_terminate.assert_awaited_once_with("sess-1")


async def test_close_before_the_ack_terminates_the_offer_once_acked(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """A stream closed while its offer awaits the ack still gives its slot back.

    It used to fall back to the shared client's current session, which can be
    another camera's.
    """
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=[])
    release = asyncio.Event()

    async def slow_offer(**_kwargs):
        await release.wait()
        return "sess-late"

    signaling.send_offer = AsyncMock(side_effect=slow_offer)
    offer = hass.async_create_task(camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha-1", [].append))
    await asyncio.sleep(0)

    camera.close_webrtc_session("ha-1")
    release.set()
    await offer
    await hass.async_block_till_done()

    signaling.send_terminate.assert_awaited_once_with("sess-late")


async def test_closing_a_superseded_stream_leaves_the_new_one_alone(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """A late close for an older browser session must not hang up the current one."""
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-1", "sess-2"])
    await _open(camera, "ha-1")
    await _open(camera, "ha-2")
    signaling.send_terminate.reset_mock()

    camera.close_webrtc_session("ha-1")
    await hass.async_block_till_done()

    signaling.send_terminate.assert_not_awaited()
    assert camera._signaling_session_id == "sess-2"


# --- Talk button: close + immediate re-offer ---------------------------------


async def test_reoffer_waits_for_the_previous_terminate(hass: HomeAssistant, mock_setup_entry: MockConfigEntry) -> None:
    """The old session is terminated before the new offer asks for a peer slot."""
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-1"])
    await _open(camera, "ha-1")

    calls: list[str] = []

    async def record_terminate(session_id=None):
        calls.append(f"terminate {session_id}")

    async def record_offer(**_kwargs):
        calls.append("offer")
        return "sess-2"

    signaling.send_terminate = AsyncMock(side_effect=record_terminate)
    signaling.send_offer = AsyncMock(side_effect=record_offer)

    camera.close_webrtc_session("ha-1")
    await _open(camera, "ha-2")
    await hass.async_block_till_done()

    assert calls == ["terminate sess-1", "offer"]


async def test_late_terminate_of_the_previous_session_does_not_end_the_new_stream(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """The device's terminate for the session we just closed is not the new one's."""
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-1", "sess-2"])
    await _open(camera, "ha-1")
    camera.close_webrtc_session("ha-1")
    messages: list = []
    await _open(camera, "ha-2", messages)

    await signaling._on_event("sess-1", "terminate", {"data": {"error": {"message": "terminate"}}})
    await signaling._on_answer("sess-2", _ANSWER_SDP)

    assert not any(isinstance(m, WebRTCError) for m in messages)
    assert any(isinstance(m, WebRTCAnswer) for m in messages)


async def test_candidates_are_sent_for_the_cameras_session(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """Browser candidates go to this camera's session, not the client's latest."""
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-1"])
    await _open(camera, "ha-1")
    await signaling._on_answer("sess-1", _ANSWER_SDP)

    await camera.async_on_webrtc_candidate("ha-1", RTCIceCandidateInit(candidate="candidate:1", sdp_m_line_index=0))

    signaling.send_candidate.assert_awaited_once_with(candidate="candidate:1", sdp_m_line_index=0, session_id="sess-1")


# --- Answer mode -------------------------------------------------------------


async def test_an_answered_call_is_not_answered_twice(hass: HomeAssistant, mock_setup_entry: MockConfigEntry) -> None:
    """After the talk button restarts the stream, the ring is already answered.

    The device's offer was consumed by the first answer, so the second offer
    has to open a live view instead of answering the same call again.
    """
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-live"])
    camera.coordinator._active_call = {
        "session_id": "sess-call",
        "module_id": EXTERNAL_UNIT_ID,
        "sdp": "v=0\r\no=- 7 0 IN IP4 0.0.0.0\r\na=setup:actpass\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
    }

    await _open(camera, "ha-1")
    signaling.send_answer.assert_awaited_once()
    assert signaling.send_answer.call_args.kwargs["session_id"] == "sess-call"

    camera.close_webrtc_session("ha-1")
    await _open(camera, "ha-2")
    await hass.async_block_till_done()

    signaling.send_answer.assert_awaited_once()
    signaling.send_offer.assert_awaited_once()


async def test_offer_superseded_while_waiting_for_the_terminate_is_dropped(
    hass: HomeAssistant, mock_setup_entry: MockConfigEntry
) -> None:
    """Two quick re-offers while the old session is still being terminated.

    Both wait for the terminate; only the newest one may then offer, otherwise
    the older one overwrites its callbacks and takes a second peer slot.
    """
    camera = _external_unit_camera(hass)
    signaling = _signaling(hass, mock_setup_entry, offer_sessions=["sess-1", "sess-3"])
    await _open(camera, "ha-1")

    terminated = asyncio.Event()

    async def slow_terminate(session_id=None):
        await terminated.wait()

    signaling.send_terminate = AsyncMock(side_effect=slow_terminate)
    camera.close_webrtc_session("ha-1")

    second = hass.async_create_task(camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha-2", [].append))
    third = hass.async_create_task(camera.async_handle_async_webrtc_offer(_OFFER_SDP, "ha-3", [].append))
    await asyncio.sleep(0)
    assert signaling.send_offer.await_count == 1  # only ha-1's so far: both wait

    terminated.set()
    await second
    await third
    camera._cancel_answer_watchdog()

    assert signaling.send_offer.await_count == 2  # ha-1 and ha-3, never ha-2
    assert camera._signaling_session_id == "sess-3"
