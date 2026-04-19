"""Platform for binary sensor integration."""

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CALL_SENSOR_TIMEOUT,
    DOMAIN,
    SIGNAL_CALL_RECEIVED,
    SUBTYPE_DOORLOCK,
    SUBTYPE_EXTERNAL_UNIT,
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
    """Set up the BTicino binary sensor platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        _LOGGER.debug("Binary Sensor Setup: Found %d modules in coordinator data.", len(coordinator.data["modules"]))
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant")
            subtype = None
            _LOGGER.debug("Binary Sensor Setup: Checking module %s, Variant: %s", module_id, variant)
            if variant and ":" in variant:
                try:
                    subtype = variant.split(":", 1)[1]
                    _LOGGER.debug("Binary Sensor Setup: Extracted subtype: %s", subtype)
                except IndexError:
                    _LOGGER.warning(
                        "Could not parse subtype from variant '%s' for module %s",
                        variant,
                        module_id,
                    )
                    subtype = None

            if subtype == SUBTYPE_EXTERNAL_UNIT:
                _LOGGER.debug("Found external unit module (via variant subtype): %s", module_id)
                entities.append(BticinoCallBinarySensor(coordinator, module_id))
            elif subtype:
                _LOGGER.debug(
                    "Binary Sensor Setup: Module %s subtype '%s' did not match expected external unit subtype.",
                    module_id,
                    subtype,
                )
            elif not subtype and variant is not None:
                _LOGGER.debug(
                    "Binary Sensor Setup: Module %s has variant '%s' but failed to extract subtype.",
                    module_id,
                    variant,
                )

    # Add bridge "busy" sensor if bridge is available
    if coordinator.main_device_id:
        entities.append(BticinoBridgeBusySensor(coordinator))

    if not entities:
        _LOGGER.debug("No BTicino binary sensor modules found")

    cleanup_orphaned_entities(hass, entry.entry_id, "binary_sensor", entities)
    async_add_entities(entities)


class BticinoCallBinarySensor(BticinoEntity, BinarySensorEntity):
    """Representation of a BTicino call sensor."""

    _attr_device_class = BinarySensorDeviceClass.SOUND
    _attr_icon = "mdi:doorbell-video"

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the call sensor."""
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_call_{module_id}"
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        entity_name = module_data.get("name")

        self._associated_lock_ids: list[str] = []
        found_locks_data: list[dict[str, Any]] = []

        if self._bridge_id:
            for other_module_id, other_module_data in coordinator.data.get("modules", {}).items():
                other_variant = other_module_data.get("variant")
                other_subtype = None
                if other_variant and ":" in other_variant:
                    try:
                        other_subtype = other_variant.split(":", 1)[1]
                    except IndexError:
                        other_subtype = None

                if other_module_data.get("bridge") == self._bridge_id and other_subtype == SUBTYPE_DOORLOCK:
                    self._associated_lock_ids.append(other_module_id)
                    found_locks_data.append(other_module_data)

        if (not entity_name or entity_name == module_id) and len(found_locks_data) == 1:
            lock_name = found_locks_data[0].get("name")
            if lock_name and lock_name != found_locks_data[0].get("id"):
                entity_name = lock_name

        if not entity_name or entity_name == module_id:
            entity_name = "Call"

        self._attr_name = entity_name
        self._update_state()

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if not self.coordinator.last_update_success:
            return False
        return self._attr_available

    @property
    def is_on(self) -> bool:
        """Return true if the binary sensor is on."""
        if hasattr(self, "_attr_is_on") and self._attr_is_on is not None:
            return self._attr_is_on

        if not self.coordinator.data:
            return False

        events = self.coordinator.data.get("events_history", {}).get(self.coordinator.home_id, [])
        if not events:
            return False

        latest_event = events[0]
        return (
            latest_event.get("type") == "call"
            and not latest_event.get("end")
            and latest_event.get("module_id") == self._module_id
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return entity specific state attributes."""
        return self._attr_extra_state_attributes

    def _update_state(self) -> None:
        """Update the state of the binary sensor."""
        if not self.coordinator.data:
            self._attr_available = False
            return

        module_data = self.coordinator.data.get("modules", {}).get(self._module_id)
        if not module_data:
            self._attr_available = False
            self._attr_extra_state_attributes = {}
            return
        else:
            self._attr_available = module_data.get("reachable", True)

        attrs: dict[str, Any] = {
            "reachable": module_data.get("reachable"),
        }

        # Add last call info from event history
        events = self.coordinator.data.get("events_history", {}).get(self.coordinator.home_id, [])
        for event in events:
            if event.get("type") == "call" and event.get("module_id") == self._module_id:
                subevents = event.get("subevents")
                if subevents and isinstance(subevents, list) and len(subevents) > 0:
                    first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}
                    attrs["last_call_type"] = first_subevent.get("type")
                    attrs["last_call_time"] = format_timestamp_iso(first_subevent.get("time"))
                    attrs["last_call_message"] = first_subevent.get("message")
                    attrs["session_id"] = first_subevent.get("session_id")
                    snapshot_data = first_subevent.get("snapshot")
                    if isinstance(snapshot_data, dict):
                        attrs["snapshot_url"] = snapshot_data.get("url")
                break

        self._attr_extra_state_attributes = {k: v for k, v in attrs.items() if v is not None}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        super()._handle_coordinator_update()

    @callback
    def _handle_call_received(self, state: bool, module_id: str | None) -> None:
        """Handle the dispatcher signal for incoming calls."""
        if module_id == self._module_id:
            _LOGGER.debug("Handling call signal for %s: state=%s", self.entity_id, state)
            self._attr_is_on = state
            self.async_write_ha_state()

            if hasattr(self, "_turn_off_canceller") and self._turn_off_canceller:
                _LOGGER.debug("Cancelling previous turn-off timer for %s", self.entity_id)
                self._turn_off_canceller()
                self._turn_off_canceller = None

            if state:
                _LOGGER.debug("Setting turn-off timer for %s", self.entity_id)
                self._turn_off_canceller = async_call_later(self.hass, CALL_SENSOR_TIMEOUT, self._turn_off_callback)
        else:
            _LOGGER.debug(
                "Ignoring call signal for module %s on sensor %s",
                module_id,
                self.entity_id,
            )

    @callback
    def _turn_off_callback(self, *args: Any) -> None:
        """Turn the sensor off after timeout."""
        _LOGGER.debug("Executing turn-off callback for %s", self.entity_id)
        self._turn_off_canceller = None
        self._attr_is_on = False
        self.async_write_ha_state()
        self.coordinator.fire_call_timeout(self._module_id)

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_CALL_RECEIVED, self._handle_call_received))

    async def async_will_remove_from_hass(self) -> None:
        """Clean up when entity is removed."""
        if hasattr(self, "_turn_off_canceller") and self._turn_off_canceller:
            _LOGGER.debug("Cancelling turn-off timer for %s during removal", self.entity_id)
            self._turn_off_canceller()
            self._turn_off_canceller = None
        await super().async_will_remove_from_hass()


class BticinoBridgeBusySensor(CoordinatorEntity[BticinoIntercomCoordinator], BinarySensorEntity):
    """Indicates whether the intercom is busy (active call in progress)."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_has_entity_name = True
    _attr_icon = "mdi:phone-in-talk"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the busy sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_bridge_busy"
        self._attr_name = "Bridge Busy"
        self._attr_is_on = self._get_busy_state()

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the main bridge device."""
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}" if self.coordinator.home_name else "BTicino Intercom"
        )
        bridge_module_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id)
        model = bridge_module_data.get("type") if bridge_module_data else None
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)},
            name=device_name,
            manufacturer="BTicino",
            model=model,
        )

    def _get_busy_state(self) -> bool:
        """Get the busy state from coordinator data."""
        if not self.coordinator.data:
            return False
        bridge_data = self.coordinator.data.get("modules", {}).get(self.coordinator.main_device_id, {})
        return bool(bridge_data.get("busy", False))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._attr_is_on = self._get_busy_state()
        self.async_write_ha_state()
