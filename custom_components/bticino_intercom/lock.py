"""Platform for lock integration."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    LOCK_RELOCK_DELAY,
    SUBTYPE_DOORLOCK,
    SUBTYPE_STAIRCASE_LIGHT,
)
from .coordinator import BticinoIntercomCoordinator
from .entity import BticinoEntity
from .utils import format_timestamp_iso

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino locks based on config entry."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    light_as_lock = entry.options.get("light_as_lock", False)

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        _LOGGER.debug("Lock Setup: Found %d modules in coordinator data.", len(coordinator.data["modules"]))
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant")
            subtype = None
            _LOGGER.debug("Lock Setup: Checking module %s, Variant: %s", module_id, variant)
            if variant and ":" in variant:
                try:
                    subtype = variant.split(":", 1)[1]
                    _LOGGER.debug("Lock Setup: Extracted subtype: %s", subtype)
                except IndexError:
                    _LOGGER.warning(
                        "Could not parse subtype from variant '%s' for module %s",
                        variant,
                        module_id,
                    )
                    subtype = None

            if subtype == SUBTYPE_DOORLOCK:
                _LOGGER.debug("Found lock module (via variant subtype): %s", module_id)
                entities.append(BticinoLock(coordinator, module_id))
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
                    "Lock Setup: Module %s subtype '%s' did not match expected lock/light subtypes.",
                    module_id,
                    subtype,
                )
            elif not subtype and variant is not None:
                _LOGGER.debug(
                    "Lock Setup: Module %s has variant '%s' but failed to extract subtype.", module_id, variant
                )

    if not entities:
        _LOGGER.debug("No BTicino lock modules found or configured to be represented as lock")

    async_add_entities(entities)


class BticinoLock(BticinoEntity, LockEntity):
    """Representation of a BTicino lock."""

    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the lock."""
        super().__init__(coordinator, module_id)
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get("name", "Lock")
        self._attr_unique_id = f"{coordinator.entry.entry_id}_lock_{module_id}"
        self._attr_is_locked = bool(module_data.get("lock", True))
        self._relock_canceller: Callable[[], None] | None = None
        self._last_unlocked: str | None = None

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
            "reachable": module_data.get("reachable"),
            "last_used": format_timestamp_iso(module_data.get("last_user_interaction")),
            "last_unlocked": self._last_unlocked,
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock with optimistic state and timer."""
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        self._attr_is_locked = False
        self._last_unlocked = datetime.now(UTC).isoformat()
        self.async_write_ha_state()

        self._relock_canceller = async_call_later(self.hass, LOCK_RELOCK_DELAY, self._relock_callback)

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"lock": False},
                timezone=self.hass.config.time_zone,
            )
        except Exception as err:
            _LOGGER.error("Failed to unlock %s: %s", self._module_id, err)
            if self._relock_canceller:
                self._relock_canceller()
                self._relock_canceller = None
            self._attr_is_locked = True
            self.async_write_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock with optimistic state."""
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        self._attr_is_locked = True
        self.async_write_ha_state()

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"lock": True},
                timezone=self.hass.config.time_zone,
            )
        except Exception as err:
            _LOGGER.error("Failed to lock %s: %s", self._module_id, err)
            self._attr_is_locked = False
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._relock_canceller is None:
            module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
            self._attr_is_locked = bool(module_data.get("lock", True))
        self.async_write_ha_state()

    @callback
    def _relock_callback(self, *args: Any) -> None:
        """Set the state back to locked after the timer expires."""
        _LOGGER.debug("Optimistic relock timer expired for %s", self.entity_id)
        self._relock_canceller = None
        self._attr_is_locked = True
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timer when entity is removed."""
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None
        await super().async_will_remove_from_hass()


class BticinoLightAsLock(BticinoEntity, LockEntity):
    """Representation of a BTicino light used as a lock."""

    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the light as lock."""
        super().__init__(coordinator, module_id)
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        base_name = module_data.get("name", "Door")
        self._attr_name = f"{base_name} Lock"
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light_as_lock_{module_id}"
        self._attr_is_locked = not bool(module_data.get("status") == "on")
        self._relock_canceller: Callable[[], None] | None = None

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
            "reachable": module_data.get("reachable"),
            "last_used": format_timestamp_iso(module_data.get("last_user_interaction")),
            "represented_as": "lock",
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (turn on the light)."""
        _LOGGER.debug("Unlocking %s (turning light ON)", self.entity_id)
        if not self._bridge_id:
            _LOGGER.error("Bridge ID not found for lock %s", self._module_id)
            return

        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        self._attr_is_locked = False
        self.async_write_ha_state()

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": True},
                timezone=self.hass.config.time_zone,
            )
            self._relock_canceller = async_call_later(self.hass, LOCK_RELOCK_DELAY, self._relock_callback)
        except Exception as err:
            _LOGGER.error("Failed to unlock %s: %s", self.entity_id, err)
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

        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        self._attr_is_locked = True
        self.async_write_ha_state()

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=self.coordinator.home_id,
                module_id=self._module_id,
                bridge_id=self._bridge_id,
                state={"on": False},
                timezone=self.hass.config.time_zone,
            )
        except Exception as err:
            _LOGGER.error("Failed to lock %s: %s", self.entity_id, err)
            self._attr_is_locked = False
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._relock_canceller is None:
            module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
            new_state_locked = not bool(module_data.get("status") == "on")
            if self._attr_is_locked != new_state_locked:
                _LOGGER.debug(
                    "Lock %s state updated to %s",
                    self.entity_id,
                    "locked" if new_state_locked else "unlocked",
                )
                self._attr_is_locked = new_state_locked
        self.async_write_ha_state()

    @callback
    def _relock_callback(self, *args: Any) -> None:
        """Set the state back to locked after the timer expires."""
        _LOGGER.debug("Optimistic relock timer expired for %s", self.entity_id)
        self._relock_canceller = None
        self._attr_is_locked = True
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up timer when entity is removed."""
        if self._relock_canceller:
            _LOGGER.debug("Cancelling relock timer for %s on removal", self.entity_id)
            self._relock_canceller()
            self._relock_canceller = None
        await super().async_will_remove_from_hass()
