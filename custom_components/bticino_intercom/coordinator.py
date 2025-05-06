"""Data update coordinator for the BTicino Intercom integration."""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional
from datetime import timedelta, datetime, UTC
import copy
import re

from pybticino import AsyncAccount, WebsocketClient, AuthHandler
from pybticino.exceptions import PyBticinoException, ApiError, AuthError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_USERNAME,
    CONF_EMAIL,
    CONF_PASSWORD,
)
from homeassistant.core import HomeAssistant, callback

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    SIGNAL_CALL_RECEIVED,
    EVENT_TYPE_INCOMING_CALL,
    EVENT_TYPE_ANSWERED_ELSEWHERE,
    EVENT_TYPE_TERMINATED,
    EVENT_LOGBOOK_INCOMING_CALL,
    EVENT_LOGBOOK_ANSWERED_ELSEWHERE,
    EVENT_LOGBOOK_TERMINATED,
    DATA_LAST_EVENT,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class BticinoIntercomCoordinator(DataUpdateCoordinator):
    """Coordinator to handle BTicino intercom data and WebSocket."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        account: AsyncAccount,
        websocket_client: WebsocketClient,
    ) -> None:
        """Initialize the coordinator."""
        self.account = account
        self.websocket_client = websocket_client

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} Coordinator - {entry.entry_id}",
            update_interval=timedelta(minutes=UPDATE_INTERVAL),
            update_method=self._async_update_data,
        )

        self.entry = entry
        self.data: dict[str, Any] = {
            "homes": {},
            "modules": {},
            DATA_LAST_EVENT: {},
            "events_history": {},
        }
        self.home_id: str = entry.data["home_id"]
        self.session = async_get_clientsession(hass)
        self._home_name = None
        self._normalized_home_name = None
        self._main_device_id = None

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
                raise UpdateFailed(
                    f"Selected home_id {self.home_id} not found in account topology."
                )

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
                    _LOGGER.debug(f"Found bridge module with MAC ID: {module_obj.id}")
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
                raise UpdateFailed(
                    "No bridge module found in the system (MAC address ID check failed)"
                )

            # Store the bridge module ID as our main device ID
            self._main_device_id = bridge_module.id

            # Fetch Status Data
            try:
                status_data = await self.account.async_get_home_status(self.home_id)
                modules_status = (
                    status_data.get("body", {}).get("home", {}).get("modules", [])
                )
            except (ApiError, AuthError) as err:
                _LOGGER.warning(
                    "Failed to fetch status for home %s: %s", self.home_id, err
                )
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
                model="Classe 100X16E",
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
                events_history_data[self.home_id] = (
                    events_data.get("body", {}).get("home", {}).get("events", [])
                )
            except (ApiError, AuthError) as err:
                _LOGGER.warning(
                    "Failed to fetch events for home %s: %s", self.home_id, err
                )
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

            return final_data

        except AuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except ApiError as err:
            raise UpdateFailed(f"API error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error during data fetch")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    def _process_websocket_event(self, message: dict[str, Any]) -> bool:
        """Process event data from websocket and update self.data."""
        updated = False
        extra_params = message.get("extra_params", {})
        session_data = extra_params.get("data", {}).get("session_description", {})

        # Extract module/device ID - prioritize specific module from call, fallback to device_id
        module_id = session_data.get("module_id") or extra_params.get("device_id")

        if not module_id:
            _LOGGER.debug(
                "Websocket event without relevant module/device ID: %s", message
            )
            return False

        # Check if module exists in coordinator data (use self.data)
        if module_id not in self.data.get("modules", {}):
            _LOGGER.warning(
                "Websocket event for unknown module %s: %s", module_id, message
            )
            # Optionally try the device_id if module_id wasn't the primary one found
            if module_id != extra_params.get("device_id") and extra_params.get(
                "device_id"
            ) in self.data.get("modules", {}):
                module_id = extra_params.get("device_id")
                _LOGGER.debug("Falling back to device_id %s", module_id)
            else:
                return False  # Skip if neither ID is known

        current_module_data = self.data["modules"][module_id]  # Use self.data
        module_updated = False

        # --- Handle RTC Events (Call, Rescind, Terminate) based on session_data type ---
        rtc_event_type = session_data.get("type")
        if rtc_event_type in ["call", "rescind", "terminate"]:
            calling_module_id = session_data.get(
                "module_id", module_id
            )  # Module initiating/involved
            calling_module_name = (
                self.data.get("modules", {})
                .get(calling_module_id, {})
                .get("name", calling_module_id)
            )

            new_event_type = None
            log_message = None

            if rtc_event_type == "call":
                new_event_type = EVENT_TYPE_INCOMING_CALL
                log_message = (
                    f"Incoming call detected via RTC for module {calling_module_id}"
                )
                # Dispatch signal for binary sensor
                async_dispatcher_send(
                    self.hass, SIGNAL_CALL_RECEIVED, True, calling_module_id
                )
                # Fire Logbook event
                self.hass.bus.async_fire(
                    EVENT_LOGBOOK_INCOMING_CALL,
                    {
                        "name": f"Incoming Call ({calling_module_name})",
                        "module_id": calling_module_id,
                    },
                )
            elif rtc_event_type == "rescind":
                new_event_type = EVENT_TYPE_ANSWERED_ELSEWHERE
                log_message = f"Call answered elsewhere for module {calling_module_id}"
                # Dispatch signal to turn off binary sensor
                async_dispatcher_send(
                    self.hass, SIGNAL_CALL_RECEIVED, False, calling_module_id
                )
                # Fire Logbook event
                self.hass.bus.async_fire(
                    EVENT_LOGBOOK_ANSWERED_ELSEWHERE,
                    {
                        "name": f"Call Answered Elsewhere ({calling_module_name})",
                        "module_id": calling_module_id,
                    },
                )
            elif rtc_event_type == "terminate":
                new_event_type = EVENT_TYPE_TERMINATED
                log_message = f"Call terminated/hung up for module {calling_module_id}"
                # Dispatch signal to turn off binary sensor
                async_dispatcher_send(
                    self.hass, SIGNAL_CALL_RECEIVED, False, calling_module_id
                )
                # Fire Logbook event
                self.hass.bus.async_fire(
                    EVENT_LOGBOOK_TERMINATED,
                    {
                        "name": f"Call Terminated ({calling_module_name})",
                        "module_id": calling_module_id,
                    },
                )

            if new_event_type:
                _LOGGER.info(log_message)

                # Extract subevents from the original message if available
                # session_data was already extracted: extra_params.get("data", {}).get("session_description", {})
                subevents_data = session_data.get("subevents")

                # Update last event data
                self.data[DATA_LAST_EVENT] = {
                    "type": new_event_type,
                    "timestamp": datetime.now(UTC), # This is the time HA processed the event
                    "time": session_data.get("time"), # Add original event time if available
                    "module_id": calling_module_id,
                    "module_name": calling_module_name,
                    "subevents": subevents_data,  # Include extracted subevents
                    "raw_event": message, # Keep raw_event for deeper debugging if needed
                    # Potentially copy other relevant fields from session_data directly
                    "session_id": session_data.get("session_id"),
                    "video_status": session_data.get("video_status"), # Example
                }
                _LOGGER.debug("Updated last event data (full structure): %s", self.data[DATA_LAST_EVENT])
                updated = True

        # --- Handle other specific push_types if necessary ---
        # elif push_type == "some_other_type":
        #    ... handle specific state changes ...
        #    updated = True

        # --- Generic State Update (Only if no specific handler updated data) ---
        # This is less likely needed now but kept for potential future use
        # Avoid overwriting data handled by specific handlers above
        if not updated and isinstance(extra_params.get("data"), dict):
            possible_state_data = extra_params["data"]
            # Check only if it's NOT an RTC event we already handled
            if not (rtc_event_type in ["call", "rescind", "terminate"]):
                for key, value in possible_state_data.items():
                    # Avoid overwriting complex structures or already handled types
                    if (
                        key not in ["session_description", "modules", "type"]
                        and isinstance(value, (str, int, float, bool))
                        and current_module_data.get(key) != value
                    ):
                        _LOGGER.debug(
                            "Updating module %s key '%s' from %s to %s via websocket (generic)",
                            module_id,
                            key,
                            current_module_data.get(key),
                            value,
                        )
                    current_module_data[key] = value
                    module_updated = True

        if module_updated:
            updated = True

        return updated

    async def _handle_websocket_message(self, message: dict[str, Any]) -> None:
        """Handle incoming WebSocket messages."""
        _LOGGER.debug("Coordinator: _handle_websocket_message called with: %s", message)
        # Directly call _process_websocket_event with the received message
        data_updated = self._process_websocket_event(message)

        # No longer need the complex logic checking event_list or push_type here,
        # as _process_websocket_event now handles the specific RTC call structure.
        # We might need to re-add checks if other push types need special handling.

        # Removed the logic that fetches events again on PUSH_TYPE_WEBSOCKET_CONNECTION

        if data_updated:
            # Notify listeners immediately with the data updated by the event
            _LOGGER.debug(
                "WebSocket message processed, notifying listeners immediately."
            )
            self.async_set_updated_data(self.data)

            # Also request a full refresh to ensure full consistency later
            _LOGGER.debug("Requesting coordinator refresh after WebSocket update.")
            await self.async_request_refresh()

    # Removed async_start_websocket and async_stop_websocket methods

    async def _create_account(self) -> Optional[Any]:
        """Create a new account instance."""
        try:
            account = AsyncAccount(
                self.entry.data[CONF_EMAIL],
                self.entry.data[CONF_PASSWORD],
                session=self.session,
            )
            await account.async_authenticate()
            return account
        except Exception as err:
            _LOGGER.error("Error creating account: %s", err)
            return None
