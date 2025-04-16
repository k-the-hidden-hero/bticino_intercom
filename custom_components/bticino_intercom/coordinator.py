"""Data update coordinator for the BTicino Intercom integration."""

import asyncio
import logging
from typing import Any, Callable
from datetime import timedelta

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
        # It will be populated by _async_update_data
        self.data: dict[str, Any] = {"homes": {}, "modules": {}}

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the API and register devices."""
        _LOGGER.debug("Coordinator: Starting data update")
        # Start with the previous data if available, otherwise empty dicts
        # Make a deep copy to avoid modifying the previous state directly during processing
        current_data = {
            "homes": {k: v.copy() for k, v in self.data.get("homes", {}).items()},
            "modules": {k: v.copy() for k, v in self.data.get("modules", {}).items()},
        }

        try:
            await self.account.async_update_topology()
            if not self.account.homes:
                _LOGGER.warning("No homes found for this account.")
                return {"homes": {}, "modules": {}}  # Return empty if no homes

            device_registry = dr.async_get(self.hass)
            processed_modules = {}  # Keep track of modules processed from topology
            for home_id, home_obj in self.account.homes.items():
                # Update home data (usually static info)
                current_data["homes"][home_id] = home_obj.raw_data
                for module_obj in home_obj.modules:
                    module_data = module_obj.raw_data
                    module_id = module_obj.id
                    # Update existing module data or add new module from topology
                    # Preserve existing status keys if not present in topology data
                    existing_module_data = current_data["modules"].get(module_id, {})
                    # Important: Update existing dict, don't overwrite with just topology data
                    existing_module_data.update(module_data)
                    current_data["modules"][module_id] = existing_module_data
                    processed_modules[module_id] = current_data["modules"][module_id]

                    # Register ALL devices, setting via_device for non-bridge modules
                    device_info = {
                        "identifiers": {(DOMAIN, module_id)},
                        "manufacturer": "BTicino",
                        "model": module_data.get("type"),
                        "name": module_data.get("name"),
                        # Use firmware_name if available, fallback to firmware_revision
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
                        device_info["via_device"] = (DOMAIN, bridge_id)

                    device_registry.async_get_or_create(
                        config_entry_id=self.entry.entry_id, **device_info
                    )

            # Remove modules from current_data that are no longer in the topology
            modules_to_remove = set(current_data["modules"]) - set(processed_modules)
            for module_id in modules_to_remove:
                _LOGGER.debug("Removing module %s no longer in topology", module_id)
                current_data["modules"].pop(module_id, None)

            # Fetch status after registering devices and processing topology
            for home_id in self.account.homes:
                _LOGGER.debug("Fetching status for home %s", home_id)
                status_data = await self.account.async_get_home_status(home_id)
                # Process status update directly into current_data["modules"]
                self._process_status_update(status_data, current_data["modules"])

            _LOGGER.debug("Data update finished. New data: %s", current_data)
            # Return the updated data structure which will become self.data
            return current_data

        except AuthError as err:
            raise UpdateFailed(f"Authentication error: {err}") from err
        except ApiError as err:
            raise UpdateFailed(f"API error: {err}") from err
        except Exception as err:
            _LOGGER.exception("Unexpected error during data fetch")
            raise UpdateFailed(f"Unexpected error: {err}") from err

    def _process_status_update(
        self, status_data: dict[str, Any], modules_target: dict[str, Any]
    ) -> bool:
        """Process data from homestatus endpoint and update the modules_target dict."""
        _LOGGER.debug("Processing status update data: %s", status_data)
        updated = False
        modules_status = status_data.get("body", {}).get("home", {}).get("modules", [])
        if not modules_status:
            return False

        for module_status_update in modules_status:
            module_id = module_status_update.get("id")
            if not module_id:
                continue

            if module_id in modules_target:
                current_module_data = modules_target[module_id]
                module_updated = False
                # Only update keys present in the status update
                for key, value in module_status_update.items():
                    if key != "id" and current_module_data.get(key) != value:
                        _LOGGER.debug(
                            "Updating module %s key '%s' from %s to %s via status",
                            module_id,
                            key,
                            current_module_data.get(key),
                            value,
                        )
                        current_module_data[key] = value
                        module_updated = True
                if module_updated:
                    updated = True  # Mark overall update if any module changed
            else:
                _LOGGER.warning(
                    "Module %s found in status but not in topology.", module_id
                )
        return updated

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
                    # No direct data update needed, signal is enough
                    updated = True  # Mark that an event occurred
                    break

        # --- Handle Module State Changes ---
        # Check for common state keys directly in the event or in a 'data' sub-dict
        possible_state_data = event_data.get("data", event_data)
        if isinstance(possible_state_data, dict):
            for key, value in possible_state_data.items():
                # Update only if the key exists in the event and differs from current state
                # or if the key is relevant and not yet present (e.g., 'lock')
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
            # Mark that data relevant for entities might have changed
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
                    if device_id and self.data.get("modules", {}).get(device_id):
                        module_home_id = self.data["modules"][device_id].get("home_id")
                        if module_home_id:
                            home_id = module_home_id
                        elif self.data.get("homes"):
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
