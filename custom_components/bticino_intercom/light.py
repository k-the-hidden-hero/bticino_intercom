"""Support for BTicino lights."""

from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.components.light import LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LIGHT_AUTO_OFF_DELAY, LIGHT_TYPES
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino light platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") in LIGHT_TYPES:
                _LOGGER.debug("Found light module: %s", module_id)
                entities.append(BticinoLight(coordinator, module_id))

    if not entities:
        _LOGGER.debug("No BTicino light modules found")

    async_add_entities(entities)


class BticinoLight(CoordinatorEntity[BticinoIntercomCoordinator], LightEntity):
    """Representation of a BTicino Light."""

    _attr_has_entity_name = True
    _auto_off_canceller: Callable[[], None] | None = None

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_is_on = False

        # Set unique ID
        self._attr_unique_id = f"{DOMAIN}_{module_id}_light"

        # Get the bridge ID for device info
        bridge_module_id = module_data.get("bridge")

        # Set device info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module_id)},
            name=module_data.get("name", f"Light {module_id}"),
            manufacturer="BTicino",
            model=module_data.get("type", "Unknown Light"),
            via_device=(DOMAIN, bridge_module_id) if bridge_module_id else None,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._auto_off_canceller is None:
            self._update_state()
            self.async_write_ha_state()

    def _update_state(self) -> None:
        """Update the state properties based on coordinator data."""
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            self._attr_available = False
            return

        module_data = self.coordinator.data["modules"].get(self._module_id)
        if not module_data:
            self._attr_available = False
            return

        self._attr_available = module_data.get("reachable", True)

    @callback
    def _auto_off_callback(self, *args: Any) -> None:
        """Turn the light off automatically after the delay."""
        self._auto_off_canceller = None
        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        # Cancel any existing auto-off timer
        if self._auto_off_canceller:
            self._auto_off_canceller()
            self._auto_off_canceller = None

        # Get required data from coordinator
        home_id = self.coordinator.entry.data.get("home_id")
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for light %s", self.unique_id)
            return

        # Update state optimistically
        self._attr_is_on = True
        self.async_write_ha_state()

        # Set auto-off timer
        self._auto_off_canceller = async_call_later(
            self.hass, LIGHT_AUTO_OFF_DELAY, self._auto_off_callback
        )

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                bridge_id=bridge_id,
                module_id=self._module_id,
                state={"on": True},
            )
        except Exception as err:
            _LOGGER.error("Failed to turn on light %s: %s", self.unique_id, err)
            # Revert optimistic update on failure
            self._attr_is_on = False
            if self._auto_off_canceller:
                self._auto_off_canceller()
                self._auto_off_canceller = None
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        # Since the light turns off automatically, we just update the state
        if self._auto_off_canceller:
            self._auto_off_canceller()
            self._auto_off_canceller = None

        self._attr_is_on = False
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Handle removal from Home Assistant."""
        if self._auto_off_canceller:
            self._auto_off_canceller()
            self._auto_off_canceller = None
