"""Platform for light integration."""

import logging
from typing import Any, Dict, Optional
from datetime import datetime, timezone, timedelta

from homeassistant.components.light import LightEntity, ColorMode
from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_call_later
from homeassistant.util.dt import utc_from_timestamp

from .const import (
    DOMAIN,
    LOCK_RELOCK_DELAY,
    SUBTYPE_STAIRCASE_LIGHT,
)
from .coordinator import BticinoIntercomCoordinator
from .utils import format_timestamp_iso, format_uptime_readable

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino lights based on config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Get the configuration option
    light_as_lock = entry.options.get("light_as_lock", False)

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        _LOGGER.debug(
            f"Light Setup: Found {len(coordinator.data['modules'])} modules in coordinator data."
        )
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant")
            subtype = None
            _LOGGER.debug(
                f"Light Setup: Checking module {module_id}, Variant: {variant}"
            )
            if variant and ":" in variant:
                try:
                    subtype = variant.split(":", 1)[1]
                    _LOGGER.debug(f"Light Setup: Extracted subtype: {subtype}")
                except IndexError:
                    _LOGGER.warning(
                        "Could not parse subtype from variant '%s' for module %s",
                        variant,
                        module_id,
                    )
                    subtype = None

            # Only create light entity if subtype matches AND light_as_lock is False
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
                    f"Light Setup: Module {module_id} subtype '{subtype}' did not match expected light subtype."
                )
            # Optionally log other subtypes or missing variants
            elif not subtype and variant is not None:
                _LOGGER.debug(
                    f"Light Setup: Module {module_id} has variant '{variant}' but failed to extract subtype."
                )

    if not entities:
        _LOGGER.debug(
            "No BTicino light modules found or configured to be represented as light"
        )

    async_add_entities(entities)


class BticinoLight(CoordinatorEntity, LightEntity):
    """Representation of a BTicino light."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:light-recessed"
    _attr_supported_color_modes = {ColorMode.ONOFF}
    _attr_color_mode = ColorMode.ONOFF

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get("name", "Light")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light_{module_id}"
        # Initialize state based on coordinator data
        self._attr_is_on = bool(module_data.get("status") == "on")
        self._bridge_id = module_data.get("bridge")

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
        # Check specific module reachability
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id)
        if not module_data:
            return False  # Module disappeared?
        return module_data.get("reachable", True)  # Assume reachable if key missing

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        # Return internal state which is updated optimistically and by coordinator
        return self._attr_is_on

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return device specific state attributes."""
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            return None
        module_data = self.coordinator.data["modules"].get(self._module_id)
        if not module_data:
            return None

        uptime_sec = module_data.get("uptime")
        last_interaction_ts = module_data.get("last_user_interaction")

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
            "appliance_type": module_data.get("appliance_type"),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on with optimistic update."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for light %s", self._module_id)
            return

        # Optimistic update
        self._attr_is_on = True
        self.async_write_ha_state()  # Logbook entry!

        # Send command to API
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": True},
            )
            # Request refresh to confirm state eventually
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn on light %s: %s", self._module_id, err)
            # Revert optimistic state on error
            self._attr_is_on = False
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off with optimistic update."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for light %s", self._module_id)
            return

        # Optimistic update
        self._attr_is_on = False
        self.async_write_ha_state()  # Logbook entry!

        # Send command to API
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": False},
            )
            # Request refresh to confirm state eventually
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to turn off light %s: %s", self._module_id, err)
            # Revert optimistic state on error
            self._attr_is_on = True
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update internal state from coordinator data
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        new_state = bool(module_data.get("status") == "on")
        if self._attr_is_on != new_state:
            _LOGGER.debug("Light %s state updated to %s", self.entity_id, new_state)
            self._attr_is_on = new_state
        # Let HA know state + attributes might have changed
        self.async_write_ha_state()
