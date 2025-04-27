"""Platform for lock integration."""

import asyncio
import logging
from typing import Any, Callable  # Added Callable and Any
from contextlib import suppress  # Added suppress

from pybticino import AsyncAccount

# Import ApiError as well
from pybticino.exceptions import ApiError

# Correct import path for Module model is no longer needed
# from pybticino.models import Module

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Import CoordinatorEntity, async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.event import async_call_later  # Added async_call_later

# Import the actual coordinator
from .coordinator import BticinoIntercomCoordinator

# Import domain constant
from .const import DOMAIN, LOCK_TYPES

_LOGGER = logging.getLogger(__name__)

# Define delay for optimistic relock
RELOCK_DELAY = 5  # seconds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino lock platform."""
    # Get the coordinator from hass.data
    if not (entry_data := hass.data.get(DOMAIN, {}).get(entry.entry_id)) or not (
        coordinator := entry_data.get("coordinator")
    ):
        _LOGGER.error("Coordinator not found in hass.data for entry %s", entry.entry_id)
        return

    # Assuming coordinator.data is populated with module info after initial fetch
    entities_to_add = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") in LOCK_TYPES:  # BTicino Door Lock module type
                _LOGGER.debug("Found lock module: %s", module_id)
                entities_to_add.append(BticinoLockEntity(coordinator, module_id))
    else:
        _LOGGER.warning("No module data found in coordinator")
        # Optionally raise ConfigEntryNotReady if locks are expected but not found

    if not entities_to_add:
        _LOGGER.warning("No BTicino lock modules found")

    async_add_entities(entities_to_add)


# Inherit from CoordinatorEntity
class BticinoLockEntity(CoordinatorEntity[BticinoIntercomCoordinator], LockEntity):
    """Representation of a BTicino Lock."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        LockEntityFeature.OPEN  # Support OPEN if unlock means opening the latch
    )
    _relock_canceller: Callable[[], None] | None = (
        None  # Attribute for timer cancel callback
    )

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})

        self._attr_unique_id = f"{DOMAIN}_{module_id}_lock"
        # Lock name is inherited via _attr_has_entity_name = True and device name

        bridge_module_id = module_data.get("bridge")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module_id)},
            name=module_data.get("name") or f"Lock {module_id}",  # Keep name for device
            manufacturer="BTicino",
            model=module_data.get("type") or "Unknown Lock",  # Use actual type or fallback to generic name
            via_device=(DOMAIN, bridge_module_id) if bridge_module_id else None,
            # sw_version and hw_version are now populated by the coordinator device registration
        )
        # Initialize state from coordinator
        self._update_state()  # Initial state update

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
            "bridge_id": module_data.get("bridge"),
            "variant": module_data.get("variant"),
            "firmware_revision": module_data.get("firmware_revision"),
            "reachable": module_data.get("reachable"),
            "appliance_type": module_data.get("appliance_type"),
            "local_ipv4": module_data.get("local_ipv4"),
            "wifi_strength": module_data.get("wifi_strength"),
            "uptime": module_data.get("uptime"),
        }
        # Filter out None values
        return {k: v for k, v in attrs.items() if v is not None}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        # Only update state if not in the middle of an optimistic update
        if self._relock_canceller is None:
            self._update_state()
            self.async_write_ha_state()
        else:
            _LOGGER.debug(
                "Skipping coordinator update for %s during optimistic state",
                self.entity_id,
            )

    def _update_state(self) -> None:
        """Update the state properties based on coordinator data."""
        if not self.coordinator.data or "modules" not in self.coordinator.data:
            self._attr_available = False
            return

        module_data = self.coordinator.data["modules"].get(self._module_id)
        if not module_data:
            self._attr_available = False
            return

        # Update availability based on reachability if available
        self._attr_available = module_data.get("reachable", True)

        # Update is_locked based on the 'lock' state (assuming boolean)
        # Default to True (locked) if 'lock' key is missing
        # Only update if not optimistically unlocked
        if self._relock_canceller is None:
            self._attr_is_locked = module_data.get("lock", True)

    @property
    def is_locking(self) -> bool | None:
        """Return true if lock is locking."""
        return None  # Not supported

    @property
    def is_unlocking(self) -> bool | None:
        """Return true if lock is unlocking."""
        # Optimistic unlock state
        return self._relock_canceller is not None and not self._attr_is_locked

    @property
    def is_jammed(self) -> bool | None:
        """Return true if lock is jammed."""
        # Update if coordinator provides this info, e.g., based on a specific status
        return False  # Default to False

    async def async_open(self, **kwargs: Any) -> None:
        """Open the door latch with optimistic update."""
        # Cancel any pending relock timer first
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        _LOGGER.info("Attempting to open lock optimistically: %s", self.unique_id)
        home_id = self.coordinator.entry.data.get("home_id")
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for lock %s", self.unique_id)
            return

        # Optimistic update: Set state to unlocked immediately
        self._attr_is_locked = False
        self.async_write_ha_state()  # This will trigger logbook entry for state change

        # Set a timer to automatically relock after a short delay
        self._relock_canceller = async_call_later(
            self.hass, RELOCK_DELAY, self._relock_callback
        )

        # Now send the command to the API
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                module_id=self._module_id,
                state={"lock": False},  # Assuming this payload is correct
                bridge_id=bridge_id,
            )
            _LOGGER.info("Open command sent successfully for lock: %s", self.unique_id)
            # No immediate refresh needed due to optimistic update
        except ApiError as err:
            _LOGGER.error(
                "Failed to send open command for lock %s: %s", self.unique_id, err
            )
            # Revert optimistic state on API error?
            # Maybe not, as the command might have succeeded. Let the timer handle it.
            # Or cancel timer and force refresh? For now, let timer run.
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error opening lock %s: %s", self.unique_id, err
            )
            # Revert optimistic state on unexpected error?
            if self._relock_canceller:
                self._relock_canceller()
                self._relock_canceller = None
            self._attr_is_locked = True  # Revert to locked
            self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (same as open)."""
        await self.async_open(**kwargs)

    @callback
    def _relock_callback(self, *args: Any) -> None:
        """Callback to set the state back to locked after a delay."""
        _LOGGER.debug("Executing optimistic relock for %s", self.entity_id)
        self._relock_canceller = None
        # We assume it's locked again, coordinator update will confirm later
        self._attr_is_locked = True
        self.async_write_ha_state()  # This will trigger logbook entry for state change
        # Request a refresh now to confirm the state from the API
        # Use create_task to avoid blocking the callback
        self.hass.async_create_task(self.coordinator.async_request_refresh())

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""
        # Cancel any pending relock timer if we explicitly lock
        if self._relock_canceller:
            self._relock_canceller()
            self._relock_canceller = None

        _LOGGER.info("Attempting to lock lock: %s", self.unique_id)
        home_id = self.coordinator.entry.data.get("home_id")
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for lock %s", self.unique_id)
            return

        # Optimistic update: Assume lock succeeded immediately
        self._attr_is_locked = True
        self.async_write_ha_state()  # This will trigger logbook entry for state change

        # Send the command to the API
        try:
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                module_id=self._module_id,
                state={"lock": True},  # Assuming this payload is correct
                bridge_id=bridge_id,
            )
            _LOGGER.info("Lock command sent successfully for lock: %s", self.unique_id)
            # Optional: Request a refresh to confirm, but not strictly needed
        except ApiError as err:
            _LOGGER.error(
                "Failed to send lock command for lock %s: %s", self.unique_id, err
            )
            # Revert optimistic state? Maybe request refresh instead.
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error locking lock %s: %s", self.unique_id, err
            )
            # Revert optimistic state and refresh
            self._attr_is_locked = False  # Assume it failed
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        # Cancel the timer if it's active
        if self._relock_canceller:
            _LOGGER.debug(
                "Cancelling relock timer for %s during removal", self.entity_id
            )
            self._relock_canceller()
            self._relock_canceller = None
        await super().async_will_remove_from_hass()
