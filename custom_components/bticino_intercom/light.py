"""Platform for light integration."""

import logging
from typing import Any, ClassVar

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SUBTYPE_STAIRCASE_LIGHT,
)
from .coordinator import BticinoIntercomCoordinator
from .entity import BticinoEntity
from .utils import cleanup_orphaned_entities, format_timestamp_iso

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino lights based on config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    light_as_lock = entry.options.get("light_as_lock", False)

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        _LOGGER.debug("Light Setup: Found %d modules in coordinator data.", len(coordinator.data["modules"]))
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant")
            subtype = None
            _LOGGER.debug("Light Setup: Checking module %s, Variant: %s", module_id, variant)
            if variant and ":" in variant:
                try:
                    subtype = variant.split(":", 1)[1]
                    _LOGGER.debug("Light Setup: Extracted subtype: %s", subtype)
                except IndexError:
                    _LOGGER.warning(
                        "Could not parse subtype from variant '%s' for module %s",
                        variant,
                        module_id,
                    )
                    subtype = None

            if subtype == SUBTYPE_STAIRCASE_LIGHT and not light_as_lock:
                _LOGGER.debug(
                    "Found light module %s (via variant subtype), representing as standard light.",
                    module_id,
                )
                entities.append(BticinoLight(coordinator, module_id))
            elif subtype == SUBTYPE_STAIRCASE_LIGHT and light_as_lock:
                _LOGGER.debug(
                    "Found light module %s (via variant subtype), but configured as lock. Skipping light entity creation.",
                    module_id,
                )
            elif subtype:
                _LOGGER.debug(
                    "Light Setup: Module %s subtype '%s' did not match expected light subtype.", module_id, subtype
                )
            elif not subtype and variant is not None:
                _LOGGER.debug(
                    "Light Setup: Module %s has variant '%s' but failed to extract subtype.", module_id, variant
                )

    if not entities:
        _LOGGER.debug("No BTicino light modules found or configured to be represented as light")

    cleanup_orphaned_entities(hass, entry.entry_id, "light", entities)
    async_add_entities(entities)


class BticinoLight(BticinoEntity, LightEntity):
    """Representation of a BTicino light."""

    _attr_icon = "mdi:light-recessed"
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the light."""
        super().__init__(coordinator, module_id)
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get("name", "Light")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light_{module_id}"
        self._attr_is_on = bool(module_data.get("status") == "on")

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id)
        if not module_data:
            return False
        return module_data.get("reachable", True)

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._attr_is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return device specific state attributes."""
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            return None
        module_data = self.coordinator.data["modules"].get(self._module_id)
        if not module_data:
            return None

        attrs = {
            "reachable": module_data.get("reachable"),
            "last_used": format_timestamp_iso(module_data.get("last_user_interaction")),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with optimistic update."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for light %s", self._module_id)
            return

        self._attr_is_on = True
        self.async_write_ha_state()

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": True},
                timezone=self.hass.config.time_zone,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn on light %s: %s", self._module_id, err)
            self._attr_is_on = False
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off with optimistic update."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for light %s", self._module_id)
            return

        self._attr_is_on = False
        self.async_write_ha_state()

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": False},
                timezone=self.hass.config.time_zone,
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off light %s: %s", self._module_id, err)
            self._attr_is_on = True
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        new_state = bool(module_data.get("status") == "on")
        if self._attr_is_on != new_state:
            _LOGGER.debug("Light %s state updated to %s", self.entity_id, new_state)
            self._attr_is_on = new_state
        self.async_write_ha_state()
