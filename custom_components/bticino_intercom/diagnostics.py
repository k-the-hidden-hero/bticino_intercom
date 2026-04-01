"""Diagnostics support for BTicino Intercom."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACT_CONFIG = {CONF_USERNAME, CONF_PASSWORD}
REDACT_COORDINATOR = {"access_token", "refresh_token", "local_ipv4", "id"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    return {
        "config_entry": async_redact_data(entry.as_dict(), REDACT_CONFIG),
        "coordinator_data": async_redact_data(
            coordinator.data if coordinator.data else {},
            REDACT_COORDINATOR,
        ),
    }
