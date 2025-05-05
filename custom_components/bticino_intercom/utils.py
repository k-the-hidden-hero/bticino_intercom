"""Utility functions for the BTicino integration."""

import logging
from datetime import datetime, timezone, timedelta
from homeassistant.util.dt import utc_from_timestamp

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
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.isoformat()
    if isinstance(timestamp, (int, float)) and timestamp > 0:
        try:
            return utc_from_timestamp(timestamp).isoformat()
        except (TypeError, ValueError):
            _LOGGER.warning(f"Could not convert timestamp {timestamp} to datetime")
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
        _LOGGER.warning(f"Uptime value {uptime_seconds} is too large to format.")
        return "Overflow"
