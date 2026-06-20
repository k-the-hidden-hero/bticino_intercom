"""Utility functions for the BTicino integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utc_from_timestamp

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)


def format_timestamp_iso(timestamp: int | float | datetime | None) -> str | None:
    """Convert various timestamp formats to ISO 8601 string.

    Args:
        timestamp: The timestamp to convert. Can be a datetime object, int/float (Unix timestamp),
                 or None.

    Returns:
        str | None: ISO 8601 formatted string if conversion successful, None otherwise.
    """
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        return timestamp.isoformat()
    if isinstance(timestamp, int | float) and timestamp > 0:
        try:
            return utc_from_timestamp(timestamp).isoformat()
        except (TypeError, ValueError):
            _LOGGER.warning("Could not convert timestamp %s to datetime", timestamp)
            return None
    return None


def format_uptime_readable(uptime_seconds: int | None) -> str | None:
    """Convert uptime in seconds to a human-readable string.

    Args:
        uptime_seconds: The uptime in seconds to convert.

    Returns:
        str | None: Human-readable string (e.g. "1 day, 2:30:45") if conversion successful,
                   None if input is None or negative, "Overflow" if value is too large.
    """
    if uptime_seconds is None or uptime_seconds < 0:
        return None
    try:
        return str(timedelta(seconds=uptime_seconds))
    except OverflowError:
        _LOGGER.warning("Uptime value %s is too large to format.", uptime_seconds)
        return "Overflow"


def cleanup_orphaned_entities(
    hass: HomeAssistant,
    entry_id: str,
    domain: str,
    current_entities: list[Entity],
) -> None:
    """Remove registered entities that are no longer created by the platform.

    Call this after building the entity list but before async_add_entities,
    so that stale entities from previous versions are automatically cleaned up.
    """
    current_unique_ids = {e.unique_id for e in current_entities}
    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry_id):
        if reg_entry.domain == domain and reg_entry.unique_id not in current_unique_ids:
            _LOGGER.info("Removing orphaned %s entity %s (%s)", domain, reg_entry.entity_id, reg_entry.unique_id)
            ent_reg.async_remove(reg_entry.entity_id)
