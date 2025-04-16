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
)  # Import necessary constants
from .coordinator import BticinoIntercomCoordinator  # Import the coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino sensor platform."""
    # Get the coordinator from hass.data
    if not (entry_data := hass.data.get(DOMAIN, {}).get(entry.entry_id)) or not (
        coordinator := entry_data.get("coordinator")
    ):
        _LOGGER.error("Coordinator not found in hass.data for entry %s", entry.entry_id)
        return
    # coordinator type is already BticinoIntercomCoordinator due to assignment expression

    # Find the main intercom module to attach the sensor to.
    # Heuristic: Look for a module likely to be the main outdoor/indoor unit.
    # Adjust types based on your specific hardware (e.g., BNDL, BNCX, BNMH).
    main_module_id = None
    main_module_data = None
    module_types_to_check = ["BNC1", "BNMH", "BNDL", "BNCX"]  # Example types
    for module_id, module_data in coordinator.data.get("modules", {}).items():
        if module_data.get("type") in module_types_to_check and not module_data.get(
            "bridge"
        ):
            main_module_id = module_id
            main_module_data = module_data
            _LOGGER.debug("Found potential main module for sensor: %s", main_module_id)
            break  # Use the first one found

    if not main_module_id:
        _LOGGER.warning(
            "Could not identify a main intercom module to attach the sensor to. "
            "Sensor will be created without a device link."
        )

    sensors_to_add = [
        BticinoLastEventSensor(coordinator, main_module_id, main_module_data)
    ]
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
        self,
        coordinator: BticinoIntercomCoordinator,
        module_id: str | None,  # Allow None if no main module found
        module_data: dict | None,  # Allow None
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._module_id = module_id
        # Set unique ID based on entry_id and sensor type
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_event"

        # Set device info only if a main module was identified
        if module_id and module_data:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, module_id)},
                # Name, manufacturer, etc., are inherited from the device registry
            }
        else:
            # No specific device link if main module not found
            self._attr_device_info = None

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
        # State is the type of the last event (e.g., "incoming_call")
        self._attr_native_value = last_event_data.get("type")

        # Attributes store additional details
        attributes = {}
        timestamp = last_event_data.get("timestamp")
        if isinstance(timestamp, datetime):
            # Ensure timestamp is timezone-aware (it's UTC from coordinator)
            # Format as ISO string for attribute
            attributes["timestamp"] = dt_util.as_timestamp(
                timestamp
            )  # Store as timestamp
            attributes["timestamp_iso"] = timestamp.isoformat()  # Also store ISO format
        else:
            attributes["timestamp"] = None
            attributes["timestamp_iso"] = None

        # Add module info if available in the event data
        attributes["event_module_id"] = last_event_data.get("module_id")
        attributes["event_module_name"] = last_event_data.get("module_name")

        self._attr_extra_state_attributes = attributes

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, dynamically."""
        if self.native_value == EVENT_TYPE_INCOMING_CALL:
            return "mdi:phone-incoming"
        # Add icons for other event types here if needed in the future
        # Example: elif self.native_value == "door_unlocked": return "mdi:lock-open"
        return "mdi:history"  # Default icon if no specific event type matches
