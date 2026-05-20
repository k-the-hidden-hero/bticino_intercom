"""Switch platform for BTicino Intercom."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

DEFAULT_SCHEDULE_DND = {
    "dnd_days": "1111111",
    "dnd_enabled": True,
    "dnd_start_time": "22:00",
    "dnd_mode": 2,
    "dnd_end_time": "06:00",
}

IMMEDIATE_DND = {
    "dnd_mode": 3,
    "dnd_enabled": True,
}

DND_CONFIG_KEYS = {
    "dnd_days",
    "dnd_duration",
    "dnd_enabled",
    "dnd_end_time",
    "dnd_end_time_duration",
    "dnd_mode",
    "dnd_start_time",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino Intercom switches from a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    entities: list[SwitchEntity] = []

    for home in coordinator.account.homes.values():
        for module in home.modules:
            if module.type == "BNC3":
                entities.append(
                    BticinoDoNotDisturbSwitch(
                        coordinator=coordinator,
                        home_id=home.id,
                        module_id=module.id,
                        module_type=module.type,
                        module_name=module.name,
                    )
                )

    async_add_entities(entities)


class BticinoDoNotDisturbSwitch(SwitchEntity):
    """Switch for immediate BTicino do-not-disturb mode."""

    _attr_icon = "mdi:bell-off"

    def __init__(
        self,
        coordinator: Any,
        home_id: str,
        module_id: str,
        module_type: str,
        module_name: str,
    ) -> None:
        """Initialize the switch."""

        self.coordinator = coordinator
        self.account = coordinator.account

        self.home_id = home_id
        self.module_id = module_id
        self.module_type = module_type

        self._attr_name = f"{module_name} Immediate Do Not Disturb"
        self._attr_unique_id = f"{home_id}_{module_id}_dnd_immediate"

        self._attr_is_on = False
        self._attr_available = True

        self._previous_dnd_config: dict[str, Any] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)}
        )

    @property
    def is_on(self) -> bool:
        """Return true if immediate do-not-disturb is active."""
        return self._attr_is_on

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return self._attr_available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "home_id": self.home_id,
            "module_id": self.module_id,
            "module_type": self.module_type,
            "has_previous_dnd_config": self._previous_dnd_config is not None,
        }

    async def async_added_to_hass(self) -> None:
        """Refresh state when entity is added."""
        await self._refresh_config()

    async def async_update(self) -> None:
        """Refresh state from BTicino config API."""
        await self._refresh_config()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable immediate do-not-disturb mode."""
        current_dnd = await self._get_current_dnd_config()

        if current_dnd is not None and not self._is_immediate_dnd(current_dnd):
            self._previous_dnd_config = deepcopy(current_dnd)

        await self._set_dnd(IMMEDIATE_DND)
        await self._refresh_config()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable immediate do-not-disturb mode and restore previous config."""

        restore_config = self._previous_dnd_config

        if restore_config is None:
            current_dnd = await self._get_current_dnd_config()

            if current_dnd is not None and not self._is_immediate_dnd(current_dnd):
                restore_config = current_dnd
            else:
                restore_config = DEFAULT_SCHEDULE_DND

        await self._set_dnd(restore_config)
        self._previous_dnd_config = None

        await self._refresh_config()

    async def _refresh_config(self) -> None:
        """Read current do-not-disturb config."""

        try:
            dnd = await self._get_current_dnd_config()

            if dnd is None:
                _LOGGER.warning(
                    "BTicino DND config for module %s not found",
                    self.module_id,
                )
                self._attr_available = False
                self.async_write_ha_state()
                return

            self._attr_is_on = self._is_immediate_dnd(dnd)
            self._attr_available = True
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.exception("Failed to refresh BTicino DND config: %s", err)
            self._attr_available = False
            self.async_write_ha_state()

    async def _get_current_dnd_config(self) -> dict[str, Any] | None:
        """Read current do-not-disturb config."""

        dnd = await self.account.async_get_do_not_disturb_config(
            home_id=self.home_id,
            module_id=self.module_id,
        )

        if not isinstance(dnd, dict):
            return None

        return self._sanitize_dnd_config(dnd)

    async def _set_dnd(self, dnd_config: dict[str, Any]) -> None:
        """Write do-not-disturb config."""

        result = await self.account.async_set_do_not_disturb_config(
            home_id=self.home_id,
            module_id=self.module_id,
            module_type=self.module_type,
            dnd_config=self._sanitize_dnd_config(dnd_config),
        )

        if result.get("status") != "ok":
            raise RuntimeError(f"Unexpected setconfigs response: {result}")

    @staticmethod
    def _is_immediate_dnd(dnd_config: dict[str, Any]) -> bool:
        """Return true if config means immediate do-not-disturb."""
        return (
            dnd_config.get("dnd_enabled") is True
            and dnd_config.get("dnd_mode") == 3
        )

    @staticmethod
    def _sanitize_dnd_config(dnd_config: dict[str, Any]) -> dict[str, Any]:
        """Keep only known do-not-disturb config keys."""
        return {
            key: value
            for key, value in dnd_config.items()
            if key in DND_CONFIG_KEYS
        }