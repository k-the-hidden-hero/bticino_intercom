"""Platform for light integration."""

import logging
from typing import Any, Dict, Optional

from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LIGHT_TYPES
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino lights based on config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") in LIGHT_TYPES:
                _LOGGER.debug("Found light module: %s", module_id)
                entities.append(BticinoLight(coordinator, module_id))

    if not entities:
        _LOGGER.debug("No BTicino light modules found")

    async_add_entities(entities)


class BticinoLight(CoordinatorEntity, LightEntity):
    """Representation of a BTicino light."""

    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get(
            "name", "Light"
        )  # Simplified name as it will be shown under the device
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light_{module_id}"
        self._attr_should_poll = True
        # Store bridge_id for later use
        self._bridge_id = module_data.get("bridge")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
        )

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        if not self.coordinator.data:
            return False

        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        return bool(module_data.get("status") == "on")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return device specific state attributes."""
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            return None
        module_data = self.coordinator.data["modules"].get(self._module_id)
        if not module_data:
            return None

        attrs = {
            "module_id": self._module_id,
            "bridge_id": self._bridge_id,
            "variant": module_data.get("variant"),
            "firmware_revision": module_data.get("firmware_revision"),
            "reachable": module_data.get("reachable"),
            "appliance_type": module_data.get("appliance_type"),
            # Add other relevant attributes from module_data if needed
        }
        # Filter out None values
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for light %s", self._module_id)
            return
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": True},
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn on light %s: %s", self._module_id, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for light %s", self._module_id)
            return
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": False},
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off light %s: %s", self._module_id, err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
