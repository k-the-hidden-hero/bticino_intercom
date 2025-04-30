"""Platform for lock integration."""

import logging
from typing import Any, Dict, Optional

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOCK_TYPES
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino locks based on config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") in LOCK_TYPES:
                _LOGGER.debug("Found lock module: %s", module_id)
                entities.append(BticinoLock(coordinator, module_id))

    if not entities:
        _LOGGER.debug("No BTicino lock modules found")

    async_add_entities(entities)


class BticinoLock(CoordinatorEntity, LockEntity):
    """Representation of a BTicino lock."""

    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the lock."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get(
            "name", "Lock"
        )  # Simplified name as it will be shown under the device
        self._attr_unique_id = f"{coordinator.entry.entry_id}_lock_{module_id}"
        self._attr_should_poll = True
        self._bridge_id = module_data.get("bridge")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
        )

    @property
    def is_locked(self) -> bool:
        """Return true if lock is locked."""
        if not self.coordinator.data:
            return True  # Default to locked for safety

        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        return bool(module_data.get("lock", True))

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
            "local_ipv4": module_data.get("local_ipv4"),
            "wifi_strength": module_data.get("wifi_strength"),
            "uptime": module_data.get("uptime"),
            # Add other relevant attributes from module_data if needed
        }
        # Filter out None values
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"lock": False},
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to unlock %s: %s", self._module_id, err)

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"lock": True},
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to lock %s: %s", self._module_id, err)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
