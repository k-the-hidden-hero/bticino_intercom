"""Switch platform for BTicino Intercom."""

from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES = 60

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

PROFESSIONAL_STUDIO_CONFIG_KEYS = {
    "ps_days",
    "ps_duration",
    "ps_enabled",
    "ps_end_time",
    "ps_end_time_duration",
    "ps_mode",
    "ps_start_time",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up BTicino Intercom switches from a config entry."""

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    professional_studio_duration_minutes = data.setdefault(
        "professional_studio_duration_minutes",
        {},
    )

    entities: list[SwitchEntity] = []

    for home in coordinator.account.homes.values():
        for module in home.modules:
            if module.type != "BNC3":
                continue

            entities.append(
                BticinoDoNotDisturbSwitch(
                    coordinator=coordinator,
                    home_id=home.id,
                    module_id=module.id,
                    module_type=module.type,
                    module_name=module.name,
                )
            )

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
                    BticinoProfessionalStudioSwitch(
                        coordinator=coordinator,
                        home_id=home.id,
                        module_id=module.id,
                        module_type=module.type,
                        module_name=module.name,
                        duration_store=professional_studio_duration_minutes,
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


class BticinoProfessionalStudioSwitch(SwitchEntity):
    """Switch for BTicino professional studio mode."""

    _attr_icon = "mdi:account-tie-voice"

    def __init__(
        self,
        coordinator: Any,
        home_id: str,
        module_id: str,
        module_type: str,
        module_name: str,
        duration_store: dict[str, int],
    ) -> None:
        """Initialize the switch."""

        self.coordinator = coordinator
        self.account = coordinator.account

        self.home_id = home_id
        self.module_id = module_id
        self.module_type = module_type
        self.duration_store = duration_store
        self.duration_key = f"{home_id}_{module_id}"

        self._attr_name = f"{module_name} Professional Studio"
        self._attr_unique_id = f"{home_id}_{module_id}_professional_studio"

        self._attr_is_on = False
        self._attr_available = True

        self._last_professional_studio_config: dict[str, Any] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.main_device_id)}
        )

    @property
    def is_on(self) -> bool:
        """Return true if professional studio mode is active."""
        return self._attr_is_on

    @property
    def available(self) -> bool:
        """Return entity availability."""
        return self._attr_available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        attrs: dict[str, Any] = {
            "home_id": self.home_id,
            "module_id": self.module_id,
            "module_type": self.module_type,
            "duration_minutes": self._duration_minutes,
        }

        if self._last_professional_studio_config is not None:
            attrs.update(
                {
                    "ps_enabled": self._last_professional_studio_config.get("ps_enabled"),
                    "ps_mode": self._last_professional_studio_config.get("ps_mode"),
                    "ps_end_time_duration": self._last_professional_studio_config.get(
                        "ps_end_time_duration"
                    ),
                }
            )

        return attrs

    @property
    def _duration_minutes(self) -> int:
        """Return configured duration in minutes."""
        value = self.duration_store.get(
            self.duration_key,
            DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES,
        )

        try:
            duration = int(value)
        except (TypeError, ValueError):
            duration = DEFAULT_PROFESSIONAL_STUDIO_DURATION_MINUTES

        return max(1, duration)

    async def async_added_to_hass(self) -> None:
        """Refresh state when entity is added."""
        await self._refresh_config()

    async def async_update(self) -> None:
        """Refresh state from BTicino config API."""
        await self._refresh_config()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable professional studio mode."""

        now_ms = int(time.time() * 1000)
        duration_minutes = self._duration_minutes

        professional_studio_config = {
            "ps_duration": 1,
            "ps_end_time_duration": now_ms + duration_minutes * 60 * 1000,
            "ps_enabled": True,
            "ps_mode": 1,
        }

        await self._set_professional_studio(professional_studio_config)
        await self._refresh_config()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable professional studio mode."""

        professional_studio_config = {
            "ps_mode": 0,
            "ps_enabled": True,
        }

        await self._set_professional_studio(professional_studio_config)
        await self._refresh_config()

    async def _refresh_config(self) -> None:
        """Read current professional studio config."""

        try:
            professional_studio = await self._get_current_professional_studio_config()

            if professional_studio is None:
                _LOGGER.warning(
                    "BTicino professional studio config for module %s not found",
                    self.module_id,
                )
                self._attr_available = False
                self.async_write_ha_state()
                return

            self._last_professional_studio_config = professional_studio

            if professional_studio.get("ps_enabled") is not True:
                self._attr_is_on = False
                self._attr_available = False
                self.async_write_ha_state()
                return

            self._attr_is_on = self._is_professional_studio_active(
                professional_studio
            )
            self._attr_available = True
            self.async_write_ha_state()

        except Exception as err:
            _LOGGER.exception(
                "Failed to refresh BTicino professional studio config: %s",
                err,
            )
            self._attr_available = False
            self.async_write_ha_state()

    async def _get_current_professional_studio_config(
        self,
    ) -> dict[str, Any] | None:
        """Read current professional studio config."""

        professional_studio = await self.account.async_get_professional_studio_config(
            home_id=self.home_id,
            module_id=self.module_id,
        )

        if not isinstance(professional_studio, dict):
            return None

        return self._sanitize_professional_studio_config(professional_studio)

    async def _set_professional_studio(
        self,
        professional_studio_config: dict[str, Any],
    ) -> None:
        """Write professional studio config."""

        result = await self.account.async_set_professional_studio_config(
            home_id=self.home_id,
            module_id=self.module_id,
            module_type=self.module_type,
            professional_studio_config=self._sanitize_professional_studio_config(
                professional_studio_config
            ),
        )

        if result.get("status") != "ok":
            raise RuntimeError(f"Unexpected setconfigs response: {result}")

    @staticmethod
    def _is_professional_studio_active(
        professional_studio_config: dict[str, Any],
    ) -> bool:
        """Return true if professional studio mode is active."""
        return (
            professional_studio_config.get("ps_enabled") is True
            and professional_studio_config.get("ps_mode") == 1
        )

    @staticmethod
    def _sanitize_professional_studio_config(
        professional_studio_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Keep only known professional studio config keys."""
        return {
            key: value
            for key, value in professional_studio_config.items()
            if key in PROFESSIONAL_STUDIO_CONFIG_KEYS
        }