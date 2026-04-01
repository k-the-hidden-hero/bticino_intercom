"""Platform for doorbell event integration."""

import logging
from typing import ClassVar

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CALL_RECEIVED, SUBTYPE_EXTERNAL_UNIT
from .coordinator import BticinoIntercomCoordinator
from .entity import BticinoEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino event platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    if coordinator.data and "modules" in coordinator.data:
        for module_id, module_data in coordinator.data["modules"].items():
            variant = module_data.get("variant", "")
            subtype = variant.split(":", 1)[1] if ":" in variant else None
            if subtype == SUBTYPE_EXTERNAL_UNIT:
                entities.append(BticinoDoorbellEvent(coordinator, module_id))

    async_add_entities(entities)


class BticinoDoorbellEvent(BticinoEntity, EventEntity):
    """Event entity for BTicino doorbell ring."""

    _attr_device_class = EventDeviceClass.DOORBELL
    _attr_event_types: ClassVar[list[str]] = ["ring"]
    _attr_icon = "mdi:doorbell-video"

    def __init__(self, coordinator: BticinoIntercomCoordinator, module_id: str) -> None:
        """Initialize the doorbell event entity."""
        super().__init__(coordinator, module_id)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_doorbell_event_{module_id}"
        module_data = coordinator.data.get("modules", {}).get(module_id, {})
        self._attr_name = module_data.get("name") or "Doorbell"

    @callback
    def _handle_call_received(self, state: bool, module_id: str | None) -> None:
        """Handle the dispatcher signal for incoming calls."""
        if module_id == self._module_id and state:
            _LOGGER.debug("Doorbell event triggered for %s", self.entity_id)
            self._trigger_event("ring", {"module_id": module_id})
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register for call events."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_CALL_RECEIVED,
                self._handle_call_received,
            )
        )
