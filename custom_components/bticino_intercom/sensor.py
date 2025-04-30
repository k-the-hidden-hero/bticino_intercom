"""Platform for sensor integration."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
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

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        # Find the bridge module for the sensor
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") == "BNC1":  # Bridge module type
                _LOGGER.debug("Found bridge module for sensor: %s", module_id)
                entities.append(BticinoLastEventSensor(coordinator, module_id))
                break

    # Add last call sensor
    entities.append(BticinoLastCallSensor(coordinator))

    if not entities:
        _LOGGER.warning("No BTicino bridge module found for sensor")

    async_add_entities(entities)


class BticinoLastEventSensor(
    CoordinatorEntity[BticinoIntercomCoordinator], SensorEntity
):
    """Representation of a BTicino Last Event Sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_unique_id = f"{DOMAIN}_{module_id}_last_event"
        self._attr_name = (
            "Last Event"  # Simplified name as it will be shown under the device
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Update the state properties based on coordinator data."""
        if not self.coordinator.data:
            self._attr_available = False
            return

        # Get last event from coordinator data
        last_event = self.coordinator.data.get("last_event")
        if not last_event:
            _LOGGER.debug("Last event data is missing or stale, checking history.")
            # Try to get the most recent event from history
            if "events" in self.coordinator.data and self.coordinator.data["events"]:
                last_event = self.coordinator.data["events"][0]
                _LOGGER.debug("Using event from history: %s", last_event)
            else:
                self._attr_available = False
                return

        # Update availability
        self._attr_available = True

        # Update state and attributes
        self._attr_native_value = last_event.get("type")
        self._attr_extra_state_attributes = {
            "timestamp": last_event.get("timestamp"),
            "timestamp_iso": datetime.fromtimestamp(
                last_event.get("timestamp", 0), tz=timezone.utc
            ).isoformat(),
            "event_module_id": last_event.get("module_id"),
            "event_module_name": last_event.get("module_name"),
            "raw_event_details": last_event.get("raw_event"),
        }

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, dynamically."""
        state = self.native_value
        if state == "incoming_call":
            return "mdi:phone-incoming"
        elif state == "accepted_call":
            return "mdi:phone-cancel"
        elif state == "missed_call":
            return "mdi:phone-hangup"
        return "mdi:history"  # Default icon for other/unknown states


class BticinoLastCallSensor(CoordinatorEntity, SensorEntity):
    """Representation of a BTicino Intercom last call sensor."""

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_device_class = "timestamp"
        self._attr_extra_state_attributes = {}
        self._attr_icon = "mdi:phone-clock"
        self._attr_name = (
            "Last Call"  # Simplified name as it will be shown under the device
        )
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_call"
        self._attr_should_poll = True

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
        )

    @property
    def native_value(self) -> Optional[str]:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None

        events = self.coordinator.data.get("events", [])
        if not events:
            return None

        # Get the most recent call event
        for event in events:
            if event.get("type") == "call" and event.get("end"):
                return event.get("end")

        return None

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        return self._attr_extra_state_attributes

    def _update_state(self) -> None:
        """Update the state of the sensor."""
        if not self.coordinator.data:
            return

        events = self.coordinator.data.get("events", [])
        if not events:
            return

        # Get the most recent call event
        for event in events:
            if event.get("type") == "call" and event.get("end"):
                # Update extra state attributes
                self._attr_extra_state_attributes = {
                    "call_id": event.get("id"),
                    "call_start": event.get("start"),
                    "call_duration": event.get("duration"),
                    "call_type": event.get("call_type"),
                    "call_status": event.get("status"),
                }
                break
