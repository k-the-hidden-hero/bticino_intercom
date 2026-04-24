"""Data update coordinator for the BTicino Intercom integration."""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pybticino import AsyncAccount, SignalingClient, WebsocketClient
from pybticino.exceptions import ApiError, AuthError

from .const import (
    CALL_RETRANSMIT_WINDOW,
    CALL_SESSION_MAX_DURATION,
    DATA_LAST_EVENT,
    DEFAULT_NAME,
    DOMAIN,
    EVENT_CALL,
    EVENT_LOGBOOK_ACCEPTED_CALL,
    EVENT_LOGBOOK_ANSWERED_ELSEWHERE,
    EVENT_LOGBOOK_INCOMING_CALL,
    EVENT_LOGBOOK_MISSED_CALL,
    EVENT_LOGBOOK_TERMINATED,
    EVENT_TYPE_ACCEPTED_CALL,
    EVENT_TYPE_ANSWERED_ELSEWHERE,
    EVENT_TYPE_INCOMING_CALL,
    EVENT_TYPE_MISSED_CALL,
    EVENT_TYPE_TERMINATED,
    SIGNAL_CALL_RECEIVED,
    UPDATE_INTERVAL,
)
from .history import EventHistoryStore

# WebSocket is considered stale if no message received for this many seconds.
# The server sends periodic pings (every 20s) that count as messages in the
# websockets library, so silence beyond this threshold means the connection
# is likely dead but not yet detected by TCP keepalive.
WS_STALE_THRESHOLD = 600  # 10 minutes

_LOGGER = logging.getLogger(__name__)


class BticinoIntercomCoordinator(DataUpdateCoordinator):
    """Coordinator to handle BTicino intercom data and WebSocket."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        account: AsyncAccount,
        websocket_client: WebsocketClient,
        signaling_client: SignalingClient | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.account = account
        self.websocket_client = websocket_client
        self._signaling_client = signaling_client

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} Coordinator - {entry.entry_id}",
            update_interval=timedelta(minutes=UPDATE_INTERVAL),
        )

        self.entry = entry
        self.data: dict[str, Any] = {
            "homes": {},
            "modules": {},
            DATA_LAST_EVENT: {},
            "events_history": {},
        }
        self.home_id: str = entry.data["home_id"]
        self._home_name = None
        self._normalized_home_name = None
        self._main_device_id = None
        self._last_ws_message_time: datetime | None = None
        self._ws_stale = False
        # Per-module active call sessions for retransmission dedup.
        # {"started": datetime, "last_seen": datetime,
        #  "watchdog": asyncio.Task | None,
        #  "session_id": str | None}
        self._active_calls: dict[str, dict[str, Any]] = {}
        # Current active call for WebRTC signaling (offer/answer SDP exchange)
        self._active_call: dict[str, Any] | None = None
        # Populated by __init__.py after entry setup.
        self.history: EventHistoryStore | None = None

    @property
    def main_device_id(self) -> str | None:
        """Return the main bridge device ID."""
        return self._main_device_id

    @property
    def home_name(self) -> str:
        """Return the home name."""
        return self._home_name or "unknown"

    @property
    def normalized_home_name(self) -> str:
        """Return the normalized home name."""
        if not self._normalized_home_name and self._home_name:
            self._normalized_home_name = self._home_name.lower().replace(" ", "_")
        return self._normalized_home_name or "unknown"

    @property
    def active_call(self) -> dict[str, Any] | None:
        """Return the active call state, or None if no call is in progress."""
        return self._active_call

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API, register devices, and return the combined data."""
        _LOGGER.debug("Coordinator: Starting data update")
        homes_data = {}
        modules_data = {}

        try:
            await self.account.async_update_topology()
            if not self.account.homes:
                _LOGGER.warning("No homes found for this account.")
                return {
                    "homes": {},
                    "modules": {},
                    "events_history": {},
                    DATA_LAST_EVENT: {},
                }
            if self.home_id not in self.account.homes:
                raise UpdateFailed(f"Selected home_id {self.home_id} not found in account topology.")

            selected_home_obj = self.account.homes[self.home_id]
            homes_data[self.home_id] = selected_home_obj.raw_data

            # Find the main bridge module by checking if the ID is a MAC address
            bridge_module = None
            mac_address_pattern = re.compile(r"^([0-9A-Fa-f]{2}:){5}([0-9A-Fa-f]{2})$")
            # LOOP 1: Populate modules_data based on topology AND find the bridge
            for module_obj in selected_home_obj.modules:
                modules_data[module_obj.id] = module_obj.raw_data
                # Check if the module ID matches the MAC address pattern and we haven't found the bridge yet
                if not bridge_module and mac_address_pattern.match(module_obj.id):
                    bridge_module = module_obj
                    _LOGGER.debug("Found bridge module with MAC ID: %s", module_obj.id)
                    # Do NOT break, continue populating modules_data

            if not bridge_module:
                # Log the available module types and IDs for debugging
                available_modules_info = [
                    f"ID: {m.id}, Type: {m.raw_data.get('type', 'N/A')}, Variant: {m.raw_data.get('variant', 'N/A')}"
                    for m in selected_home_obj.modules
                ]
                _LOGGER.error(
                    "No bridge module found (expected ID formatted as MAC address). Available modules: %s",
                    available_modules_info,
                )
                raise UpdateFailed("No bridge module found in the system (MAC address ID check failed)")

            # Store the bridge module ID as our main device ID
            self._main_device_id = bridge_module.id

            # Fetch Status Data
            try:
                status_data = await self.account.async_get_home_status(self.home_id)
                modules_status = status_data.get("body", {}).get("home", {}).get("modules", [])
            except (ApiError, AuthError) as err:
                _LOGGER.warning("Failed to fetch status for home %s: %s", self.home_id, err)
                modules_status = []

            # Update modules with status data
            for module_status_update in modules_status:
                module_id = module_status_update.get("id")
                if module_id and module_id in modules_data:
                    for key, value in module_status_update.items():
                        if key != "id":
                            modules_data[module_id][key] = value

            # Register/Update the main device in the registry
            device_registry = dr.async_get(self.hass)
            device_registry.async_get_or_create(
                config_entry_id=self.entry.entry_id,
                identifiers={(DOMAIN, self._main_device_id)},
                manufacturer="BTicino",
                model=bridge_module.raw_data.get("type", DEFAULT_NAME),
                name=f"BTicino Intercom - {self.home_name}",
                sw_version=str(
                    bridge_module.raw_data.get("firmware_name")
                    or bridge_module.raw_data.get("firmware_revision", "Unknown")
                ),
            )

            # Fetch Events
            events_history_data = {}
            try:
                events_data = await self.account.async_get_events(self.home_id, size=20)
                events_history_data[self.home_id] = events_data.get("body", {}).get("home", {}).get("events", [])
            except (ApiError, AuthError) as err:
                _LOGGER.warning("Failed to fetch events for home %s: %s", self.home_id, err)
                events_history_data[self.home_id] = []

            final_data = {
                "homes": homes_data,
                "modules": modules_data,
                "events_history": events_history_data,
                DATA_LAST_EVENT: self.data.get(DATA_LAST_EVENT, {}),
            }

            if "name" in final_data["homes"][self.home_id]:
                self._home_name = final_data["homes"][self.home_id]["name"]
                _LOGGER.debug("Home name set to: %s", self._home_name)

            # Check WebSocket health: if we haven't received a message in a while,
            # flag the connection as stale so the manager can force a reconnect.
            if self._last_ws_message_time:
                silence = (datetime.now(UTC) - self._last_ws_message_time).total_seconds()
                if silence > WS_STALE_THRESHOLD:
                    _LOGGER.warning(
                        "WebSocket has been silent for %ds (threshold %ds), flagging as stale",
                        int(silence),
                        WS_STALE_THRESHOLD,
                    )
                    self._ws_stale = True

            return final_data

        except AuthError as err:
            # Auth errors are critical — must re-authenticate
            raise UpdateFailed(f"Authentication error: {err}") from err
        except (ApiError, TimeoutError, OSError, ConnectionError) as err:
            # Transient errors: return last known data instead of marking
            # all entities unavailable. Matches mobile app behavior.
            if self.data and self.data.get("modules"):
                _LOGGER.warning(
                    "Transient error during update (%s: %s), keeping last known data.",
                    type(err).__name__,
                    err,
                )
                return self.data
            raise UpdateFailed(f"API error (no previous data): {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error during data fetch")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    def _fire_call_event(
        self,
        event_type: str,
        module_id: str | None = None,
        *,
        reason: str | None = None,
        snapshot_url: str | None = None,
        vignette_url: str | None = None,
    ) -> None:
        """Fire a bticino_intercom_call event for frontend/automations."""
        session_id = self._active_call.get("session_id") if self._active_call else None
        module_name = None
        camera_entity_id = None

        if module_id:
            modules = self.data.get("modules", {}) if self.data else {}
            module_name = modules.get(module_id, {}).get("name", module_id)
            ent_reg = er.async_get(self.hass)
            for entry in er.async_entries_for_config_entry(ent_reg, self.entry.entry_id):
                if entry.domain == "camera" and entry.unique_id and module_id in entry.unique_id:
                    camera_entity_id = entry.entity_id
                    break

        data: dict[str, Any] = {
            "type": event_type,
            "entry_id": self.entry.entry_id,
            "module_id": module_id,
            "module_name": module_name,
            "session_id": session_id,
        }
        if event_type == "ring":
            data["snapshot_url"] = snapshot_url
            data["vignette_url"] = vignette_url
            data["camera_entity_id"] = camera_entity_id
        if reason:
            data["reason"] = reason

        self.hass.bus.async_fire(EVENT_CALL, data)

    async def _process_websocket_event(self, message: dict[str, Any]) -> bool:
        """Process event data from websocket and update self.data.

        Handles two event formats:
        - Format A (RTC): push_type=BNC1-rtc, action type in extra_params.data.type
          (offer/rescind/terminate), SDP in extra_params.data.session_description
        - Format B (Status): push_type=BNC1-incoming_call/missed_call/accepted_call,
          event info in extra_params directly (snapshot_url, vignette_url, etc.)
        """
        extra_params = message.get("extra_params", {})
        push_type = message.get("push_type", "")

        # --- Format A: RTC events (BNC1-rtc) ---
        rtc_data = extra_params.get("data", {})
        rtc_action = rtc_data.get("type")  # "offer", "rescind", "terminate"

        if rtc_action in ("offer", "rescind", "terminate"):
            return await self._process_rtc_event(message, extra_params, rtc_data, rtc_action)

        # --- Format B: Status events (BNC1-incoming_call, etc.) ---
        event_type = extra_params.get("event_type")
        if event_type in ("incoming_call", "missed_call", "accepted_call"):
            return await self._process_status_event(message, extra_params, event_type)

        # --- Unknown event ---
        _LOGGER.debug("Unhandled websocket event (push_type=%s): %s", push_type, message)
        return False

    async def _process_rtc_event(
        self,
        message: dict[str, Any],
        extra_params: dict[str, Any],
        rtc_data: dict[str, Any],
        rtc_action: str,
    ) -> bool:
        """Process an RTC event (offer/rescind/terminate).

        Note: only "offer" events contain session_description with module_id.
        For "terminate" and "rescind", session_description is absent — we recover
        the calling module from the active_call state set during the offer.
        """
        session_desc = rtc_data.get("session_description", {})
        device_id = extra_params.get("device_id")
        session_id = extra_params.get("session_id")

        # For offer: module_id comes from session_description
        # For terminate/rescind: recover from active_call state
        calling_module_id = session_desc.get("module_id")
        if not calling_module_id and self._active_call:
            calling_module_id = self._active_call.get("module_id")

        # Use calling_module_id for display, fall back to device_id (bridge MAC)
        display_id = calling_module_id or device_id
        if not display_id:
            _LOGGER.debug("RTC event without module/device ID: %s", message)
            return False

        display_name = self.data.get("modules", {}).get(display_id, {}).get("name", display_id)

        if rtc_action == "offer":
            _LOGGER.info("Incoming call (RTC offer) from %s (%s)", display_name, display_id)

            # Store active call state for WebRTC signaling
            self._active_call = {
                "session_id": session_id,
                "tag_id": extra_params.get("tag_id"),
                "correlation_id": extra_params.get("correlation_id"),
                "device_id": device_id,
                "module_id": calling_module_id,
                "sdp": session_desc.get("sdp"),
                "modules": session_desc.get("modules", []),
            }
            _LOGGER.debug("Active call state stored: session_id=%s, module=%s", session_id, calling_module_id)

            # Prepare the signaling client for answer-mode WebRTC
            if self._signaling_client:
                self._signaling_client.set_session_from_push(
                    session_id=session_id,
                    tag_id=extra_params.get("tag_id", ""),
                    correlation_id=str(extra_params.get("correlation_id", "")),
                    device_id=device_id,
                )
                _LOGGER.debug("Signaling session set from push offer: session_id=%s", session_id)

            # Call session tracking for retransmission dedup
            now = datetime.now(UTC)
            dedup_id = calling_module_id or display_id
            session = self._active_calls.get(dedup_id)

            if session is not None:
                # Retransmission of an ongoing call: only refresh timestamp
                session["last_seen"] = now
                _LOGGER.debug("Ignoring retransmitted offer for module %s (session active)", dedup_id)
                return False

            # New call: open session with watchdog
            watchdog = self.hass.async_create_background_task(
                self._call_session_watchdog(dedup_id),
                name=f"{DOMAIN} call watchdog - {dedup_id}",
            )
            self._active_calls[dedup_id] = {
                "started": now,
                "last_seen": now,
                "watchdog": watchdog,
            }

            # Record the call in history (without images for now).
            # If an incoming_call push arrives later with snapshots,
            # async_record_call will update the same record in-place.
            if self.history is not None and (calling_module_id or device_id):
                record_module = calling_module_id or device_id
                now_ts = int(now.timestamp())
                event_id = session_id or f"{now_ts}-{record_module}"
                await self.history.async_record_call(
                    event_id=event_id,
                    module_id=record_module,
                    module_name=display_name,
                    snapshot_url=None,
                    vignette_url=None,
                    event_timestamp=now_ts,
                )

            # Dispatch signal for binary sensor
            if calling_module_id:
                async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, True, calling_module_id)

            self._fire_call_event("ring", calling_module_id)

            self.hass.bus.async_fire(
                EVENT_LOGBOOK_INCOMING_CALL,
                {"name": f"Incoming Call ({display_name})", "module_id": display_id},
            )
            self._update_last_event(EVENT_TYPE_INCOMING_CALL, display_id, display_name, message, extra_params)
            return True

        if rtc_action == "rescind":
            _LOGGER.info("Call answered elsewhere (RTC rescind) for %s", display_name)

            closing_session_id = (
                session_id
                or session_desc.get("session_id")
                or self._active_calls.get(calling_module_id or display_id, {}).get("session_id")
            )
            dedup_id = calling_module_id or display_id
            self._end_call_session(dedup_id, reason="rescind")
            if calling_module_id:
                async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, False, calling_module_id)
            self._fire_call_event("end", calling_module_id, reason="rescind")
            self._active_call = None

            self.hass.bus.async_fire(
                EVENT_LOGBOOK_ANSWERED_ELSEWHERE,
                {"name": f"Call Answered Elsewhere ({display_name})", "module_id": display_id},
            )
            self._update_last_event(EVENT_TYPE_ANSWERED_ELSEWHERE, display_id, display_name, message, extra_params)
            await self._close_history_event(closing_session_id, EVENT_TYPE_ANSWERED_ELSEWHERE)
            return True

        if rtc_action == "terminate":
            _LOGGER.info("Call terminated (RTC terminate) for %s", display_name)

            closing_session_id = (
                session_id
                or session_desc.get("session_id")
                or self._active_calls.get(calling_module_id or display_id, {}).get("session_id")
            )
            dedup_id = calling_module_id or display_id
            self._end_call_session(dedup_id, reason="terminate")
            if calling_module_id:
                async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, False, calling_module_id)
            self._fire_call_event("end", calling_module_id, reason="terminate")
            self._active_call = None

            self.hass.bus.async_fire(
                EVENT_LOGBOOK_TERMINATED,
                {"name": f"Call Terminated ({display_name})", "module_id": display_id},
            )
            self._update_last_event(EVENT_TYPE_TERMINATED, display_id, display_name, message, extra_params)
            await self._close_history_event(closing_session_id, EVENT_TYPE_TERMINATED)
            return True

        return False

    async def _process_status_event(
        self,
        message: dict[str, Any],
        extra_params: dict[str, Any],
        event_type: str,
    ) -> bool:
        """Process a status event (incoming_call, missed_call, accepted_call)."""
        device_id = extra_params.get("device_id")
        device_name = self.data.get("modules", {}).get(device_id, {}).get("name", device_id)

        if event_type == "incoming_call":
            _LOGGER.info("Incoming call status event (with snapshot) for %s", device_name)

            snapshot_url = extra_params.get("snapshot_url")
            vignette_url = extra_params.get("vignette_url")
            timestamp = extra_params.get("timestamp")
            now_ts = int(datetime.now(UTC).timestamp())
            # Use the same event_id logic as the RTC offer handler so the
            # incoming_call push updates the record created by the offer.
            calling_module = self._active_call.get("module_id") if self._active_call else None
            record_module = calling_module or device_id
            event_id = extra_params.get("session_id") or f"{now_ts}-{record_module}"

            # Download images to local storage BEFORE firing events,
            # so automations/notifications have images available immediately.
            if self.history is not None and device_id:
                await self.history.async_record_call(
                    event_id=event_id,
                    module_id=device_id,
                    module_name=device_name,
                    snapshot_url=snapshot_url,
                    vignette_url=vignette_url,
                    event_timestamp=now_ts,
                )

            last_event = self.data.get(DATA_LAST_EVENT, {})
            if last_event and last_event.get("type") == EVENT_TYPE_INCOMING_CALL:
                last_event["snapshot_url"] = snapshot_url
                last_event["vignette_url"] = vignette_url
                _LOGGER.info("Enriched existing call event with snapshot/vignette from incoming_call push")
            else:
                self.data[DATA_LAST_EVENT] = {
                    "type": EVENT_TYPE_INCOMING_CALL,
                    "timestamp": datetime.now(UTC),
                    "time": timestamp,
                    "module_id": device_id,
                    "module_name": device_name,
                    "session_id": extra_params.get("session_id"),
                    "event_id": event_id,
                    "snapshot_url": snapshot_url,
                    "vignette_url": vignette_url,
                }

            calling_module_id = self._active_call.get("module_id") if self._active_call else None
            if calling_module_id:
                self._fire_call_event(
                    "ring",
                    calling_module_id,
                    snapshot_url=snapshot_url,
                    vignette_url=vignette_url,
                )

            self.hass.bus.async_fire(
                EVENT_LOGBOOK_INCOMING_CALL,
                {"name": f"Incoming Call ({device_name})", "module_id": device_id},
            )

            return True

        if event_type == "missed_call":
            _LOGGER.info("Missed call for %s", device_name)

            # Turn off binary sensor — use the calling module from active call if available,
            # since device_id here is the bridge MAC, not the external unit module_id
            calling_module = self._active_call.get("module_id") if self._active_call else None
            if calling_module:
                async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, False, calling_module)
            self._active_call = None

            self.data[DATA_LAST_EVENT] = {
                "type": EVENT_TYPE_MISSED_CALL,
                "timestamp": datetime.now(UTC),
                "time": extra_params.get("timestamp"),
                "module_id": device_id,
                "module_name": device_name,
                "session_id": extra_params.get("session_id"),
                "event_id": extra_params.get("event_id"),
            }

            self.hass.bus.async_fire(
                EVENT_LOGBOOK_MISSED_CALL,
                {"name": f"Missed Call ({device_name})", "module_id": device_id},
            )
            return True

        if event_type == "accepted_call":
            _LOGGER.info("Call accepted for %s", device_name)

            # Don't clear active_call here — accepted means someone answered,
            # the WebRTC session may still be active on another device

            self.data[DATA_LAST_EVENT] = {
                "type": EVENT_TYPE_ACCEPTED_CALL,
                "timestamp": datetime.now(UTC),
                "time": extra_params.get("timestamp"),
                "module_id": device_id,
                "module_name": device_name,
                "session_id": extra_params.get("session_id"),
                "event_id": extra_params.get("event_id"),
            }

            self.hass.bus.async_fire(
                EVENT_LOGBOOK_ACCEPTED_CALL,
                {"name": f"Call Accepted ({device_name})", "module_id": device_id},
            )
            return True

        return False

    def _update_last_event(
        self,
        event_type: str,
        module_id: str,
        module_name: str,
        message: dict[str, Any],
        extra_params: dict[str, Any],
    ) -> None:
        """Update the last event data from an RTC event."""
        session_data = extra_params.get("data", {}).get("session_description", {})

        # Only use subevents from this event — never reuse stale subevents
        # from a previous poll, as their Azure SAS URLs will have expired.
        subevents_data = session_data.get("subevents")

        self.data[DATA_LAST_EVENT] = {
            "type": event_type,
            "timestamp": datetime.now(UTC),
            "time": session_data.get("time"),
            "module_id": module_id,
            "module_name": module_name,
            "session_id": extra_params.get("session_id"),
            "subevents": subevents_data,
        }

    async def _close_history_event(self, event_id: str | None, event_type: str) -> None:
        """Close the history record for the given session id."""
        if self.history is None or not event_id:
            return
        await self.history.async_close_call(event_id=event_id, event_type=event_type)

    def _end_call_session(self, module_id: str, reason: str) -> None:
        """Close an active call session and cancel its watchdog."""
        session = self._active_calls.pop(module_id, None)
        if session is None:
            return
        watchdog: asyncio.Task | None = session.get("watchdog")
        if watchdog is not None and not watchdog.done():
            watchdog.cancel()
        _LOGGER.debug("Call session for module %s closed (reason=%s)", module_id, reason)

    def fire_call_timeout(self, module_id: str) -> None:
        """Fire an end event when the call times out."""
        self._fire_call_event("end", module_id, reason="timeout")
        self._active_call = None

    async def _call_session_watchdog(self, module_id: str) -> None:
        """Fallback watchdog: closes the session if rescind/terminate
        never arrives, or if 'call' retransmissions stop."""
        try:
            while True:
                await asyncio.sleep(CALL_RETRANSMIT_WINDOW)
                session = self._active_calls.get(module_id)
                if session is None:
                    return
                now = datetime.now(UTC)
                idle = (now - session["last_seen"]).total_seconds()
                total = (now - session["started"]).total_seconds()

                if idle >= CALL_RETRANSMIT_WINDOW:
                    _LOGGER.info(
                        "Call session for module %s closed by inactivity (%.1fs)",
                        module_id,
                        idle,
                    )
                    async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, False, module_id)
                    self._end_call_session(module_id, reason="inactivity")
                    return
                if total >= CALL_SESSION_MAX_DURATION:
                    _LOGGER.warning(
                        "Call session for module %s closed by hard timeout (%.1fs)",
                        module_id,
                        total,
                    )
                    async_dispatcher_send(self.hass, SIGNAL_CALL_RECEIVED, False, module_id)
                    self._end_call_session(module_id, reason="hard_timeout")
                    return
        except asyncio.CancelledError:
            raise

    @property
    def ws_stale(self) -> bool:
        """Return True if the WebSocket connection appears stale."""
        return self._ws_stale

    async def _handle_websocket_message(self, message: dict[str, Any]) -> None:
        """Handle incoming WebSocket messages."""
        self._last_ws_message_time = datetime.now(UTC)
        self._ws_stale = False
        _LOGGER.debug("Coordinator: _handle_websocket_message called with: %s", message)
        data_updated = await self._process_websocket_event(message)

        # No longer need the complex logic checking event_list or push_type here,
        # as _process_websocket_event now handles the specific RTC call structure.
        # We might need to re-add checks if other push types need special handling.

        # Removed the logic that fetches events again on PUSH_TYPE_WEBSOCKET_CONNECTION

        if data_updated:
            # Notify listeners immediately with the data updated by the event
            _LOGGER.debug("WebSocket message processed, notifying listeners immediately.")
            self.async_set_updated_data(self.data)

            # Also request a full refresh to ensure full consistency later
            _LOGGER.debug("Requesting coordinator refresh after WebSocket update.")
            await self.async_request_refresh()
