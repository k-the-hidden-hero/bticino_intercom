"""Utility functions for the BTicino integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utc_from_timestamp

from .const import KNOWN_SUBTYPES, TYPE_TO_SUBTYPE

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity import Entity

_LOGGER = logging.getLogger(__name__)


def resolve_module_subtype(module_data: dict[str, Any]) -> str | None:
    """Return the canonical subtype for a module.

    The raw ``type`` (BNDL/BNEU/BNSL) is always present and identifies the
    module for the Classe 100X/300X and the Classe 300 EOS alike, so we map it
    directly via ``TYPE_TO_SUBTYPE``.

    The legacy ``variant`` field (``"<TYPE>:<subtype>"``, present only on the
    older models) is consulted purely as a forward-compat override: if it
    carries a subtype we do not yet know about, we honour it. A known variant is
    redundant with ``type`` and adds nothing.
    """
    subtype = TYPE_TO_SUBTYPE.get(module_data.get("type", ""))
    variant = module_data.get("variant")
    if variant and ":" in variant:
        variant_subtype = variant.split(":", 1)[1]
        if variant_subtype and variant_subtype not in KNOWN_SUBTYPES:
            return variant_subtype
    return subtype


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
