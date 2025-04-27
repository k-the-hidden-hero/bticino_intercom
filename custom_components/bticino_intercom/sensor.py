"""Sensor platform for BTicino Intercom integration."""

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util  # For timezone handling if needed

from .const import (
    DOMAIN,
    DATA_LAST_EVENT,
    EVENT_TYPE_INCOMING_CALL,
    EVENT_TYPE_ANSWERED_ELSEWHERE,
    EVENT_TYPE_TERMINATED,
    BRIDGE_TYPES,
)
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino sensor platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    # Find the main bridge module to attach the sensor to.
    bridge_module_id = None
    bridge_module_data = None
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") in BRIDGE_TYPES and not module_data.get(
                "bridge"
            ):
                bridge_module_id = module_id
                bridge_module_data = module_data
                _LOGGER.debug("Found bridge module for sensor: %s", bridge_module_id)
                break  # Use the first one found

    if not bridge_module_id:
        _LOGGER.warning(
            "Could not identify a bridge module. "
            "Last Event sensor will not be linked to a specific device."
        )
        # Optionally, could still create the sensor but without device info,
        # or link it to the config entry as before as a fallback.
        # For now, we'll only create it if we find the bridge.
        return

    sensors_to_add = [BticinoLastEventSensor(coordinator, bridge_module_id)]
    async_add_entities(sensors_to_add)


class BticinoLastEventSensor(
    CoordinatorEntity[BticinoIntercomCoordinator], SensorEntity
):
    """Representation of the Last Event sensor."""

    _attr_has_entity_name = (
        True  # Use automatic naming based on device + translation_key
    )
    _attr_translation_key = (
        "last_event"  # Corresponds to strings.json key for sensor name
    )

    def __init__(
        self, coordinator: BticinoIntercomCoordinator, bridge_module_id: str
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        # Link to the bridge device
        self._attr_device_info = {
            "identifiers": {(DOMAIN, bridge_module_id)},
            # Name etc. will be inherited from the device registry entry for the bridge
        }
        # Set unique ID based on bridge_id and sensor type
        self._attr_unique_id = f"{bridge_module_id}_last_event"
        # Initialize state from coordinator data immediately
        self._update_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Update the sensor's state and attributes from coordinator data."""
        last_event_data = self.coordinator.data.get(DATA_LAST_EVENT, {})
        # Try to get the latest event from DATA_LAST_EVENT (updated by websocket)
        last_event_data = self.coordinator.data.get(DATA_LAST_EVENT, {})

        # If DATA_LAST_EVENT is empty or old, check events_history (updated by polling)
        # Define "old" threshold, e.g., 10 minutes
        event_is_stale = True
        if last_event_data.get("timestamp"):
            # Make sure timestamp is datetime object
            ts = last_event_data["timestamp"]
            if isinstance(ts, datetime):
                # Check if timestamp is recent enough (e.g., within last 10 minutes)
                if (dt_util.utcnow() - ts).total_seconds() < 600:
                    event_is_stale = False
            else:
                _LOGGER.warning(
                    "Invalid timestamp type in DATA_LAST_EVENT: %s", type(ts)
                )

        if not last_event_data or event_is_stale:
            _LOGGER.debug("Last event data is missing or stale, checking history.")
            history = self.coordinator.data.get("events_history", {}).get(
                self.coordinator.home_id, []
            )
            # Find the most recent relevant event from history (e.g., call related)
            relevant_event_types = {
                "incoming_call",
                "accepted_call",
                "missed_call",
                "outdoor",
            }  # Add more if needed
            latest_relevant_event = None
            for event in sorted(history, key=lambda x: x.get("time", 0), reverse=True):
                event_type = event.get("type")
                sub_event_type = None
                if event_type == "outdoor" and "subevents" in event:
                    # Check subevents for call types
                    for sub in event.get("subevents", []):
                        if sub.get("type") in relevant_event_types:
                            sub_event_type = sub.get("type")
                            break  # Found relevant subevent

                if event_type in relevant_event_types or sub_event_type:
                    latest_relevant_event = event
                    # Use sub_event_type if found, otherwise main event type
                    last_event_data = {
                        "type": sub_event_type or event_type,
                        "timestamp": dt_util.utc_from_timestamp(event.get("time", 0)),
                        "module_id": event.get("module_id"),
                        "module_name": self.coordinator.data.get("modules", {})
                        .get(event.get("module_id"), {})
                        .get("name"),
                        "raw_event": event,  # Store raw event for potential attributes
                    }
                    _LOGGER.debug("Using event from history: %s", last_event_data)
                    break  # Stop searching once the latest relevant event is found
            # If no relevant event found in history either, set state to None or "unknown"
            if not last_event_data:
                _LOGGER.debug("No relevant event found in history or recent data.")
                last_event_data = {"type": None}  # Ensure last_event_data is a dict

        # --- Update state and attributes based on the determined last_event_data ---
        self._attr_native_value = last_event_data.get("type")

        attributes = {}
        timestamp = last_event_data.get("timestamp")
        if isinstance(timestamp, datetime):
            attributes["timestamp"] = dt_util.as_timestamp(timestamp)
            attributes["timestamp_iso"] = timestamp.isoformat()
        else:
            attributes["timestamp"] = None
            attributes["timestamp_iso"] = None

        attributes["event_module_id"] = last_event_data.get("module_id")
        attributes["event_module_name"] = last_event_data.get("module_name")
        # Optionally add more details from raw_event if needed
        attributes["raw_event_details"] = last_event_data.get("raw_event")

        self._attr_extra_state_attributes = attributes

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, dynamically."""
        state = self.native_value
        if state == EVENT_TYPE_INCOMING_CALL:
            return "mdi:phone-incoming"
        elif state == EVENT_TYPE_ANSWERED_ELSEWHERE:
            return "mdi:phone-cancel"
        elif state == EVENT_TYPE_TERMINATED:
            return "mdi:phone-hangup"
        # Add other icons based on event types if needed
        return "mdi:history"  # Default icon for other/unknown states
