"""Data update coordinator for the BTicino Intercom integration."""

import asyncio
import logging
from typing import Any, Callable
from datetime import timedelta
import copy  # Import deepcopy

from pybticino import AsyncAccount, WebsocketClient, AuthHandler
from pybticino.exceptions import PyBticinoException, ApiError, AuthError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME  # Needed for title fallback
from homeassistant.core import HomeAssistant, callback

# Import device registry helper
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

# Import dispatcher for signaling
from homeassistant.helpers.dispatcher import async_dispatcher_send

# Import constants including the signal and event/push types
from .const import (
    DOMAIN,
    SIGNAL_CALL_RECEIVED,
    EVENT_TYPE_INCOMING_CALL,
    PUSH_TYPE_WEBSOCKET_CONNECTION,
)

_LOGGER = logging.getLogger(__name__)


# Inherit from DataUpdateCoordinator
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
        self._websocket_task: asyncio.Task | None = None

        # Call super().__init__
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} Coordinator - {entry.entry_id}",
            update_interval=timedelta(hours=1),  # Example: Poll hourly as fallback
            update_method=self._async_update_data,
        )
        # Data is initialized by super().__init__ calling update_method
        self.entry = entry
        # Ensure self.data is initialized as a dictionary by the parent class
        self.data: dict[str, Any] = {"homes": {}, "modules": {}}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API, register devices, and return the combined data."""
        _LOGGER.debug("Coordinator: Starting data update")
        homes_data = {}
        modules_data = {}

        try:
            # 1. Fetch Topology
            await self.account.async_update_topology()
            if not self.account.homes:
                _LOGGER.warning("No homes found for this account.")
                return {"homes": {}, "modules": {}}

            # Store topology data temporarily
            for home_id, home_obj in self.account.homes.items():
                homes_data[home_id] = home_obj.raw_data
                for module_obj in home_obj.modules:
                    modules_data[module_obj.id] = module_obj.raw_data

            # 2. Fetch Status Data for all homes
            for home_id in self.account.homes:
                _LOGGER.debug("Fetching status for home %s", home_id)
                status_data = await self.account.async_get_home_status(home_id)
                modules_status = (
                    status_data.get("body", {}).get("home", {}).get("modules", [])
                )
                for module_status_update in modules_status:
                    module_id = module_status_update.get("id")
                    if module_id and module_id in modules_data:
                        # Merge status into existing module data from topology
                        for key, value in module_status_update.items():
                            if key != "id":  # Don't overwrite id
                                modules_data[module_id][key] = value
                    elif module_id:
                        _LOGGER.warning(
                            "Module %s found in status but not in topology.", module_id
                        )
                        # Optionally add it if desired, but might indicate inconsistency
                        # modules_data[module_id] = module_status_update

            # 3. Register/Update Devices in Registry using the merged data
            device_registry = dr.async_get(self.hass)
            processed_module_ids = set(
                modules_data.keys()
            )  # All modules found in topology/status

            for module_id, module_data in modules_data.items():
                # Construct device_info using the fully merged module_data
                device_info = {
                    "identifiers": {(DOMAIN, module_id)},
                    "manufacturer": "BTicino",
                    "model": module_data.get("type"),
                    "name": module_data.get("name"),
                    "sw_version": str(
                        module_data.get("firmware_name")
                        or module_data.get("firmware_revision")
                    ),
                    "hw_version": (
                        str(module_data.get("hardware_version"))
                        if module_data.get("hardware_version")
                        else None
                    ),
                }
                bridge_id = module_data.get("bridge")
                if bridge_id:
                    # Ensure the bridge device exists before setting via_device
                    # This assumes bridge was processed in the loop already or exists from previous update
                    if bridge_id in modules_data:
                        device_info["via_device"] = (DOMAIN, bridge_id)
                    else:
                        _LOGGER.warning(
                            "Bridge device %s not found for module %s",
                            bridge_id,
                            module_id,
                        )

                _LOGGER.debug(
                    "Registering/Updating device %s with info: %s",
                    module_id,
                    device_info,
                )
                device_registry.async_get_or_create(
                    config_entry_id=self.entry.entry_id, **device_info
                )

            # 4. Prepare final data structure to return (and store in self.data)
            final_data = {"homes": homes_data, "modules": modules_data}
            _LOGGER.debug("Data update finished. Final data: %s", final_data)
            return final_data

        except AuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except ApiError as err:
            raise UpdateFailed(f"API error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error during data fetch")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    # _process_status_update is now integrated into _async_update_data

    def _process_websocket_event(self, event_data: dict[str, Any]) -> bool:
        """Process event data from websocket and update self.data['modules']."""
        updated = False
        event_type = event_data.get("event_type") or event_data.get("push_type")
        module_id = event_data.get("module_id") or event_data.get("device_id")

        if not module_id:
            _LOGGER.debug("Websocket event without module/device ID: %s", event_data)
            return False

        # Use self.data directly as it's managed by DataUpdateCoordinator
        if not self.data or "modules" not in self.data:
            _LOGGER.warning("Coordinator data not ready for websocket event.")
            return False

        if module_id not in self.data["modules"]:
            _LOGGER.warning(
                "Websocket event for unknown module %s: %s", module_id, event_data
            )
            return False

        current_module_data = self.data["modules"][module_id]
        module_updated = False

        # --- Handle Incoming Call ---
        if event_type == "outdoor" and "subevents" in event_data:
            for subevent in event_data.get("subevents", []):
                if subevent.get("type") == EVENT_TYPE_INCOMING_CALL:
                    _LOGGER.info("Incoming call detected for module %s", module_id)
                    async_dispatcher_send(
                        self.hass, SIGNAL_CALL_RECEIVED, True, module_id
                    )
                    updated = True  # Mark that an event occurred
                    break

        # --- Handle Module State Changes ---
        possible_state_data = event_data.get("data", event_data)
        if isinstance(possible_state_data, dict):
            for key, value in possible_state_data.items():
                if key != "id" and current_module_data.get(key) != value:
                    _LOGGER.debug(
                        "Updating module %s key '%s' from %s to %s via websocket",
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
        _LOGGER.debug("Received WebSocket message: %s", message)
        data_updated = False
        # Process based on message structure
        if "event_list" in message:
            for event in message["event_list"]:
                if self._process_websocket_event(event):
                    data_updated = True
        elif "push_type" in message:
            if self._process_websocket_event(message):
                data_updated = True
            # Fetch events only if specifically needed by a push type (like connection trigger)
            if message.get("push_type") == PUSH_TYPE_WEBSOCKET_CONNECTION:
                _LOGGER.info(
                    "Websocket connection trigger received, fetching recent events..."
                )
                try:
                    # Find home_id associated with the device_id
                    device_id = message.get("extra_params", {}).get("device_id")
                    home_id = None
                    # Find the home_id associated with this device_id
                    for hid, home_data in self.data.get("homes", {}).items():
                        # Check if device_id is in the modules list for this home
                        if (
                            device_id in self.data.get("modules", {})
                            and self.data["modules"][device_id].get("home_id") == hid
                        ):
                            home_id = hid
                            break
                        # Fallback check in raw module list if home_id not stored in module data
                        elif any(
                            m.get("id") == device_id
                            for m in home_data.get("modules", [])
                        ):
                            home_id = hid
                            break
                    # Last resort fallback if not found
                    if not home_id and self.data.get("homes"):
                        home_id = next(iter(self.data["homes"]))

                    if home_id:
                        events_data = await self.account.async_get_events(
                            home_id=home_id, size=5
                        )
                        if (
                            events_data
                            and "body" in events_data
                            and "home" in events_data["body"]
                        ):
                            for event in events_data["body"]["home"].get("events", []):
                                # Process events, potentially updating data
                                if self._process_websocket_event(event):
                                    data_updated = True
                    else:
                        _LOGGER.warning(
                            "Could not determine home_id for device %s to fetch events",
                            device_id,
                        )
                except Exception:
                    _LOGGER.exception("Error fetching events after websocket trigger")
        else:  # Fallback for other message structures
            if self._process_websocket_event(message):
                data_updated = True

        if data_updated:
            # Notify listeners via DataUpdateCoordinator
            _LOGGER.debug("Notifying listeners of updated data.")
            self.async_set_updated_data(self.data)

    async def async_start_websocket(self) -> None:
        """Start the WebSocket listener task."""
        if self._websocket_task and not self._websocket_task.done():
            _LOGGER.debug("WebSocket listener task already running")
            return

        if not self.websocket_client:
            _LOGGER.error("Websocket client not initialized in coordinator")
            return

        _LOGGER.debug("Starting WebSocket listener task")
        self._websocket_task = self.hass.async_create_task(
            self.websocket_client.run_forever(),
            name=f"{DOMAIN} WebSocket Listener - {self.entry.entry_id}",
        )

    async def async_stop_websocket(self) -> None:
        """Stop the WebSocket listener task."""
        if self._websocket_task and not self._websocket_task.done():
            _LOGGER.debug("Cancelling WebSocket listener task")
            self._websocket_task.cancel()
            try:
                await self._websocket_task
            except asyncio.CancelledError:
                _LOGGER.debug("WebSocket listener task cancelled successfully")
            except Exception:
                _LOGGER.exception("Error waiting for WebSocket task cancellation")
            self._websocket_task = None

        if self.websocket_client:
            await self.websocket_client.disconnect()
            _LOGGER.debug("WebSocket client disconnected")
