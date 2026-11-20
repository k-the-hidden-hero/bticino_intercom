"""WebSocket API for the BTicino Intercom call history and native call answering.

Serves:
- ``bticino_intercom/history``: recorded call history with signed thumbnail URLs.
- ``bticino_intercom/call/offer``: active incoming RTC SDP offer for a camera.
- ``bticino_intercom/call/answer``: native WebRTC answer subscription (streams
  remote ICE candidates and terminate events back to the card, and terminates
  the device session on unsubscribe).
- ``bticino_intercom/call/candidate``: trickle local ICE candidates for a native
  answered call.
- ``bticino_intercom/webrtc_connected``: card notification that WebRTC media is
  connected, triggering a single ``player/unmute`` for answered incoming calls.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from webrtc_models import RTCIceCandidateInit

from .const import DOMAIN
from .history import IMAGE_KINDS, EventHistoryStore
from .media_source import BticinoHistoryImageView

if TYPE_CHECKING:
    from homeassistant.components.websocket_api.connection import ActiveConnection

    from .camera import BticinoWebRTCCamera

_LOGGER = logging.getLogger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
SIGNED_URL_LIFETIME = timedelta(hours=1)
WEBSOCKET_API_FLAG = f"{DOMAIN}_websocket_api_registered"


def _find_camera(hass: HomeAssistant, entity_id: str) -> BticinoWebRTCCamera | None:
    """Locate a registered BticinoWebRTCCamera by entity_id."""
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict):
            continue
        cameras = entry_data.get("cameras")
        if isinstance(cameras, dict) and entity_id in cameras:
            return cameras[entity_id]
    return None


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register the WebSocket commands, once per Home Assistant instance."""
    if hass.data.get(WEBSOCKET_API_FLAG):
        return
    websocket_api.async_register_command(hass, ws_list_history)
    websocket_api.async_register_command(hass, ws_call_offer)
    websocket_api.async_register_command(hass, ws_call_answer)
    websocket_api.async_register_command(hass, ws_call_candidate)
    websocket_api.async_register_command(hass, ws_webrtc_connected)
    hass.data[WEBSOCKET_API_FLAG] = True


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history",
        vol.Optional("entry_id"): str,
        vol.Optional("module_id"): str,
        vol.Optional("limit", default=DEFAULT_LIMIT): vol.All(int, vol.Range(min=1, max=MAX_LIMIT)),
    }
)
@callback
def ws_list_history(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send the recorded calls, most recent first."""
    limit: int = msg["limit"]
    wanted_entry: str | None = msg.get("entry_id")
    module_id: str | None = msg.get("module_id")

    events: list[dict[str, Any]] = []
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_data, dict):
            continue
        if wanted_entry and entry_id != wanted_entry:
            continue
        store: EventHistoryStore | None = entry_data.get("history")
        if store is None:
            continue
        events.extend(
            _as_payload(hass, connection, entry_id, event)
            for event in store.list_events(limit=limit, module_id=module_id)
        )

    events.sort(key=lambda event: event.get("timestamp") or 0, reverse=True)
    connection.send_result(msg["id"], {"events": events[:limit]})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/call/offer",
        vol.Required("entity_id"): str,
    }
)
@callback
def ws_call_offer(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the active incoming call SDP offer for entity_id, if any."""
    camera = _find_camera(hass, msg["entity_id"])
    if camera is None:
        connection.send_result(msg["id"], {"has_offer": False})
        return
    offer = camera.get_incoming_offer()
    if offer is None:
        connection.send_result(msg["id"], {"has_offer": False})
        return
    connection.send_result(msg["id"], {"has_offer": True, **offer})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/call/answer",
        vol.Required("entity_id"): str,
        vol.Required("session_id"): str,
        vol.Required("answer"): str,
    }
)
@websocket_api.async_response
async def ws_call_answer(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Answer an active incoming call natively and stream remote candidates."""
    camera = _find_camera(hass, msg["entity_id"])
    if camera is None:
        connection.send_error(msg["id"], "not_found", f"Camera {msg['entity_id']} not found")
        return

    msg_id: int = msg["id"]
    call_session: str = msg["session_id"]
    answer_sdp: str = msg["answer"]

    closed = False

    @callback
    def _unsubscribe() -> None:
        nonlocal closed
        closed = True
        camera.close_webrtc_session(call_session)

    connection.subscriptions[msg_id] = _unsubscribe
    connection.send_result(msg_id)

    @callback
    def _send_event(payload: dict[str, Any]) -> None:
        if not closed:
            connection.send_message(websocket_api.event_message(msg_id, payload))

    await camera.async_answer_incoming_call(call_session, answer_sdp, _send_event)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/call/candidate",
        vol.Required("entity_id"): str,
        vol.Required("session_id"): str,
        vol.Required("candidate"): dict,
    }
)
@websocket_api.async_response
async def ws_call_candidate(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Forward a local browser ICE candidate for an active native answered call."""
    camera = _find_camera(hass, msg["entity_id"])
    if camera is None:
        connection.send_error(msg["id"], "not_found", f"Camera {msg['entity_id']} not found")
        return

    cand = msg["candidate"]
    init = RTCIceCandidateInit(
        candidate=cand.get("candidate", ""),
        sdp_m_line_index=cand.get("sdpMLineIndex", cand.get("sdp_m_line_index")),
        sdp_mid=cand.get("sdpMid", cand.get("sdp_mid")),
    )
    await camera.async_on_webrtc_candidate(msg["session_id"], init)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/webrtc_connected",
        vol.Required("entity_id"): str,
        vol.Required("session_id"): str,
    }
)
@websocket_api.async_response
async def ws_webrtc_connected(
    hass: HomeAssistant,
    connection: ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Trigger a one-shot player/unmute once media is connected for an answered call."""
    camera = _find_camera(hass, msg["entity_id"])
    if camera is None:
        connection.send_result(msg["id"], {"unmuted": False})
        return
    unmuted = await camera.async_handle_webrtc_connected(msg["session_id"])
    connection.send_result(msg["id"], {"unmuted": unmuted})


def _as_payload(
    hass: HomeAssistant,
    connection: ActiveConnection,
    entry_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Turn a stored record into what the card needs."""
    event_id = event.get("event_id")
    payload: dict[str, Any] = {
        "entry_id": entry_id,
        "event_id": event_id,
        "module_id": event.get("module_id"),
        "module_name": event.get("module_name"),
        "timestamp": event.get("timestamp"),
        "ended_at": event.get("ended_at"),
        "event_type": event.get("event_type"),
        # True once the call was picked up elsewhere, False once it ended
        # unanswered, None while it is still open
        "answered": event.get("answered"),
    }
    for kind in IMAGE_KINDS:
        if not event_id or not event.get(f"{kind}_file"):
            continue
        payload[kind] = _signed(hass, connection, BticinoHistoryImageView.build_url(entry_id, event_id, kind))
    return payload


def _signed(hass: HomeAssistant, connection: ActiveConnection, path: str) -> str:
    """Return a path the browser can fetch without an auth header."""
    try:
        from homeassistant.components.http.auth import async_sign_path

        return async_sign_path(
            hass,
            path,
            SIGNED_URL_LIFETIME,
            refresh_token_id=connection.refresh_token_id,
        )
    except Exception:  # the image is simply not shown if signing is unavailable
        _LOGGER.debug("Could not sign %s", path, exc_info=True)
        return path
