"""Platform for lock integration."""

import logging
from typing import Any, Dict, Optional, Callable
import asyncio

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_call_later

from .const import DOMAIN, LOCK_TYPES
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)

RELOCK_DELAY = 5  # Seconds


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
        self._attr_name = module_data.get("name", "Lock")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_lock_{module_id}"
        # Lock state is based on coordinator data, but we need _attr_is_locked for optimistic
        self._attr_is_locked = bool(module_data.get("lock", True))
        self._bridge_id = module_data.get("bridge")
        self._relock_canceller: Callable[[], None] | None = None

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
        # Check specific module reachability
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id)
        if not module_data:
            return False  # Module disappeared?
        return module_data.get("reachable", True)  # Assume reachable if key missing

    @property
    def is_locked(self) -> bool:
        """Return true if lock is locked."""
        # Return optimistic state if timer is active
        if self._relock_canceller is not None:
            return self._attr_is_locked
        # Otherwise, return state from coordinator
        # Use internal _attr_is_locked which is updated by coordinator_update
        return self._attr_is_locked

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
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock with optimistic state and timer."""
        # Cancel any previous relock timer
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        # Optimistic update
        self._attr_is_locked = False
        self.async_write_ha_state()  # Logbook entry!

        # Set timer to automatically relock state
        self._relock_canceller = async_call_later(
            self.hass, RELOCK_DELAY, self._relock_callback
        )

        # Send command to API
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"lock": False},
            )
            # No need to refresh immediately, coordinator will handle it
        except Exception as err:
            _LOGGER.error("Failed to unlock %s: %s", self._module_id, err)
            # Revert optimistic state on error
            if self._relock_canceller:
                self._relock_canceller()
                self._relock_canceller = None
            self._attr_is_locked = True  # Revert to locked
            self.async_write_ha_state()
            # Optionally trigger a refresh on error to get actual state sooner
            # await self.coordinator.async_request_refresh()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock with optimistic state."""
        # Cancel relock timer if it's running
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        # Optimistic update
        self._attr_is_locked = True
        self.async_write_ha_state()  # Logbook entry!

        # Send command to API
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"lock": True},
            )
            # No need to refresh immediately
        except Exception as err:
            _LOGGER.error("Failed to lock %s: %s", self._module_id, err)
            # Revert optimistic state on error
            self._attr_is_locked = False
            self.async_write_ha_state()
            # Optionally trigger refresh
            # await self.coordinator.async_request_refresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update internal state only if not optimistically unlocked
        if self._relock_canceller is None:
            module_data = self.coordinator.data.get("modules", {}).get(
                self._module_id, {}
            )
            self._attr_is_locked = bool(module_data.get("lock", True))

        # Let HA know state + attributes might have changed
        self.async_write_ha_state()

    @callback
    def _relock_callback(self, *args: Any) -> None:
        """Set the state back to locked after the timer expires."""
        _LOGGER.debug("Optimistic relock timer expired for %s", self.entity_id)
        self._relock_canceller = None
        self._attr_is_locked = True
        self.async_write_ha_state()  # Logbook entry for relock
        # Optionally request refresh to confirm
        # self.hass.async_create_task(self.coordinator.async_request_refresh())

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timer when entity is removed."""
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None
        await super().async_will_remove_from_hass()
