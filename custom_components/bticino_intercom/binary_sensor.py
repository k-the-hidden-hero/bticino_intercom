"""Platform for binary sensor integration."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from contextlib import suppress  # Import suppress for async_will_remove_from_hass
from datetime import datetime, timezone, timedelta  # Added datetime etc

from pybticino import AsyncAccount

# Unused imports removed

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import utc_from_timestamp  # Added

# Import the actual coordinator and signal
from .coordinator import BticinoIntercomCoordinator

# Import CoordinatorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import (
    DOMAIN,
    SIGNAL_CALL_RECEIVED,
    SUBTYPE_EXTERNAL_UNIT,
    SUBTYPE_DOORLOCK,
    CALL_SENSOR_TIMEOUT,
)  # Remove unused consts
from .utils import format_timestamp_iso, format_uptime_readable

_LOGGER = logging.getLogger(__name__)

# How long the sensor stays 'on' after a call is detected (in seconds)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino binary sensor platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        _LOGGER.debug(
            f"Binary Sensor Setup: Found {len(coordinator.data['modules'])} modules in coordinator data."
        )
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant")
            subtype = None
            _LOGGER.debug(
                f"Binary Sensor Setup: Checking module {module_id}, Variant: {variant}"
            )
            if variant and ":" in variant:
                try:
                    subtype = variant.split(":", 1)[1]
                    _LOGGER.debug(f"Binary Sensor Setup: Extracted subtype: {subtype}")
                except IndexError:
                    _LOGGER.warning(
                        "Could not parse subtype from variant '%s' for module %s",
                        variant,
                        module_id,
                    )
                    subtype = None

            if subtype == SUBTYPE_EXTERNAL_UNIT:
                _LOGGER.debug(
                    "Found external unit module (via variant subtype): %s", module_id
                )
                entities.append(BticinoCallBinarySensor(coordinator, module_id))
            elif subtype:
                _LOGGER.debug(
                    f"Binary Sensor Setup: Module {module_id} subtype '{subtype}' did not match expected external unit subtype."
                )
            elif not subtype and variant is not None:
                _LOGGER.debug(
                    f"Binary Sensor Setup: Module {module_id} has variant '{variant}' but failed to extract subtype."
                )

    if not entities:
        _LOGGER.debug("No BTicino external unit modules found")

    async_add_entities(entities)


class BticinoCallBinarySensor(
    CoordinatorEntity[BticinoIntercomCoordinator], BinarySensorEntity
):
    """Representation of a BTicino call sensor."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY  # Or SOUND?
    _attr_icon = "mdi:doorbell-video"
    _attr_has_entity_name = True  # Added

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the call sensor."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_unique_id = f"{module_id}_call"
        # Determine entity name with priority: Module Name > Single Lock Name > "Call"
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        entity_name = module_data.get("name")

        # Initialize bridge_id and associated_lock_ids early
        self._bridge_id = module_data.get("bridge")
        self._associated_lock_ids: List[str] = []
        found_locks_data: List[Dict[str, Any]] = []  # Store lock data for name check

        if self._bridge_id:
            for other_module_id, other_module_data in coordinator.data.get(
                "modules", {}
            ).items():
                other_variant = other_module_data.get("variant")
                other_subtype = None
                if other_variant and ":" in other_variant:
                    try:
                        other_subtype = other_variant.split(":", 1)[1]
                    except IndexError:
                        other_subtype = None

                if (
                    other_module_data.get("bridge") == self._bridge_id
                    and other_subtype == SUBTYPE_DOORLOCK
                ):
                    self._associated_lock_ids.append(other_module_id)
                    found_locks_data.append(other_module_data)  # Store lock data

        # If module name is missing or seems generic (like the ID), try lock name
        if not entity_name or entity_name == module_id:
            if len(found_locks_data) == 1:
                lock_name = found_locks_data[0].get("name")
                if lock_name and lock_name != found_locks_data[0].get(
                    "id"
                ):  # Check if lock name is valid
                    entity_name = lock_name  # Use the lock's name

        # If still no valid name, default to "Call"
        if not entity_name or entity_name == module_id:
            entity_name = "Call"

        self._attr_name = entity_name

        # Initial state based on coordinator data
        self._update_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}"
            if self.coordinator.home_name
            else "BTicino Intercom"
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
            name=device_name,
            manufacturer="BTicino",
        )

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Check coordinator update success first
        if not self.coordinator.last_update_success:
            return False
        # Return internal state updated by _handle_coordinator_update
        return self._attr_available

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        if hasattr(self, "_attr_is_on") and self._attr_is_on is not None:
            return self._attr_is_on  # Return optimistically set state if available

        # Fallback to coordinator data if no optimistic state
        if not self.coordinator.data:
            return False

        # Access events history correctly
        events = self.coordinator.data.get("events_history", {}).get(
            self.coordinator.home_id, []
        )
        if not events:
            return False

        latest_event = events[0]
        return (
            latest_event.get("type") == "call"
            and not latest_event.get("end")
            and latest_event.get("module_id") == self._module_id
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return entity specific state attributes."""
        # Ensure attributes are updated before returning
        # Note: This relies on the coordinator update running first
        return self._attr_extra_state_attributes

    def _update_state(self) -> None:
        """Update the state of the binary sensor."""
        if not self.coordinator.data:
            self._attr_available = False
            return

        # Update availability based on the associated module's reachability
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id)
        if not module_data:
            self._attr_available = False
            # Clear attributes if module data is missing
            self._attr_extra_state_attributes = {}
            return
        else:
            self._attr_available = module_data.get("reachable", True)

        uptime_sec = module_data.get("uptime")
        last_interaction_ts = module_data.get("last_user_interaction")

        # Start with generic module attributes
        attrs = {
            "module_id": self._module_id,
            "bridge_id": self._bridge_id,
            "variant": module_data.get("variant"),
            "firmware_revision": module_data.get("firmware_revision"),
            "reachable": module_data.get("reachable"),
            "configured": module_data.get("configured"),
            "last_user_interaction": last_interaction_ts,
            "last_user_interaction_iso": format_timestamp_iso(last_interaction_ts),
            "uptime": uptime_sec,
            "uptime_readable": format_uptime_readable(uptime_sec),
            "websocket_connected": module_data.get("websocket_connected"),
            "appliance_type": module_data.get("appliance_type"),
            "associated_locks": self._associated_lock_ids,
        }

        # Find the latest call event specific to this module and add its details
        # Access events history correctly
        events = self.coordinator.data.get("events_history", {}).get(
            self.coordinator.home_id, []
        )
        latest_call_event = None
        if events:
            for event in events:
                if (
                    event.get("type") == "call"
                    and event.get("module_id") == self._module_id
                ):
                    latest_call_event = event
                    break

        if latest_call_event:
            # Extract snapshot/vignette from the first subevent if available
            snapshot_url = None
            snapshot_expires_at = None
            vignette_url = None
            vignette_expires_at = None
            subevents = latest_call_event.get("subevents")
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

            call_start_ts = latest_call_event.get("start")
            call_end_ts = latest_call_event.get("end")

            attrs.update(
                {
                    "call_id": latest_call_event.get("id"),
                    "call_start": call_start_ts,
                    "call_end": call_end_ts,
                    "call_duration": latest_call_event.get("duration"),
                    "call_type": latest_call_event.get("call_type"),
                    "call_status": latest_call_event.get("status"),
                    "snapshot_url": snapshot_url,
                    "snapshot_expires_at": snapshot_expires_at,
                    "vignette_url": vignette_url,
                    "vignette_expires_at": vignette_expires_at,
                    "call_start_iso": format_timestamp_iso(call_start_ts),
                    "call_end_iso": format_timestamp_iso(call_end_ts),
                    "snapshot_expires_at_iso": format_timestamp_iso(
                        snapshot_expires_at
                    ),
                    "vignette_expires_at_iso": format_timestamp_iso(
                        vignette_expires_at
                    ),
                }
            )

        # Filter out None values and update the attribute
        self._attr_extra_state_attributes = {
            k: v for k, v in attrs.items() if v is not None
        }

        # State (is_on) is handled by dispatcher/timer, not directly by coordinator update

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        super()._handle_coordinator_update()

    @callback
    def _handle_call_received(self, state: bool, module_id: str | None) -> None:
        """Handle the dispatcher signal for incoming calls."""
        if module_id == self._module_id:
            _LOGGER.debug(
                "Handling call signal for %s: state=%s", self.entity_id, state
            )
            self._attr_is_on = state
            self.async_write_ha_state()

            if hasattr(self, "_turn_off_canceller") and self._turn_off_canceller:
                _LOGGER.debug(
                    "Cancelling previous turn-off timer for %s", self.entity_id
                )
                self._turn_off_canceller()
                self._turn_off_canceller = None

            if state:
                _LOGGER.debug("Setting turn-off timer for %s", self.entity_id)
                self._turn_off_canceller = async_call_later(
                    self.hass, CALL_SENSOR_TIMEOUT, self._turn_off_callback
                )
        else:
            _LOGGER.debug(
                "Ignoring call signal for module %s on sensor %s",
                module_id,
                self.entity_id,
            )

    @callback
    def _turn_off_callback(self, *args: Any) -> None:
        """Turn the sensor off after timeout."""
        _LOGGER.debug("Executing turn-off callback for %s", self.entity_id)
        self._turn_off_canceller = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CALL_RECEIVED, self._handle_call_received
            )
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        if hasattr(self, "_turn_off_canceller")  and self._turn_off_canceller:
            _LOGGER.debug(
                "Cancelling turn-off timer for %s during removal", self.entity_id
            )
            self._turn_off_canceller()
            self._turn_off_canceller = None
        await super().async_will_remove_from_hass()
