"""Platform for camera integration."""

import logging
from datetime import datetime
from typing import Any

import aiohttp
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.camera.webrtc import (
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import utc_from_timestamp, utcnow
from pybticino import AsyncAccount, SignalingClient
from webrtc_models import RTCConfiguration, RTCIceCandidateInit, RTCIceServer

from .const import DOMAIN, IMAGE_CACHE_SECONDS
from .coordinator import BticinoIntercomCoordinator
from .utils import format_timestamp_iso

_LOGGER = logging.getLogger(__name__)

# How long to cache the image locally after fetching (seconds)
# Prevents re-downloading the same image repeatedly between coordinator updates


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino camera platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Only add cameras if the bridge is identified
    if not coordinator.main_device_id:
        _LOGGER.warning("No bridge device ID found, cannot set up cameras.")
        return

    account: AsyncAccount = hass.data[DOMAIN][entry.entry_id]["account"]
    signaling_client: SignalingClient = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    entities: list[Camera] = [
        BticinoSnapshotCamera(coordinator),
        BticinoVignetteCamera(coordinator),
    ]

    # Create one WebRTC camera per external unit (BNEU module)
    for mid, mdata in coordinator.data.get("modules", {}).items():
        variant = mdata.get("variant", "")
        if "bneu_external_unit" in variant:
            entities.append(
                BticinoWebRTCCamera(
                    coordinator,
                    account,
                    signaling_client,
                    module_id=mid,
                    module_name=mdata.get("name", mid),
                )
            )

    async_add_entities(entities)


class BticinoBaseEventCamera(CoordinatorEntity[BticinoIntercomCoordinator], Camera):
    """Base class for cameras showing the latest event image."""

    _attr_entity_registry_enabled_default = True  # Changed to True
    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature(0)  # No streaming, no controls
    _unrecorded_attributes = frozenset({"image_url", "expires_at_iso", "event_time_iso"})

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        image_type: str,  # 'snapshot' or 'vignette'
    ) -> None:
        """Initialize the base event camera."""
        super().__init__(coordinator)
        Camera.__init__(self)  # Initialize base Camera class
        self._image_type = image_type
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_{image_type}"
        # Set name in subclasses
        self._image_url: str | None = None
        self._image_expires_at: datetime | None = None
        self._event_time: datetime | None = None
        self._cached_image: bytes | None = None
        self._cached_image_time: datetime | None = None
        self._update_state()  # Initial update

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the main bridge device."""
        if not self.coordinator.main_device_id:
            return DeviceInfo(identifiers={(DOMAIN, self.coordinator.entry.entry_id)})
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
        )
        # Extract bridge module data to potentially get model info
        bridge_module_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id)
        model = bridge_module_data.get("type") if bridge_module_data else None

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)},
            name=device_name,  # Use dynamic name
            manufacturer="BTicino",
            model=model,  # Add model if available
        )

    @property
    def available(self) -> bool:
        """Return True if the coordinator succeeded and we have an image URL."""
        return self.coordinator.last_update_success and self._image_url is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {
            "image_url": self._image_url,
            "expires_at_iso": format_timestamp_iso(self._image_expires_at),
            "event_time_iso": format_timestamp_iso(self._event_time),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        old_url = self._image_url
        self._update_state()
        if self._image_url != old_url:
            _LOGGER.debug("Image URL changed for %s, clearing cache.", self.entity_id)
            self._cached_image = None
            self._cached_image_time = None
        self.async_write_ha_state()

    def _extract_image_from_event(self, event: dict) -> tuple[str | None, int | float | None, int | float | None]:
        """Extract image URL, expiry, and event time from an event dict.

        Supports two formats:
        - WS status events: snapshot_url/vignette_url directly in the event dict
        - API history events: subevents[0].snapshot.url / subevents[0].vignette.url

        Returns (image_url, expires_at_ts, event_time_ts) or (None, None, time) if no image.
        """
        event_time_ts = event.get("time") or event.get("timestamp")

        # Format B (WS status events): direct URL fields
        url_key = f"{self._image_type}_url"  # "snapshot_url" or "vignette_url"
        direct_url = event.get(url_key)
        if direct_url:
            return direct_url, None, event_time_ts

        # Format from API history: nested in subevents
        subevents = event.get("subevents")
        if subevents and isinstance(subevents, list) and len(subevents) > 0:
            first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}
            event_time_ts = first_subevent.get("time") or event_time_ts
            image_data = first_subevent.get(self._image_type)
            if isinstance(image_data, dict) and image_data.get("url"):
                return image_data["url"], image_data.get("expires_at"), event_time_ts

        return None, None, event_time_ts

    def _update_state_internal(self) -> None:
        """Update internal state variables from coordinator data."""
        image_url = None
        expires_at_ts = None
        event_time_ts = None

        # First check the last WS event (most recent, real-time)
        last_event = self.coordinator.data.get("last_event")
        if last_event:
            image_url, expires_at_ts, event_time_ts = self._extract_image_from_event(last_event)

        # If no image found in last_event, search through event history
        if not image_url:
            events = self.coordinator.data.get("events_history", {}).get(self.coordinator.home_id, [])
            for event in events:
                url, exp, evt_time = self._extract_image_from_event(event)
                if url:
                    image_url, expires_at_ts, event_time_ts = url, exp, evt_time
                    break

        self._image_url = image_url
        self._image_expires_at = (
            utc_from_timestamp(expires_at_ts) if isinstance(expires_at_ts, int | float) and expires_at_ts > 0 else None
        )
        self._event_time = (
            utc_from_timestamp(event_time_ts) if isinstance(event_time_ts, int | float) and event_time_ts > 0 else None
        )

    def _update_state(self) -> None:
        """Update internal state from coordinator data."""
        self._update_state_internal()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return bytes of camera image."""
        now = utcnow()

        # Return cached image if valid and not expired
        if (
            self._cached_image is not None
            and self._cached_image_time is not None
            and (now - self._cached_image_time).total_seconds() < IMAGE_CACHE_SECONDS
        ):
            return self._cached_image

        # Check if the image URL is present and not expired
        if not self._image_url:
            return None
        if self._image_expires_at and now >= self._image_expires_at:
            _LOGGER.debug("%s: Image URL has expired", self.entity_id)
            return None

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self._image_url) as response:
                response.raise_for_status()
                image_bytes = await response.read()
                self._cached_image = image_bytes
                self._cached_image_time = now
                _LOGGER.debug(
                    "%s: Fetched and cached image (%d bytes)",
                    self.entity_id,
                    len(image_bytes),
                )
                return image_bytes
        except aiohttp.ClientError as err:
            _LOGGER.error("%s: Error fetching camera image: %s", self.entity_id, err)
            return None
        except Exception as err:
            _LOGGER.error("%s: Unexpected error fetching image: %s", self.entity_id, err)
            return None


class BticinoSnapshotCamera(BticinoBaseEventCamera):
    """Representation of the Last Event Snapshot Camera."""

    _attr_name = "Last Event Snapshot"

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the snapshot camera."""
        super().__init__(coordinator, image_type="snapshot")


class BticinoVignetteCamera(BticinoBaseEventCamera):
    """Representation of the Last Event Vignette Camera."""

    _attr_name = "Last Event Vignette"

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the vignette camera."""
        super().__init__(coordinator, image_type="vignette")


class BticinoWebRTCCamera(CoordinatorEntity[BticinoIntercomCoordinator], Camera):
    """BTicino WebRTC camera for live video from the intercom.

    Uses HA's native async WebRTC support. When the frontend requests a stream,
    this entity sends an SDP offer to the BTicino device via the Netatmo
    signaling WebSocket and returns the answer SDP for peer connection setup.

    ICE candidates from the browser are buffered until the signaling session
    is established (the ack from the server can take 1-2 seconds).
    """

    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature.STREAM
    _attr_entity_registry_enabled_default = True

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        account: AsyncAccount,
        signaling_client: SignalingClient,
        module_id: str,
        module_name: str,
    ) -> None:
        """Initialize the WebRTC camera."""
        super().__init__(coordinator)
        Camera.__init__(self)
        self._account = account
        self._signaling = signaling_client
        self._module_id = module_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_webrtc_{module_id}"
        self._attr_name = module_name
        self._turn_servers: list[RTCIceServer] = []
        # Buffer for ICE candidates that arrive before session is ready
        self._pending_candidates: list[RTCIceCandidateInit] = []
        self._session_ready = False
        # Per-camera signaling session ID (the shared SignalingClient tracks
        # only the LAST session across all cameras)
        self._signaling_session_id: str | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the main bridge device."""
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
        )
        bridge_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id)
        model = bridge_data.get("type") if bridge_data else None
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)},
            name=device_name,
            manufacturer="BTicino",
            model=model,
        )

    @property
    def available(self) -> bool:
        """Return True if the coordinator has data and bridge is known."""
        return self.coordinator.last_update_success and self.coordinator.main_device_id is not None

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        """Return a snapshot image if available (from last event)."""
        last_event = self.coordinator.data.get("last_event", {})
        snapshot_url = last_event.get("snapshot_url")
        if not snapshot_url:
            return None

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(snapshot_url) as resp:
                resp.raise_for_status()
                return await resp.read()
        except aiohttp.ClientError:
            _LOGGER.debug("Failed to fetch snapshot for WebRTC camera")
            return None

    # --- SDP manipulation methods ---
    #
    # The BTicino device requires specific SDP attributes to enable audio.
    # The browser and device see DIFFERENT SDPs — we act as a translator:
    #
    #   Browser offer              What we send to device     Device answer           What browser sees
    #   ─────────────              ──────────────────────     ─────────────           ─────────────────
    #   audio: recvonly    ──►     audio: sendrecv            audio: sendrecv   ──►   audio: sendonly
    #   video: recvonly    ──►     video: recvonly             video: sendonly   ──►   video: sendonly
    #   setup: actpass     ──►     setup: actpass              setup: active    ──►   setup: active
    #
    # When the browser natively sends sendrecv (e.g., two-way audio card),
    # no rewriting is needed — the SDP passes through unchanged in both directions.
    #
    # Why this matters:
    # - The device only transmits audio if it sees "sendrecv" in the offer
    #   (discovered by comparing with the official BTicino/Netatmo mobile app)
    # - But the browser rejects an answer with "sendrecv" when its offer said
    #   "recvonly" (RFC 3264: direction mismatch → "Incompatible send direction")
    # - So we tell the device "sendrecv" to activate audio, then rewrite the
    #   answer back to "sendonly" before the browser sees it
    #
    # Methods:
    # - _enable_audio_sendrecv():      browser offer → device (recvonly → sendrecv)
    # - _fix_answer_audio_direction(): device answer → browser (sendrecv → sendonly)
    # - convert_offer_to_answer_sdp(): DTLS role for answer mode (actpass → active)

    @staticmethod
    def _enable_audio_sendrecv(sdp: str) -> str:
        """Change audio direction from recvonly to sendrecv in the outgoing SDP offer.

        Applied to the browser's offer BEFORE sending to the BTicino device.
        Only modifies the audio m-section; video stays recvonly.

        Without this: device responds with sendonly but transmits no audio data.
        With this: device sees sendrecv, enables audio transmission.
        """
        lines = sdp.split("\r\n")
        result = []
        in_audio = False
        for line in lines:
            if line.startswith("m=audio"):
                in_audio = True
            elif line.startswith("m="):
                in_audio = False

            if in_audio and line == "a=recvonly":
                result.append("a=sendrecv")
            else:
                result.append(line)
        return "\r\n".join(result)

    @staticmethod
    def _fix_answer_audio_direction(answer_sdp: str) -> str:
        """Rewrite audio direction in the device's answer for browser compatibility.

        Applied to the device's answer BEFORE forwarding to the browser.
        Only modifies the audio m-section; video is left unchanged.

        The browser's original offer has recvonly for audio. The device may
        respond with sendrecv ("I send and receive") or recvonly ("I receive").
        Neither is compatible with the browser's recvonly offer — the browser
        rejects any answer that implies receiving audio it didn't offer to send.

        Fix: force audio to sendonly in the answer, which is the only direction
        compatible with the browser's recvonly offer per RFC 3264.
        """
        lines = answer_sdp.split("\r\n")
        result = []
        in_audio = False
        for line in lines:
            if line.startswith("m=audio"):
                in_audio = True
            elif line.startswith("m="):
                in_audio = False

            if in_audio and line in ("a=sendrecv", "a=recvonly"):
                result.append("a=sendonly")
            else:
                result.append(line)
        return "\r\n".join(result)

    @staticmethod
    def convert_offer_to_answer_sdp(offer_sdp: str) -> str:
        """Convert a browser SDP offer to be usable as an answer to the device.

        Used in answer mode (incoming call): the browser's offer is sent to
        the device as an "answer" to the device's original call offer.
        The DTLS setup role must change from actpass (offerer) to active (answerer).
        """
        return offer_sdp.replace("a=setup:actpass", "a=setup:active")

    async def _flush_pending_candidates(self) -> None:
        """Send all buffered ICE candidates now that the session is ready."""
        if not self._pending_candidates:
            return
        _LOGGER.debug("Flushing %d buffered ICE candidates", len(self._pending_candidates))
        for candidate in self._pending_candidates:
            await self._signaling.send_candidate(
                candidate=candidate.candidate,
                sdp_m_line_index=candidate.sdp_m_line_index or 0,
            )
        self._pending_candidates.clear()

    async def async_handle_async_webrtc_offer(
        self,
        offer_sdp: str,
        session_id: str,
        send_message: WebRTCSendMessage,
    ) -> None:
        """Handle a WebRTC offer from the HA frontend.

        Sends the browser's SDP offer to the BTicino device via the signaling
        WebSocket. The device responds with an SDP answer (or a terminate error
        if it's not accepting connections).
        """
        device_id = self.coordinator.main_device_id
        if not device_id:
            send_message(WebRTCError(code="no_device", message="No bridge device found"))
            return

        self._session_ready = False
        self._pending_candidates.clear()

        try:
            # Ensure signaling is connected
            if not self._signaling.is_connected:
                await self._signaling.connect()

            # Set up callbacks for this session
            async def on_answer(sig_session_id: str, sdp: str) -> None:
                _LOGGER.info("Received answer SDP for session %s", sig_session_id)
                self._signaling_session_id = sig_session_id
                # Only fix audio direction if we rewrote the offer from recvonly to sendrecv.
                # If the browser natively sent sendrecv (e.g., two-way audio card),
                # the answer's sendrecv is correct and must not be downgraded.
                if audio_was_rewritten:
                    sdp = self._fix_answer_audio_direction(sdp)
                send_message(WebRTCAnswer(answer=sdp))
                # Device has processed our offer and replied — safe to send ICE candidates now
                self._session_ready = True
                await self._flush_pending_candidates()

            async def on_candidate(sig_session_id: str, ice: dict) -> None:
                candidate_str = ice.get("candidate", "")
                sdp_m_line_index = ice.get("sdp_m_line_index", ice.get("sdpMLineIndex", 0))
                _LOGGER.debug("Received remote ICE candidate (m=%d)", sdp_m_line_index)
                send_message(
                    WebRTCCandidate(
                        RTCIceCandidateInit(
                            candidate=candidate_str,
                            sdp_m_line_index=sdp_m_line_index,
                        )
                    )
                )

            async def on_event(sig_session_id: str, event_type: str, data: dict) -> None:
                error = data.get("data", {}).get("error", {})
                error_msg = error.get("message", event_type) if error else event_type
                _LOGGER.warning("Signaling event %s: %s", event_type, error_msg)
                send_message(WebRTCError(code=event_type, message=error_msg))

            self._signaling._on_answer = on_answer
            self._signaling._on_candidate = on_candidate
            self._signaling._on_event = on_event

            # Enable bidirectional audio — the device only sends audio when
            # it sees sendrecv (like the mobile app does)
            modified_offer = self._enable_audio_sendrecv(offer_sdp)
            audio_was_rewritten = modified_offer != offer_sdp
            offer_sdp = modified_offer

            active_call = self.coordinator.active_call
            if active_call and active_call.get("sdp") and active_call.get("module_id") == self._module_id:
                # --- Answer mode: respond to the device's incoming call ---
                _LOGGER.info(
                    "Answering incoming call (session=%s, device=%s)",
                    active_call.get("session_id"),
                    device_id,
                )
                answer_sdp = self.convert_offer_to_answer_sdp(offer_sdp)
                await self._signaling.send_answer(answer_sdp)

                # Send the device's original offer to the browser as the "answer".
                # The browser needs a remote SDP to complete the WebRTC handshake.
                # The device's offer serves this role — it contains the device's
                # media capabilities, ICE credentials, and DTLS fingerprint.
                device_sdp = active_call["sdp"]
                send_message(WebRTCAnswer(answer=device_sdp))
            else:
                # --- Offer mode: initiate on-demand call ---
                _LOGGER.info("Sending WebRTC offer to device %s (module=%s)", device_id, self._module_id)
                await self._signaling.send_offer(
                    device_id=device_id,
                    sdp=offer_sdp,
                    module_id=self._module_id,
                )

            # In answer mode, we're ready immediately (no on_answer callback expected).
            # In offer mode, on_answer handles this when the device responds.
            if active_call and active_call.get("sdp") and active_call.get("module_id") == self._module_id:
                self._signaling_session_id = self._signaling.session_id
                self._session_ready = True
                await self._flush_pending_candidates()

        except Exception as err:
            _LOGGER.exception("Failed to handle WebRTC offer")
            send_message(WebRTCError(code="offer_failed", message=str(err)))

    async def async_on_webrtc_candidate(self, session_id: str, candidate: RTCIceCandidateInit) -> None:
        """Forward an ICE candidate from the HA frontend to the device.

        If the signaling session isn't ready yet, buffer the candidate
        and send it once the session is established.
        """
        if not self._session_ready or not self._signaling.session_id:
            self._pending_candidates.append(candidate)
            return

        await self._signaling.send_candidate(
            candidate=candidate.candidate,
            sdp_m_line_index=candidate.sdp_m_line_index or 0,
        )

    @callback
    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Return WebRTC client configuration with TURN servers."""
        return WebRTCClientConfiguration(
            configuration=RTCConfiguration(ice_servers=list(self._turn_servers)),
        )

    async def async_added_to_hass(self) -> None:
        """Fetch TURN servers when entity is added."""
        await super().async_added_to_hass()

        try:
            ice_servers_raw = await self._account.async_get_turn_servers()
            self._turn_servers = [
                RTCIceServer(
                    urls=server.get("urls", server.get("url", [])),
                    username=server.get("username"),
                    credential=server.get("credential"),
                )
                for server in ice_servers_raw
            ]
            _LOGGER.info("Loaded %d TURN/STUN servers", len(self._turn_servers))
        except Exception:
            _LOGGER.warning("Failed to fetch TURN servers, WebRTC may not work behind NAT")

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close the WebRTC session by sending terminate."""
        _LOGGER.info(
            "Closing WebRTC session for %s (signaling_session=%s)",
            self._module_id,
            self._signaling_session_id,
        )
        self._session_ready = False
        self._pending_candidates.clear()
        if self._signaling_session_id:
            # Point the shared signaling client at this camera's session so
            # send_terminate uses the correct session_id.  HA runs on a
            # single-threaded event loop, so there is no race with other
            # cameras between setting the id and awaiting send_terminate.
            self._signaling._session_id = self._signaling_session_id
            self._signaling_session_id = None
            self.hass.async_create_task(self._signaling.send_terminate())
