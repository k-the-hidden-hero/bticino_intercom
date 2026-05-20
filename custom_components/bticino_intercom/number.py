"""Number platform for BTicino Intercom."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES = 60
MIN_PROFESSIONAL_STUDIO_DURATION_MINUTES = 1
MAX_PROFESSIONAL_STUDIO_DURATION_MINUTES = 1440
STEP_PROFESSIONAL_STUDIO_DURATION_MINUTES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino Intercom number entities from a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    professional_studio_duration_minutes = data.setdefault(
        "professional_studio_duration_minutes",
        {},
    )

    entities: list[NumberEntity] = []

    for home in coordinator.account.homes.values():
        for module in home.modules:
            if module.type != "BNC3":
                continue

            professional_studio = await coordinator.account.async_get_professional_studio_config(
                home_id=home.id,
                module_id=module.id,
            )

            if (
                isinstance(professional_studio, dict)
                and professional_studio.get("ps_enabled") is True
            ):
                key = f"{home.id}_{module.id}"

                professional_studio_duration_minutes.setdefault(
                    key,
                    DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES,
                )

                entities.append(
                    BticinoProfessionalStudioDurationNumber(
                        coordinator=coordinator,
                        home_id=home.id,
                        module_id=module.id,
                        module_type=module.type,
                        module_name=module.name,
                        duration_store=professional_studio_duration_minutes,
                    )
                )

    async_add_entities(entities)


class BticinoProfessionalStudioDurationNumber(NumberEntity, RestoreEntity):
    """Number entity for professional studio duration."""

    _attr_icon = "mdi:timer-outline"
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_PROFESSIONAL_STUDIO_DURATION_MINUTES
    _attr_native_max_value = MAX_PROFESSIONAL_STUDIO_DURATION_MINUTES
    _attr_native_step = STEP_PROFESSIONAL_STUDIO_DURATION_MINUTES
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: Any,
        home_id: str,
        module_id: str,
        module_type: str,
        module_name: str,
        duration_store: dict[str, int],
    ) -> None:
        """Initialize the number entity."""

        self.coordinator = coordinator
        self.account = coordinator.account

        self.home_id = home_id
        self.module_id = module_id
        self.module_type = module_type
        self.duration_store = duration_store
        self.duration_key = f"{home_id}_{module_id}"

        self._attr_name = f"{module_name} Professional Studio Duration"
        self._attr_unique_id = f"{home_id}_{module_id}_professional_studio_duration"

        self._attr_native_value = self.duration_store.get(
            self.duration_key,
            DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES,
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)}
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "home_id": self.home_id,
            "module_id": self.module_id,
            "module_type": self.module_type,
        }

    async def async_added_to_hass(self) -> None:
        """Restore previous duration value."""

        restored_state = await self.async_get_last_state()

        if restored_state is None:
            self._set_duration_value(
                self.duration_store.get(
                    self.duration_key,
                    DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES,
                )
            )
            return

        try:
            restored_value = int(float(restored_state.state))
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Could not restore BTicino professional studio duration from %s",
                restored_state.state,
            )
            restored_value = DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES

        self._set_duration_value(restored_value)

    async def async_set_native_value(self, value: float) -> None:
        """Set professional studio duration."""

        self._set_duration_value(value)
        self.async_write_ha_state()

    def _set_duration_value(self, value: float | int) -> None:
        """Store duration value."""

        try:
            duration = int(value)
        except (TypeError, ValueError):
            duration = DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES

        duration = max(
            MIN_PROFESSIONAL_STUDIO_DURATION_MINUTES,
            min(MAX_PROFESSIONAL_STUDIO_DURATION_MINUTES, duration),
        )

        self.duration_store[self.duration_key] = duration
        self._attr_native_value = duration