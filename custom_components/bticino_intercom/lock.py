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
            if module_data.get("type") == "BNDL":  # BTicino Door Lock module type
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

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator)
        self._module_id = module_id
        module_data = coordinator.data.get("modules", {}).get(module_id, {})

        self._attr_unique_id = f"{DOMAIN}_{module_id}_lock"
        self._attr_name = module_data.get("name") or f"Lock {module_id}"

        bridge_module_id = module_data.get("bridge")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, module_id)},
            name=self._attr_name,
            manufacturer="BTicino",
            model=module_data.get("type", "BNDL"),
            via_device=(DOMAIN, bridge_module_id) if bridge_module_id else None,
            # sw_version and hw_version are now populated by the coordinator device registration
        )
        # Initialize state from coordinator
        self._update_state()

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
            "appliance_type": module_data.get("appliance_type"),  # Added
            "local_ipv4": module_data.get(
                "local_ipv4"
            ),  # Added (likely only on bridge)
            "wifi_strength": module_data.get(
                "wifi_strength"
            ),  # Added (likely only on bridge)
            "uptime": module_data.get("uptime"),  # Added
        }
        # Filter out None values
        return {k: v for k, v in attrs.items() if v is not None}

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

        # Update availability based on reachability if available
        self._attr_available = module_data.get("reachable", True)

        # Update is_locked based on the 'lock' state (assuming boolean)
        # Default to True (locked) if 'lock' key is missing
        self._attr_is_locked = module_data.get("lock", True)

    @property
    def is_locking(self) -> bool | None:
        """Return true if lock is locking."""
        return None  # Not supported

    @property
    def is_unlocking(self) -> bool | None:
        """Return true if lock is unlocking."""
        return None  # Not supported

    @property
    def is_jammed(self) -> bool | None:
        """Return true if lock is jammed."""
        # Update if coordinator provides this info, e.g., based on a specific status
        return False  # Default to False

    async def async_open(self, **kwargs: Any) -> None:
        """Open the door latch."""
        _LOGGER.info("Attempting to open lock: %s", self.unique_id)
        home_id = self.coordinator.entry.data.get("home_id")
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for lock %s", self.unique_id)
            return

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                module_id=self._module_id,
                state={"lock": False},  # Assuming this payload is correct
                bridge_id=bridge_id,
            )
            _LOGGER.info("Open command sent successfully for lock: %s", self.unique_id)
            # Request a refresh to get updated state sooner if needed
            await self.coordinator.async_request_refresh()
        except ApiError as err:
            _LOGGER.error("Failed to open lock %s: %s", self.unique_id, err)
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error opening lock %s: %s", self.unique_id, err
            )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door (might be same as open)."""
        await self.async_open(**kwargs)

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""
        _LOGGER.info("Attempting to lock lock: %s", self.unique_id)
        home_id = self.coordinator.entry.data.get("home_id")
        module_data = self.coordinator.data.get("modules", {}).get(self._module_id, {})
        bridge_id = module_data.get("bridge")

        if not home_id:
            _LOGGER.error("Home ID not found for lock %s", self.unique_id)
            return

        try:
            await self.coordinator.account.async_set_module_state(
                home_id=home_id,
                module_id=self._module_id,
                state={"lock": True},  # Assuming this payload is correct
                bridge_id=bridge_id,
            )
            _LOGGER.info("Lock command sent successfully for lock: %s", self.unique_id)
            # Request a refresh to get updated state sooner if needed
            await self.coordinator.async_request_refresh()
        except ApiError as err:
            _LOGGER.error("Failed to lock lock %s: %s", self.unique_id, err)
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error locking lock %s: %s", self.unique_id, err
            )
