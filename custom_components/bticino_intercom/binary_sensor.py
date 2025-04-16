"""Platform for binary sensor integration."""

import asyncio
import logging
from typing import Any

from pybticino import AsyncAccount

# Import correct exception if needed, and correct model path
from pybticino.exceptions import ApiError  # Assuming ApiError might be needed later
from pybticino.models import Module

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
    # Get the coordinator from runtime data
    coordinator: BticinoIntercomCoordinator = entry.runtime_data

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


class BticinoCallSensor(BinarySensorEntity):
    """Representation of a BTicino Incoming Call Sensor."""

    _attr_has_entity_name = True
    _attr_device_class = (
        BinarySensorDeviceClass.SOUND
    )  # Using SOUND to represent ringing
    _attr_should_poll = False  # State updated via dispatcher

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the call sensor."""
        self.coordinator = coordinator  # Store coordinator for device info
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})

        self._attr_unique_id = f"{DOMAIN}_{module_id}_call"
        # Use name from coordinator data
        self._attr_name = (
            f"{module_data.get('name') or f'External Unit {module_id}'} Call"
        )
        self._attr_is_on = False
        self._turn_off_timer: asyncio.TimerHandle | None = None

        bridge_module_id = module_data.get("bridge")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module_id)},
            name=module_data.get("name") or f"External Unit {module_id}",
            manufacturer="BTicino",
            model=module_data.get("type", "BNEU"),  # Use type from data
            via_device=(DOMAIN, bridge_module_id) if bridge_module_id else None,
            # sw_version=module_data.get("firmware_version"),
        )

    @callback
    def _handle_call_received(self, state: bool, module_id: str | None) -> None:
        """Handle the dispatcher signal for incoming calls."""
        """Handle the dispatcher signal for incoming calls."""
        # Check if the signal is relevant for this specific sensor's module
        if module_id == self._module_id:
            _LOGGER.debug(
                "Handling call signal for %s: state=%s", self.entity_id, state
            )
            self._attr_is_on = state
            self.async_write_ha_state()

            # Cancel previous timer if a new 'on' signal arrives
            if self._turn_off_timer:
                self._turn_off_timer.cancel()
                self._turn_off_timer = None

            # If the sensor turned on, set a timer to turn it off
            if state:
                self._turn_off_timer = async_call_later(
                    self.hass, CALL_SENSOR_TIMEOUT, self._turn_off
                )
        else:
            _LOGGER.debug(
                "Ignoring call signal for module %s on sensor %s",
                module_id,
                self.entity_id,
            )

    @callback
    def _turn_off(self, *args: Any) -> None:
        """Turn the sensor off after timeout."""
        _LOGGER.debug("Turning off call sensor %s after timeout", self.entity_id)
        self._turn_off_timer = None
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
        await super().async_will_remove_from_hass()
        if self._turn_off_timer:
            self._turn_off_timer.cancel()
            self._turn_off_timer = None
