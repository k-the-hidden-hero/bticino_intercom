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
            events = self.coordinator.data.get("events_history", {}).get(
                self.coordinator.home_id, []
            )
            if events:
                last_event = events[0]
                _LOGGER.debug("Using event from history: %s", last_event)
            else:
                self._attr_available = False
                return

        # Update availability
        self._attr_available = True

        # Update state and attributes
        self._attr_native_value = last_event.get("type")
        timestamp = last_event.get("timestamp")
        if isinstance(timestamp, datetime):
            timestamp_iso = timestamp.isoformat()
        # Handle case where timestamp might be from history (int) or websocket (datetime)
        elif isinstance(timestamp, (int, float)):
            timestamp_iso = datetime.fromtimestamp(
                timestamp or 0, tz=timezone.utc
            ).isoformat()
        else:
            timestamp_iso = None  # Or handle other potential types
            timestamp = None

        # Extract snapshot/vignette from the first subevent if available
        snapshot_url = None
        snapshot_expires_at = None
        vignette_url = None
        vignette_expires_at = None
        subevents = last_event.get("subevents")
        if subevents and isinstance(subevents, list) and len(subevents) > 0:
            first_subevent = subevents[0]
            if isinstance(first_subevent, dict):
                snapshot_data = first_subevent.get("snapshot")
                if isinstance(snapshot_data, dict):
                    snapshot_url = snapshot_data.get("url")
                    snapshot_expires_at = snapshot_data.get("expires_at")
                vignette_data = first_subevent.get("vignette")
                if isinstance(vignette_data, dict):
                    vignette_url = vignette_data.get("url")
                    vignette_expires_at = vignette_data.get("expires_at")

        # Store attributes, filtering out None values
        attrs = {
            "timestamp": timestamp,
            "timestamp_iso": timestamp_iso,
            "event_module_id": last_event.get("module_id"),
            "event_module_name": last_event.get(
                "module_name"
            ),  # Likely not present in history events
            "snapshot_url": snapshot_url,
            "snapshot_expires_at": snapshot_expires_at,
            "vignette_url": vignette_url,
            "vignette_expires_at": vignette_expires_at,
            "raw_event_details": last_event.get(
                "raw_event"
            ),  # Only present if from websocket
        }
        self._attr_extra_state_attributes = {
            k: v for k, v in attrs.items() if v is not None
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

        events = self.coordinator.data.get("events_history", {}).get(
            self.coordinator.home_id, []
        )
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

        events = self.coordinator.data.get("events_history", {}).get(
            self.coordinator.home_id, []
        )
        if not events:
            return

        # Get the most recent call event
        latest_completed_call = None
        for event in events:
            if event.get("type") == "call" and event.get("end"):
                latest_completed_call = event
                break  # Found the most recent completed call

        if latest_completed_call:
            # Extract snapshot/vignette from the first subevent if available
            snapshot_url = None
            snapshot_expires_at = None
            vignette_url = None
            vignette_expires_at = None
            subevents = latest_completed_call.get("subevents")
            if subevents and isinstance(subevents, list) and len(subevents) > 0:
                first_subevent = subevents[0]  # Assuming first subevent is relevant
                if isinstance(first_subevent, dict):
                    snapshot_data = first_subevent.get("snapshot")
                    if isinstance(snapshot_data, dict):
                        snapshot_url = snapshot_data.get("url")
                        snapshot_expires_at = snapshot_data.get("expires_at")
                    vignette_data = first_subevent.get("vignette")
                    if isinstance(vignette_data, dict):
                        vignette_url = vignette_data.get("url")
                        vignette_expires_at = vignette_data.get("expires_at")

            # Update extra state attributes, filtering out None values
            attrs = {
                "call_id": latest_completed_call.get("id"),
                "call_start": latest_completed_call.get("start"),
                "call_duration": latest_completed_call.get("duration"),
                "call_type": latest_completed_call.get("call_type"),
                "call_status": latest_completed_call.get("status"),
                "snapshot_url": snapshot_url,
                "snapshot_expires_at": snapshot_expires_at,
                "vignette_url": vignette_url,
                "vignette_expires_at": vignette_expires_at,
            }
            self._attr_extra_state_attributes = {
                k: v for k, v in attrs.items() if v is not None
            }
        else:
            # Clear attributes if no completed call found
            self._attr_extra_state_attributes = {}
