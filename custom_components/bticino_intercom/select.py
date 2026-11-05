"""Select platform for BTicino Intercom: the BNC3 ringtone mute mode (#71).

The Home + Security app offers three ringtone modes on a Classe 300X. They are
``dnd_mode`` values in the module's do-not-disturb config, as verified on the
device in #71:

    0  ringtone on
    1  temporarily muted, until ``dnd_end_time_duration`` (Unix ms)
    3  always muted

The app also has a schedule (mode 2 with days and times). Writing it was not
confirmed to work, so it is not offered; a device in that mode shows no option
and exposes the raw ``dnd_mode`` instead.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DEFAULT_DND_DURATION_MINUTES, DND_DURATION_STORE, DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)

OPTION_RINGTONE_ON = "ringtone_on"
OPTION_TEMPORARILY_MUTED = "temporarily_muted"
OPTION_ALWAYS_MUTED = "always_muted"

_MODE_BY_OPTION = {
    OPTION_RINGTONE_ON: 0,
    OPTION_TEMPORARILY_MUTED: 1,
    OPTION_ALWAYS_MUTED: 3,
}
_OPTION_BY_MODE = {mode: option for option, mode in _MODE_BY_OPTION.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ringtone mode select for every BNC3 module."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    durations = data.setdefault(DND_DURATION_STORE, {})
    ent_reg = er.async_get(hass)

    entities: list[SelectEntity] = []
    for home in coordinator.account.homes.values():
        for module in home.modules:
            if module.type != "BNC3":
                continue
            # 2.0.4-rc1 had an "Immediate Do Not Disturb" switch for the same
            # setting; this select replaces it.
            old_switch = ent_reg.async_get_entity_id(SWITCH_DOMAIN, DOMAIN, f"{home.id}_{module.id}_dnd_immediate")
            if old_switch:
                ent_reg.async_remove(old_switch)
            entities.append(
                BticinoDoNotDisturbSelect(
                    coordinator=coordinator,
                    home_id=home.id,
                    module_id=module.id,
                    module_type=module.type,
                    module_name=module.name,
                    duration_store=durations,
                )
            )

    async_add_entities(entities, update_before_add=True)


class BticinoDoNotDisturbSelect(SelectEntity):
    """Ringtone mode of a BNC3 indoor unit."""

    _attr_icon = "mdi:bell-cog-outline"
    _attr_translation_key = "do_not_disturb"

    def __init__(
        self,
        coordinator: Any,
        home_id: str,
        module_id: str,
        module_type: str,
        module_name: str,
        duration_store: dict[str, int],
    ) -> None:
        """Initialize the select."""
        self.coordinator = coordinator
        self.account = coordinator.account
        self.home_id = home_id
        self.module_id = module_id
        self.module_type = module_type
        self._duration_store = duration_store
        self._duration_key = f"{home_id}_{module_id}"
        self._dnd_config: dict[str, Any] = {}

        self._attr_options = list(_MODE_BY_OPTION)
        self._attr_name = f"{module_name} Do Not Disturb"
        self._attr_unique_id = f"{home_id}_{module_id}_dnd_mode"
        self._attr_current_option = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information for this entity."""
        return DeviceInfo(identifiers={(DOMAIN, self.coordinator.main_device_id)})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw mode, and when a temporary mute ends."""
        attributes: dict[str, Any] = {
            "home_id": self.home_id,
            "module_id": self.module_id,
            "module_type": self.module_type,
            "dnd_mode": self._dnd_config.get("dnd_mode"),
        }
        end_ms = self._dnd_config.get("dnd_end_time_duration")
        if self._attr_current_option == OPTION_TEMPORARILY_MUTED and isinstance(end_ms, int | float):
            attributes["muted_until"] = dt_util.utc_from_timestamp(end_ms / 1000).isoformat()
        return attributes

    async def async_update(self) -> None:
        """Read the mode from the module's do-not-disturb config."""
        try:
            dnd = await self.account.async_get_do_not_disturb_config(home_id=self.home_id, module_id=self.module_id)
        except Exception as err:
            if self._attr_available:
                _LOGGER.warning("Failed to read the do-not-disturb config of %s: %s", self.module_id, err)
            self._attr_available = False
            return

        if not isinstance(dnd, dict):
            if self._attr_available:
                _LOGGER.warning("No do-not-disturb config for module %s", self.module_id)
            self._attr_available = False
            return

        self._attr_available = True
        self._dnd_config = dnd
        self._attr_current_option = self._option_for(dnd)

    async def async_select_option(self, option: str) -> None:
        """Write the chosen mode."""
        dnd_config: dict[str, Any] = {"dnd_mode": _MODE_BY_OPTION[option], "dnd_enabled": True}
        if option == OPTION_TEMPORARILY_MUTED:
            minutes = self._duration_store.get(self._duration_key, DEFAULT_DND_DURATION_MINUTES)
            now_ms = int(dt_util.utcnow().timestamp() * 1000)
            dnd_config["dnd_duration"] = minutes
            dnd_config["dnd_end_time_duration"] = now_ms + minutes * 60_000

        result = await self.account.async_set_do_not_disturb_config(
            home_id=self.home_id,
            module_id=self.module_id,
            module_type=self.module_type,
            dnd_config=dnd_config,
        )
        if not isinstance(result, dict) or result.get("status") != "ok":
            raise HomeAssistantError(f"The intercom did not accept the do-not-disturb change: {result}")

        await self.async_update()
        self.async_write_ha_state()

    @staticmethod
    def _option_for(dnd: dict[str, Any]) -> str | None:
        """Return the option the config describes, or None for one not offered."""
        if dnd.get("dnd_enabled") is not True:
            return OPTION_RINGTONE_ON
        return _OPTION_BY_MODE.get(dnd.get("dnd_mode"))
