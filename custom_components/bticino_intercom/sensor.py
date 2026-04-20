"""Platform for sensor integration."""

import logging
from datetime import timedelta
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import utc_from_timestamp, utcnow

from .const import (
    DOMAIN,
    EVENT_TYPE_ANSWERED_ELSEWHERE,
    EVENT_TYPE_INCOMING_CALL,
    EVENT_TYPE_TERMINATED,
)
from .coordinator import BticinoIntercomCoordinator
from .utils import cleanup_orphaned_entities, format_timestamp_iso, format_uptime_readable

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino sensor platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Allow event sensors even if bridge ID is missing initially?
    # Let's require bridge ID for consistency now.
    if not coordinator.data or not coordinator.main_device_id:
        _LOGGER.warning("Coordinator data or bridge ID not available for sensor setup.")
        return  # Don't set up any sensors if bridge isn't there

    bridge_id = coordinator.main_device_id
    bridge_module_data = coordinator.data.get("modules", {}).get(bridge_id)

    entities = []

    # --- Event Sensors (Re-linked to bridge device) ---
    entities.append(BticinoEventSensor(coordinator))
    entities.append(BticinoLastCallTimestampSensor(coordinator))
    entities.append(BticinoCallCountSensor(coordinator))

    # --- Bridge Status Sensors (Keep as is) ---
    if bridge_module_data:
        _LOGGER.debug("Found bridge module data for sensor setup: %s", bridge_id)
        entities.append(BticinoBridgeUptimeSensor(coordinator, bridge_id, bridge_module_data))
        entities.append(BticinoBridgeWifiStrengthSensor(coordinator, bridge_id, bridge_module_data))
        entities.append(BticinoBridgeWebsocketStatusSensor(coordinator, bridge_id, bridge_module_data))
        entities.append(BticinoBridgeLocalIpSensor(coordinator, bridge_id, bridge_module_data))
    else:
        _LOGGER.warning(
            "Bridge module data not found for ID %s, skipping bridge-specific sensors.",
            bridge_id,
        )

    if not entities:
        _LOGGER.warning("No BTicino sensors could be set up.")
        return

    cleanup_orphaned_entities(hass, entry.entry_id, "sensor", entities)
    async_add_entities(entities)


class BticinoEventSensor(CoordinatorEntity[BticinoIntercomCoordinator], SensorEntity):
    """Representation of the Last BTicino Event Type."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_event_type"
        self._attr_name = "Last Event Type"
        self._update_state()

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info linking to the main bridge device."""
        if self.coordinator.main_device_id:
            # Reuse the logic from BridgeBaseSensor to ensure consistency
            device_name = (
                f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
            )
            bridge_module_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id)
            model = bridge_module_data.get("type") if bridge_module_data else None
            sw_version = bridge_module_data.get("firmware_name") if bridge_module_data else None

            return DeviceInfo(
                identifiers={(DOMAIN, self.coordinator.main_device_id)},
                name=device_name,
                manufacturer="BTicino",
                model=model,
                sw_version=sw_version,
            )
        return None

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

        last_event = self.coordinator.data.get("last_event")
        if not last_event:
            events = self.coordinator.data.get("events_history", {}).get(self.coordinator.home_id, [])
            if events:
                last_event = events[0]
                _LOGGER.debug("EventSensor: Using event from history: %s", last_event)
            else:
                self._attr_available = False
                self._attr_native_value = None
                self._attr_extra_state_attributes = {}
                return  # No event data available

        self._attr_available = True
        # State can be the main type or the first subevent type if more specific
        main_type = last_event.get("type")
        subevents = last_event.get("subevents")
        first_subevent = {}
        if subevents and isinstance(subevents, list) and len(subevents) > 0:
            first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}

        self._attr_native_value = first_subevent.get("type") or main_type  # Prioritize subevent type

        timestamp = (
            first_subevent.get("time") or last_event.get("time") or last_event.get("timestamp")
        )  # Try multiple keys

        # Extract details from the first subevent if available
        snapshot_url = None
        snapshot_expires_at_iso = None
        vignette_url = None
        vignette_expires_at_iso = None

        snapshot_data = first_subevent.get("snapshot")
        if isinstance(snapshot_data, dict):
            snapshot_url = snapshot_data.get("url")
            snapshot_expires_at_iso = format_timestamp_iso(snapshot_data.get("expires_at"))

        vignette_data = first_subevent.get("vignette")
        if isinstance(vignette_data, dict):
            vignette_url = vignette_data.get("url")
            vignette_expires_at_iso = format_timestamp_iso(vignette_data.get("expires_at"))

        attrs = {
            "timestamp_iso": format_timestamp_iso(timestamp),
            "event_id": last_event.get("id"),
            "event_module_id": last_event.get("module_id"),
            # "subevent_type": first_subevent.get("type"), # State is now this
            "message": first_subevent.get("message"),
            "session_id": first_subevent.get("session_id"),
            "video_status": last_event.get("video_status"),
            "snapshot_url": snapshot_url,
            "snapshot_expires_at_iso": snapshot_expires_at_iso,
            "vignette_url": vignette_url,
            "vignette_expires_at_iso": vignette_expires_at_iso,
        }
        self._attr_extra_state_attributes = {k: v for k, v in attrs.items() if v is not None}

    @property
    def icon(self) -> str | None:
        """Return the icon to use in the frontend, dynamically."""
        state = self.native_value
        # Use event types defined in coordinator or constants
        if state == "missed_call" or state == EVENT_TYPE_TERMINATED:  # Check defined const
            return "mdi:phone-missed"
        elif state == "accepted_call" or state == EVENT_TYPE_ANSWERED_ELSEWHERE:  # Check defined const
            return "mdi:phone-check"  # Or phone-log?
        elif state == "incoming_call" or state == EVENT_TYPE_INCOMING_CALL:  # Check defined const
            return "mdi:phone-incoming"
        elif state == "connection":
            return "mdi:lan-connect"
        elif state == "disconnection":
            return "mdi:lan-disconnect"
        elif state == "outdoor":  # Generic outdoor event?
            return "mdi:door"  # Or mdi:history?
        # Add more specific icons if needed
        return "mdi:history"  # Default icon


class BticinoLastCallTimestampSensor(CoordinatorEntity[BticinoIntercomCoordinator], SensorEntity):
    """Representation of the timestamp of the last completed BTicino call."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:phone-clock"

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_call_timestamp"
        self._attr_name = "Last Call Timestamp"
        self._last_call_event_data = None
        self._update_state()

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info linking to the main bridge device."""
        if self.coordinator.main_device_id:
            # Reuse the logic from BridgeBaseSensor to ensure consistency
            device_name = (
                f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
            )
            bridge_module_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id)
            model = bridge_module_data.get("type") if bridge_module_data else None
            sw_version = bridge_module_data.get("firmware_name") if bridge_module_data else None

            return DeviceInfo(
                identifiers={(DOMAIN, self.coordinator.main_device_id)},
                name=device_name,
                manufacturer="BTicino",
                model=model,
                sw_version=sw_version,
            )
        return None

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

        events = self.coordinator.data.get("events_history", {}).get(self.coordinator.home_id, [])
        if not events:
            self._attr_available = False  # No history to check
            self._attr_native_value = None
            self._last_call_event_data = None
            self._attr_extra_state_attributes = {}
            return

        latest_completed_call = None
        completion_timestamp = None
        for event in events:
            subevents = event.get("subevents")
            is_relevant_call_event = False
            potential_completion_time = None

            if subevents and isinstance(subevents, list) and len(subevents) > 0:
                first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}
                first_subevent_type = first_subevent.get("type")
                if first_subevent_type in [
                    "incoming_call",
                    "accepted_call",
                    "missed_call",
                ]:
                    is_relevant_call_event = True
                    # Prioritize subevent time for completion
                    potential_completion_time = first_subevent.get("time")

            # If it's a relevant call event, check if we found a completion time
            if is_relevant_call_event and potential_completion_time:
                latest_completed_call = event  # Store the whole event
                completion_timestamp = potential_completion_time
                break  # Found the most recent completed call

        if latest_completed_call and completion_timestamp:
            self._attr_available = True
            self._last_call_event_data = latest_completed_call  # Store for attributes

            # Set native_value as datetime object
            if isinstance(completion_timestamp, int | float) and completion_timestamp > 0:
                self._attr_native_value = utc_from_timestamp(completion_timestamp)
            else:
                self._attr_native_value = None

            # Update attributes
            self._update_attributes()

        else:
            # No completed call found in history, keep previous state? Or set unavailable?
            # Let's keep previous state but mark unavailable if coordinator failed later
            # If we never found one, it should be unavailable.
            if self._last_call_event_data is None:  # Only set unavailable if never found
                self._attr_available = False
                self._attr_native_value = None
                self._attr_extra_state_attributes = {}

    def _update_attributes(self) -> None:
        """Helper to update attributes from stored event data."""
        if not self._last_call_event_data:
            self._attr_extra_state_attributes = {}
            return

        event = self._last_call_event_data
        subevents = event.get("subevents")
        first_subevent = {}
        if subevents and isinstance(subevents, list) and len(subevents) > 0:
            first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}

        snapshot_url = None
        snapshot_expires_at_iso = None
        vignette_url = None
        vignette_expires_at_iso = None

        snapshot_data = first_subevent.get("snapshot")
        if isinstance(snapshot_data, dict):
            snapshot_url = snapshot_data.get("url")
            snapshot_expires_at_iso = format_timestamp_iso(snapshot_data.get("expires_at"))

        vignette_data = first_subevent.get("vignette")
        if isinstance(vignette_data, dict):
            vignette_url = vignette_data.get("url")
            vignette_expires_at_iso = format_timestamp_iso(vignette_data.get("expires_at"))

        attrs = {
            "call_event_id": event.get("id"),
            "call_event_time_iso": format_timestamp_iso(event.get("time")),
            "call_module_id": event.get("module_id"),
            "subevent_type": first_subevent.get("type"),
            "subevent_time_iso": format_timestamp_iso(first_subevent.get("time")),  # Same as native_value (ISO)
            "message": first_subevent.get("message"),
            "session_id": first_subevent.get("session_id"),
            "video_status": event.get("video_status"),
            "snapshot_url": snapshot_url,
            "snapshot_expires_at_iso": snapshot_expires_at_iso,
            "vignette_url": vignette_url,
            "vignette_expires_at_iso": vignette_expires_at_iso,
        }
        self._attr_extra_state_attributes = {k: v for k, v in attrs.items() if v is not None}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return getattr(self, "_attr_extra_state_attributes", {})


class BticinoCallCountSensor(CoordinatorEntity[BticinoIntercomCoordinator], SensorEntity):
    """Counts the number of calls in the current event history."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:phone-log"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the call count sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_call_count"
        self._attr_name = "Calls"
        self._update_state()

    @property
    def device_info(self) -> DeviceInfo | None:
        """Return device info linking to the main bridge device."""
        if not self.coordinator.main_device_id:
            return None
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        self.async_write_ha_state()

    def _update_state(self) -> None:
        """Count calls in event history."""
        if not self.coordinator.data:
            self._attr_available = False
            return

        self._attr_available = True
        events = self.coordinator.data.get("events_history", {}).get(self.coordinator.home_id, [])

        total = 0
        missed = 0
        answered = 0
        for event in events:
            subevents = event.get("subevents", [])
            if subevents and isinstance(subevents, list):
                first = subevents[0] if isinstance(subevents[0], dict) else {}
                stype = first.get("type", "")
                if stype in ("missed_call", "accepted_call", "incoming_call"):
                    total += 1
                    if stype == "missed_call":
                        missed += 1
                    elif stype == "accepted_call":
                        answered += 1

        self._attr_native_value = total
        self._attr_extra_state_attributes = {
            "answered": answered,
            "missed": missed,
        }


# --- New Bridge Status Sensors ---


class BticinoBridgeBaseSensor(CoordinatorEntity[BticinoIntercomCoordinator], SensorEntity):
    """Base class for sensors attached to the bridge device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        bridge_id: str,
        bridge_module_data: dict[str, Any],
    ) -> None:
        """Initialize the bridge sensor."""
        super().__init__(coordinator)
        self._bridge_id = bridge_id
        self._update_state_from_data(bridge_module_data or {})

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the main bridge device."""
        # All these sensors belong to the bridge device identified by bridge_id
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
        )
        # Extract bridge module data to potentially get model info
        # Note: We already have bridge_module_data passed in __init__, but reading fresh data might be safer?
        # Let's use the coordinator data directly here for consistency.
        bridge_module_data = self.coordinator.data.get("modules", {}).get(self._bridge_id)
        model = bridge_module_data.get("type") if bridge_module_data else None
        sw_version = (
            bridge_module_data.get("firmware_name") if bridge_module_data else None
        )  # Use firmware_name if available

        return DeviceInfo(
            identifiers={(DOMAIN, self._bridge_id)},
            name=device_name,  # Use dynamic name
            manufacturer="BTicino",
            model=model,  # Add model if available
            sw_version=sw_version,  # Add firmware version
            # Consider adding hw_version if available and useful
        )

    @property
    def available(self) -> bool:
        """Return True if coordinator updated and module data exists."""
        # Check coordinator update success first
        if not self.coordinator.last_update_success:
            return False
        # Check specific module data exists
        return self._bridge_id in self.coordinator.data.get("modules", {})

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        bridge_module_data = self.coordinator.data.get("modules", {}).get(self._bridge_id)
        if bridge_module_data:
            self._update_state_from_data(bridge_module_data)
            # Availability is handled by the property, just update state
            if self.hass:  # Check hass is available before writing state
                self.async_write_ha_state()
        # If bridge_module_data is None, the 'available' property will return False

    def _update_state_from_data(self, data: dict[str, Any]) -> None:
        """Update state and attributes from the provided module data."""
        # To be implemented by subclasses
        raise NotImplementedError()


class BticinoBridgeUptimeSensor(BticinoBridgeBaseSensor):
    """Representation of the Bridge Uptime as a timestamp (boot time)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-sand"

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        bridge_id: str,
        bridge_module_data: dict[str, Any],
    ) -> None:
        """Initialize the uptime sensor."""
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{bridge_id}_uptime"
        self._attr_name = "Bridge Last Boot"
        super().__init__(coordinator, bridge_id, bridge_module_data)

    def _update_state_from_data(self, data: dict[str, Any]) -> None:
        """Update state — convert uptime seconds to a boot timestamp."""
        uptime_sec = data.get("uptime")
        if isinstance(uptime_sec, int) and uptime_sec > 0:
            boot_time = utcnow() - timedelta(seconds=uptime_sec)
            self._attr_native_value = boot_time
        else:
            self._attr_native_value = None
        attrs = {"uptime_readable": format_uptime_readable(uptime_sec)}
        self._attr_extra_state_attributes = {k: v for k, v in attrs.items() if v is not None}


class BticinoBridgeWifiStrengthSensor(BticinoBridgeBaseSensor):
    """Representation of the Bridge WiFi Strength."""

    # Assuming value is RSSI in dBm, adjust if different
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        bridge_id: str,
        bridge_module_data: dict[str, Any],
    ) -> None:
        """Initialize the wifi strength sensor."""
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{bridge_id}_wifi_strength"
        self._attr_name = "Bridge WiFi Strength"
        super().__init__(coordinator, bridge_id, bridge_module_data)

    def _update_state_from_data(self, data: dict[str, Any]) -> None:
        """Update state."""
        wifi_strength = data.get("wifi_strength")
        if isinstance(wifi_strength, int | float):
            # RSSI should be negative. If it's positive, make it negative.
            self._attr_native_value = -abs(wifi_strength)
        else:
            self._attr_native_value = None
        self._attr_extra_state_attributes = {}  # No extra attributes needed


class BticinoBridgeWebsocketStatusSensor(BticinoBridgeBaseSensor):
    """Representation of the Bridge WebSocket Connection Status."""

    _attr_device_class = SensorDeviceClass.ENUM  # Or CONNECTIVITY? Enum seems better for True/False
    _attr_options: ClassVar[list[str]] = ["connected", "disconnected"]
    _attr_icon = "mdi:connection"

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        bridge_id: str,
        bridge_module_data: dict[str, Any],
    ) -> None:
        """Initialize the websocket status sensor."""
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{bridge_id}_websocket_status"
        self._attr_name = "Bridge WebSocket Status"
        super().__init__(coordinator, bridge_id, bridge_module_data)

    def _update_state_from_data(self, data: dict[str, Any]) -> None:
        """Update state."""
        is_connected = data.get("websocket_connected")
        if isinstance(is_connected, bool):
            self._attr_native_value = "connected" if is_connected else "disconnected"
        else:
            self._attr_native_value = None  # Or "unknown"?
        self._attr_extra_state_attributes = {}


class BticinoBridgeLocalIpSensor(BticinoBridgeBaseSensor):
    """Representation of the Bridge Local IP Address."""

    _attr_icon = "mdi:ip-network-outline"

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        bridge_id: str,
        bridge_module_data: dict[str, Any],
    ) -> None:
        """Initialize the IP address sensor."""
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{bridge_id}_local_ip"
        self._attr_name = "Bridge Local IP Address"
        super().__init__(coordinator, bridge_id, bridge_module_data)

    def _update_state_from_data(self, data: dict[str, Any]) -> None:
        """Update state."""
        self._attr_native_value = data.get("local_ipv4")
        self._attr_extra_state_attributes = {}
