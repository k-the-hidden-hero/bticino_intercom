"""Platform for lock integration."""

import logging
from typing import Any

from pybticino import AsyncAccount

# Import ApiError as well
from pybticino.exceptions import ApiError

# Correct import path for Module model
from pybticino.models import Module

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

# Import CoordinatorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

# Import the actual coordinator
from .coordinator import BticinoIntercomCoordinator

# Remove unused consts if data comes from coordinator
from .const import DOMAIN  # , ACCOUNT, ATTR_MODULE_ID, ATTR_HOME_ID

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino lock platform."""
    # Get the coordinator from runtime data
    coordinator: BticinoIntercomCoordinator = entry.runtime_data

    # Assuming coordinator.data is populated with module info after initial fetch
    # The structure might need adjustment based on how coordinator stores data
    # Example: coordinator.data = {"modules": {"module_id1": {...}, "module_id2": {...}}, "homes": {...}}
    entities_to_add = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            if module_data.get("type") == "BNDL":  # BTicino Door Lock module type
                _LOGGER.debug("Found lock module: %s", module_id)
                entities_to_add.append(BticinoLockEntity(coordinator, module_id))
    else:
        _LOGGER.warning("No module data found in coordinator")
        # Optionally raise ConfigEntryNotReady if locks are expected but not found

    if not entities_to_add:
        _LOGGER.warning("No BTicino lock modules found")
        # No return needed here, async_add_entities handles empty list

    async_add_entities(entities_to_add)


# Inherit from CoordinatorEntity
class BticinoLockEntity(CoordinatorEntity[BticinoIntercomCoordinator], LockEntity):
    """Representation of a BTicino Lock."""

    _attr_has_entity_name = True
    # Remove default state, it will come from the coordinator
    # _attr_is_locked = True
    _attr_supported_features = (
        LockEntityFeature.OPEN  # Support OPEN if unlock means opening the latch
        # Add LockEntityFeature.LOCK if locking is supported? Check API.
    )

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the lock entity."""
        # Pass coordinator to CoordinatorEntity
        super().__init__(coordinator)
        self._module_id = module_id
        # Get initial module data from coordinator
        module_data = coordinator.data.get("modules", {}).get(module_id, {})

        self._attr_unique_id = f"{DOMAIN}_{module_id}_lock"
        # Set name based on coordinator data, fallback if needed
        self._attr_name = module_data.get("name") or f"Lock {module_id}"

        bridge_module_id = module_data.get("bridge")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module_id)},
            name=self._attr_name,
            manufacturer="BTicino",
            model=module_data.get("type", "BNDL"),  # Use type from data
            via_device=(DOMAIN, bridge_module_id) if bridge_module_id else None,
            # Add firmware/hardware versions if available in module_data
            # sw_version=module_data.get("firmware_version"),
        )
        # Initialize state from coordinator
        self._update_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
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

        self._attr_available = True  # Mark as available if we have data
        # Update is_locked based on the 'lock' state from the module data
        # Adjust the key 'lock' if it's different in your actual data structure
        self._attr_is_locked = module_data.get(
            "lock", True
        )  # Default to locked if state unknown

    # is_locking, is_unlocking, is_jammed properties remain False unless data is available

    @property
    def is_locking(self) -> bool | None:
        """Return true if lock is locking."""
        # Update if coordinator provides this info
        return False

    @property
    def is_unlocking(self) -> bool | None:
        """Return true if lock is unlocking."""
        # Update if coordinator provides this info
        return False

    @property
    def is_jammed(self) -> bool | None:
        """Return true if lock is jammed."""
        # Update if coordinator provides this info
        return False

    # Implement LockEntityFeature.OPEN if unlock actually opens the latch
    async def async_open(self, **kwargs: Any) -> None:
        """Open the door latch."""
        _LOGGER.info("Attempting to open lock: %s", self.unique_id)
        # Get home_id from the config entry associated with the coordinator
        home_id = self.coordinator.entry.data.get(
            "home_id"
        )  # Adjust if home_id stored differently
        # Get bridge_id from module data in coordinator
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for lock %s", self.unique_id)
            return

        try:
            # Use coordinator's account object
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                module_id=self._module_id,
                state={"lock": False},  # Assuming this payload is correct
                bridge_id=bridge_id,  # Pass bridge_id if available
            )
            # Remove optimistic state update, rely on coordinator
            # self._attr_is_locked = False
            # self.async_write_ha_state()
            _LOGGER.info("Open command sent successfully for lock: %s", self.unique_id)
            # Optionally request a coordinator refresh if state updates aren't immediate via WebSocket
            # await self.coordinator.async_request_refresh()
        except ApiError as err:
            _LOGGER.error("Failed to open lock %s: %s", self.unique_id, err)
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error opening lock %s: %s", self.unique_id, err
            )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (might be same as open)."""
        # If unlock is different from open, implement here. Otherwise, call open.
        await self.async_open(**kwargs)

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""
        _LOGGER.info("Attempting to lock lock: %s", self.unique_id)
        home_id = self.coordinator.entry.data.get(
            "home_id"
        )  # Adjust if stored differently
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for lock %s", self.unique_id)
            return

        try:
            # Use coordinator's account object
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                module_id=self._module_id,
                state={"lock": True},  # Assuming this payload is correct
                bridge_id=bridge_id,  # Pass bridge_id if available
            )
            # Remove optimistic state update, rely on coordinator
            # self._attr_is_locked = True
            # self.async_write_ha_state()
            _LOGGER.info("Lock command sent successfully for lock: %s", self.unique_id)
            # Optionally request a coordinator refresh
            # await self.coordinator.async_request_refresh()
        except ApiError as err:
            _LOGGER.error("Failed to lock lock %s: %s", self.unique_id, err)
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error locking lock %s: %s", self.unique_id, err
            )
