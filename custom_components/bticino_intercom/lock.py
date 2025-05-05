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

from .const import (
    DOMAIN,
    LOCK_RELOCK_DELAY,
    SUBTYPE_DOORLOCK,
    SUBTYPE_STAIRCASE_LIGHT,
    SUBTYPE_TO_PLATFORM,
    Platform,
)
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino locks based on config entry."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    # Get the configuration option
    light_as_lock = entry.options.get("light_as_lock", False)

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        _LOGGER.debug(
            f"Lock Setup: Found {len(coordinator.data['modules'])} modules in coordinator data."
        )
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant")
            subtype = None
            _LOGGER.debug(
                f"Lock Setup: Checking module {module_id}, Variant: {variant}"
            )
            if variant and ":" in variant:
                try:
                    subtype = variant.split(":", 1)[1]
                    _LOGGER.debug(f"Lock Setup: Extracted subtype: {subtype}")
                except IndexError:
                    _LOGGER.warning(
                        "Could not parse subtype from variant '%s' for module %s",
                        variant,
                        module_id,
                    )
                    subtype = None  # Ensure subtype is None if split fails

            # Check if the subtype corresponds to a lock
            if subtype == SUBTYPE_DOORLOCK:
                _LOGGER.debug("Found lock module (via variant subtype): %s", module_id)
                entities.append(BticinoLock(coordinator, module_id))

            # Check if the subtype corresponds to a light and light_as_lock is enabled
            elif subtype == SUBTYPE_STAIRCASE_LIGHT and light_as_lock:
                _LOGGER.debug(
                    "Found light module %s (via variant subtype), representing as lock (light_as_lock=True).",
                    module_id,
                )
                entities.append(BticinoLightAsLock(coordinator, module_id))
            elif subtype == SUBTYPE_STAIRCASE_LIGHT and not light_as_lock:
                _LOGGER.debug(
                    "Found light module %s (via variant subtype), but configured as light. Skipping lock entity creation.",
                    module_id,
                )
            elif subtype:
                _LOGGER.debug(
                    f"Lock Setup: Module {module_id} subtype '{subtype}' did not match expected lock/light subtypes."
                )
            # Optionally, log modules with unknown/missing variants if needed for debugging
            elif not subtype and variant is not None:
                _LOGGER.debug(
                    f"Lock Setup: Module {module_id} has variant '{variant}' but failed to extract subtype."
                )
            # else: # subtype is None and variant is None
            #     _LOGGER.debug(f"Lock Setup: Module {module_id} has no variant field.") # Potentially too verbose

    if not entities:
        _LOGGER.debug(
            "No BTicino lock modules found or configured to be represented as lock"
        )

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
            "configured": module_data.get("configured"),
            "last_user_interaction": module_data.get("last_user_interaction"),
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
            self.hass, LOCK_RELOCK_DELAY, self._relock_callback
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


class BticinoLightAsLock(CoordinatorEntity, LockEntity):
    """Representation of a BTicino light used as a lock."""

    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the light as lock."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        # Use the light's name but append ' Lock'
        base_name = module_data.get("name", "Door")
        self._attr_name = f"{base_name} Lock"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light_as_lock_{module_id}"
        # Lock state is inverse of light state (light on = unlocked, light off = locked)
        self._attr_is_locked = not bool(module_data.get("status") == "on")
        self._bridge_id = module_data.get("bridge")
        self._relock_canceller: Callable[[], None] | None = (
            None  # Ensure Callable is imported if not already
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        # Associate with the same device as the coordinator
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
        )

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
    def is_locked(self) -> bool:
        """Return true if lock is locked."""
        # Return optimistic state if timer is active
        if self._relock_canceller is not None:
            return self._attr_is_locked
        # Otherwise, return state from coordinator
        return self._attr_is_locked

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return device specific state attributes."""
        # Reuse attributes from BticinoLight for consistency
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
            "configured": module_data.get("configured"),
            "last_user_interaction": module_data.get("last_user_interaction"),
            "uptime": module_data.get("uptime"),
            "appliance_type": module_data.get("appliance_type"),
            "local_ipv4": module_data.get("local_ipv4"),
            "wifi_strength": module_data.get("wifi_strength"),
            "represented_as": "lock",  # Indicate this light is acting as a lock
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (turn on the light)."""
        _LOGGER.debug("Unlocking %s (turning light ON)", self.entity_id)
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        # Cancel any existing relock timer
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        # Optimistic update
        self._attr_is_locked = False
        self.async_write_ha_state()

        try:
            # Turn on the light to unlock
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": True},
            )

            # Set auto-relock timer
            self._relock_canceller = async_call_later(
                self.hass, LOCK_RELOCK_DELAY, self._relock_callback
            )

        except Exception as err:
            _LOGGER.error("Failed to unlock %s: %s", self.entity_id, err)
            # Revert optimistic state on error
            if self._relock_canceller:
                self._relock_canceller()
                self._relock_canceller = None
            self._attr_is_locked = True
            self.async_write_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door (turn off the light)."""
        _LOGGER.debug("Locking %s (turning light OFF)", self.entity_id)
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        # Cancel any relock timer (might be called manually)
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        # Optimistic update
        self._attr_is_locked = True
        self.async_write_ha_state()

        try:
            # Turn off the light to lock
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": False},
            )
        except Exception as err:
            _LOGGER.error("Failed to lock %s: %s", self.entity_id, err)
            # Revert optimistic state on error
            self._attr_is_locked = False
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Update internal state only if not optimistically unlocked
        if self._relock_canceller is None:
            module_data = self.coordinator.data.get("modules", {}).get(
                self._module_id, {}
            )
            # Light on = unlocked, Light off = locked
            new_state_locked = not bool(module_data.get("status") == "on")
            if self._attr_is_locked != new_state_locked:
                _LOGGER.debug(
                    "Lock %s state updated to %s",
                    self.entity_id,
                    "locked" if new_state_locked else "unlocked",
                )
                self._attr_is_locked = new_state_locked
        # Let HA know state + attributes might have changed
        self.async_write_ha_state()

    @callback
    def _relock_callback(self, *args: Any) -> None:
        """Set the state back to locked after the timer expires."""
        _LOGGER.debug("Optimistic relock timer expired for %s", self.entity_id)
        self._relock_canceller = None
        self._attr_is_locked = True
        self.async_write_ha_state()
        # Optionally request refresh to confirm
        # self.hass.async_create_task(self.coordinator.async_request_refresh())

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timer when entity is removed."""
        if self._relock_canceller:
            _LOGGER.debug("Cancelling relock timer for %s on removal", self.entity_id)
            self._relock_canceller()
            self._relock_canceller = None
        await super().async_will_remove_from_hass()
