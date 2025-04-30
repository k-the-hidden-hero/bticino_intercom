"""Platform for binary sensor integration."""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from contextlib import suppress  # Import suppress for async_will_remove_from_hass

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

# Import the actual coordinator and signal
from .coordinator import BticinoIntercomCoordinator

# Import CoordinatorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import (
    DOMAIN,
    SIGNAL_CALL_RECEIVED,
    DOOR_BELL_TYPES,
    LOCK_TYPES,
)  # Remove unused consts

_LOGGER = logging.getLogger(__name__)

# How long the sensor stays 'on' after a call is detected (in seconds)
CALL_SENSOR_TIMEOUT = 30


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
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") in DOOR_BELL_TYPES:
                _LOGGER.debug("Found external unit module: %s", module_id)
                entities.append(BticinoCallBinarySensor(coordinator, module_id))

    if not entities:
        _LOGGER.debug("No BTicino external unit modules found")

    async_add_entities(entities)


class BticinoCallBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a BTicino Intercom call binary sensor."""

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._module_id = module_id
        self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
        self._attr_extra_state_attributes = {}
        self._attr_icon = "mdi:phone-in-talk"
        self._attr_name = (
            f"Call {module_id}"  # Simplified name as it will be shown under the device
        )
        self._attr_unique_id = f"{coordinator.entry.entry_id}_call_{module_id}"
        self._attr_should_poll = True
        self._turn_off_canceller = None
        # Store bridge_id for later use in attributes
        self._bridge_id = (
            coordinator.data.get("modules", {}).get(module_id, {}).get("bridge")
        )

        # Find associated locks and determine name
        self._associated_lock_ids: List[str] = []
        binary_sensor_module_data = coordinator.data.get("modules", {}).get(
            module_id, {}
        )
        self._bridge_id = binary_sensor_module_data.get("bridge")
        entity_base_name = (
            binary_sensor_module_data.get("name") or f"Call {self._module_id}"
        )

        if self._bridge_id:
            found_locks = []
            for other_module_id, other_module_data in coordinator.data.get(
                "modules", {}
            ).items():
                if (
                    other_module_data.get("bridge") == self._bridge_id
                    and other_module_data.get("type") in LOCK_TYPES
                ):
                    found_locks.append(other_module_data)
                    self._associated_lock_ids.append(other_module_id)

            if len(found_locks) == 1:
                lock_name = found_locks[0].get("name")
                if lock_name:
                    entity_base_name = (
                        f"Call {lock_name}"  # Use associated lock name if unique
                    )
            # If 0 or >1 locks, keep the binary sensor module name determined earlier

        self._attr_name = entity_base_name
        # Initialize availability
        self._attr_available = binary_sensor_module_data.get("reachable", True)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
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

        events = self.coordinator.data.get("events", [])
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

        # Start with generic module attributes
        attrs = {
            "module_id": self._module_id,
            "bridge_id": self._bridge_id,
            "variant": module_data.get("variant"),
            "firmware_revision": module_data.get("firmware_revision"),
            "reachable": module_data.get("reachable"),
            "appliance_type": module_data.get("appliance_type"),
            "associated_locks": self._associated_lock_ids,
        }

        # Find the latest call event specific to this module and add its details
        events = self.coordinator.data.get("events", [])
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
            attrs.update(
                {
                    "call_id": latest_call_event.get("id"),
                    "call_start": latest_call_event.get("start"),
                    "call_end": latest_call_event.get("end"),
                    "call_duration": latest_call_event.get("duration"),
                    "call_type": latest_call_event.get("call_type"),
                    "call_status": latest_call_event.get("status"),
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
        if self._turn_off_canceller:
            _LOGGER.debug(
                "Cancelling turn-off timer for %s during removal", self.entity_id
            )
            self._turn_off_canceller()
            self._turn_off_canceller = None
        await super().async_will_remove_from_hass()
