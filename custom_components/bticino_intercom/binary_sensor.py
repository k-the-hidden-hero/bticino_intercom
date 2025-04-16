"""Platform for binary sensor integration."""

import asyncio
import logging
from typing import Any, Callable
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
from .const import DOMAIN, SIGNAL_CALL_RECEIVED  # Remove unused consts

_LOGGER = logging.getLogger(__name__)

# How long the sensor stays 'on' after a call is detected (in seconds)
CALL_SENSOR_TIMEOUT = 30


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino binary sensor platform."""
    # Get the coordinator from hass.data
    if not (entry_data := hass.data.get(DOMAIN, {}).get(entry.entry_id)) or not (
        coordinator := entry_data.get("coordinator")
    ):
        _LOGGER.error("Coordinator not found in hass.data for entry %s", entry.entry_id)
        return

    entities_to_add = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            # Create a call sensor for each External Unit (likely source of calls)
            if module_data.get("type") == "BNEU":
                _LOGGER.debug("Found external unit module: %s", module_id)
                entities_to_add.append(BticinoCallSensor(coordinator, module_id))
    else:
        _LOGGER.warning("No module data found in coordinator for binary sensors")

    if not entities_to_add:
        _LOGGER.warning("No BTicino external unit modules found for call sensors")

    async_add_entities(entities_to_add)
    # WebSocket is started in __init__ by the coordinator


# Inherit from CoordinatorEntity as well, although state is updated via dispatcher
class BticinoCallSensor(
    CoordinatorEntity[BticinoIntercomCoordinator], BinarySensorEntity
):
    """Representation of a BTicino Incoming Call Sensor."""

    _attr_has_entity_name = True
    _attr_device_class = (
        BinarySensorDeviceClass.SOUND
    )  # Using SOUND to represent ringing
    _attr_should_poll = False  # State updated via dispatcher

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the call sensor."""
        super().__init__(coordinator)  # Initialize CoordinatorEntity
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})

        self._attr_unique_id = f"{DOMAIN}_{module_id}_call"
        # Use name from coordinator data
        self._attr_name = (
            f"{module_data.get('name') or f'External Unit {module_id}'} Call"
        )
        self._attr_is_on = False  # State managed by dispatcher and timer
        self._turn_off_canceller: Callable[[], None] | None = (
            None  # Store the cancel function returned by async_call_later
        )

        bridge_module_id = module_data.get("bridge")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module_id)},
            name=module_data.get("name") or f"External Unit {module_id}",
            manufacturer="BTicino",
            model=module_data.get("type", "BNEU"),  # Use type from data
            via_device=(DOMAIN, bridge_module_id) if bridge_module_id else None,
            # sw_version and hw_version are now populated by the coordinator device registration
        )
        # Initialize extra attributes
        self._update_extra_attributes()

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return device specific state attributes."""
        return self._attr_extra_state_attributes

    @callback
    def _update_extra_attributes(self) -> None:
        """Update extra attributes from coordinator data."""
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            self._attr_extra_state_attributes = None
            return
        module_data = self.coordinator.data["modules"].get(self._module_id)
        if not module_data:
            self._attr_extra_state_attributes = None
            return

        attrs = {
            "module_id": self._module_id,
            "bridge_id": module_data.get("bridge"),
            "variant": module_data.get("variant"),
            "firmware_revision": module_data.get("firmware_revision"),
            "reachable": module_data.get("reachable"),
            "appliance_type": module_data.get("appliance_type"),  # Added
            "local_ipv4": module_data.get(
                "local_ipv4"
            ),  # Added (likely only on bridge)
            "wifi_strength": module_data.get(
                "wifi_strength"
            ),  # Added (likely only on bridge)
            "uptime": module_data.get("uptime"),  # Added
        }
        self._attr_extra_state_attributes = {
            k: v for k, v in attrs.items() if v is not None
        }

    @callback
    def _handle_call_received(self, state: bool, module_id: str | None) -> None:
        """Handle the dispatcher signal for incoming calls."""
        # Check if the signal is relevant for this specific sensor's module
        if module_id == self._module_id:
            _LOGGER.debug(
                "Handling call signal for %s: state=%s", self.entity_id, state
            )
            self._attr_is_on = state
            self.async_write_ha_state()

            # Cancel previous timer if a new 'on' signal arrives or if state becomes false
            if self._turn_off_canceller:
                _LOGGER.debug(
                    "Cancelling previous turn-off timer for %s", self.entity_id
                )
                self._turn_off_canceller()
                self._turn_off_canceller = None

            # If the sensor turned on, set a timer to turn it off
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
        self._turn_off_canceller = None  # Clear the stored canceller
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        # Call CoordinatorEntity's method first
        await super().async_added_to_hass()
        # Register dispatcher listener
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_CALL_RECEIVED, self._handle_call_received
            )
        )
        # Register listener for coordinator updates to update extra attributes
        # Note: _handle_coordinator_update from CoordinatorEntity calls async_write_ha_state
        # We only need to update our internal attribute state here.
        self.async_on_remove(
            self.coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator (for extra attributes)."""
        self._update_extra_attributes()
        # We also need to update availability based on coordinator data
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            self._attr_available = False
        else:
            module_data = self.coordinator.data["modules"].get(self._module_id)
            if not module_data:
                self._attr_available = False
            else:
                self._attr_available = module_data.get("reachable", True)
        # Let CoordinatorEntity handle async_write_ha_state if needed
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        # Cancel the timer if it's active
        if self._turn_off_canceller:
            _LOGGER.debug(
                "Cancelling turn-off timer for %s during removal", self.entity_id
            )
            self._turn_off_canceller()
            self._turn_off_canceller = None
        await super().async_will_remove_from_hass()
