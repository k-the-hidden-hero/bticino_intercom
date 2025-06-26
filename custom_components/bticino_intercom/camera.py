"""Platform for camera integration."""

import logging
from typing import Any, Optional
from datetime import datetime

import aiohttp
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.dt import utcnow, utc_from_timestamp

from .const import DOMAIN, IMAGE_CACHE_SECONDS
from .coordinator import BticinoIntercomCoordinator
from .utils import format_timestamp_iso

_LOGGER = logging.getLogger(__name__)

# How long to cache the image locally after fetching (seconds)
# Prevents re-downloading the same image repeatedly between coordinator updates


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BTicino camera platform."""
    coordinator: BticinoIntercomCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    # Only add cameras if the bridge is identified
    if not coordinator._main_device_id:
        _LOGGER.warning("No bridge device ID found, cannot set up cameras.")
        return

    entities = [
        BticinoSnapshotCamera(coordinator),
        BticinoVignetteCamera(coordinator),
    ]
    async_add_entities(entities)


class BticinoBaseEventCamera(CoordinatorEntity[BticinoIntercomCoordinator], Camera):
    """Base class for cameras showing the latest event image."""

    _attr_entity_registry_enabled_default = True  # Changed to True
    _attr_has_entity_name = True
    _attr_supported_features = CameraEntityFeature(0)  # No streaming, no controls
    _unrecorded_attributes = frozenset(
        {"image_url", "expires_at_iso", "event_time_iso"}
    )

    def __init__(
        self,
        coordinator: BticinoIntercomCoordinator,
        image_type: str,  # 'snapshot' or 'vignette'
    ) -> None:
        """Initialize the base event camera."""
        super().__init__(coordinator)
        Camera.__init__(self)  # Initialize base Camera class
        self._image_type = image_type
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_{image_type}"
        # Set name in subclasses
        self._image_url: Optional[str] = None
        self._image_expires_at: Optional[datetime] = None
        self._event_time: Optional[datetime] = None
        self._cached_image: Optional[bytes] = None
        self._cached_image_time: Optional[datetime] = None
        self._update_state()  # Initial update

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info linking to the main bridge device."""
        # Assert that bridge_id exists because we check in setup_entry
        assert self.coordinator._main_device_id is not None
        device_name = (
            f"BTicino Intercom - {self.coordinator.home_name}"
            if self.coordinator.home_name
            else "BTicino Intercom"
        )
        # Extract bridge module data to potentially get model info
        bridge_module_data = self.coordinator.data.get("modules", {}).get(
            self.coordinator._main_device_id
        )
        model = bridge_module_data.get("type") if bridge_module_data else None

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator._main_device_id)},
            name=device_name,  # Use dynamic name
            manufacturer="BTicino",
            model=model,  # Add model if available
        )

    @property
    def available(self) -> bool:
        """Return True if the coordinator succeeded and we have an image URL."""
        return self.coordinator.last_update_success and self._image_url is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        attrs = {
            "image_url": self._image_url,
            "expires_at_iso": format_timestamp_iso(self._image_expires_at),
            "event_time_iso": format_timestamp_iso(self._event_time),
        }
        return {k: v for k, v in attrs.items() if v is not None}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        # Clear cache if the underlying image URL changed
        # (We check the URL itself, not just the timestamp, in case the same event
        # somehow gets processed twice but with a new URL)
        # We need to get the NEW url from the coordinator state update first
        new_url = self._get_image_url_from_coordinator()
        if new_url != self._image_url:
            _LOGGER.debug("Image URL changed for %s, clearing cache.", self.entity_id)
            self._cached_image = None
            self._cached_image_time = None
            # Update internal state with new URL etc AFTER clearing cache
            self._update_state_internal()

        self.async_write_ha_state()

    def _get_image_url_from_coordinator(self) -> Optional[str]:
        """Helper to extract the correct image URL from coordinator data."""
        image_url = None
        last_event = self.coordinator.data.get("last_event")
        if not last_event:
            events = self.coordinator.data.get("events_history", {}).get(
                self.coordinator.home_id, []
            )
            last_event = events[0] if events else None

        if last_event:
            subevents = last_event.get("subevents")
            if subevents and isinstance(subevents, list) and len(subevents) > 0:
                first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}
                image_data = first_subevent.get(
                    self._image_type
                )  # 'snapshot' or 'vignette'
                if isinstance(image_data, dict):
                    image_url = image_data.get("url")
        return image_url

    def _update_state_internal(self) -> None:
        """Update internal state variables from coordinator data."""
        image_url = None
        expires_at_ts = None
        event_time_ts = None

        last_event = self.coordinator.data.get("last_event")
        if not last_event:
            events = self.coordinator.data.get("events_history", {}).get(
                self.coordinator.home_id, []
            )
            last_event = events[0] if events else None

        if last_event:
            event_time_ts = last_event.get("time") or last_event.get("timestamp")
            subevents = last_event.get("subevents")
            if subevents and isinstance(subevents, list) and len(subevents) > 0:
                first_subevent = subevents[0] if isinstance(subevents[0], dict) else {}
                event_time_ts = (
                    first_subevent.get("time") or event_time_ts
                )  # Prioritize subevent time
                image_data = first_subevent.get(self._image_type)
                if isinstance(image_data, dict):
                    image_url = image_data.get("url")
                    expires_at_ts = image_data.get("expires_at")

        self._image_url = image_url
        self._image_expires_at = (
            utc_from_timestamp(expires_at_ts)
            if isinstance(expires_at_ts, (int, float)) and expires_at_ts > 0
            else None
        )
        self._event_time = (
            utc_from_timestamp(event_time_ts)
            if isinstance(event_time_ts, (int, float)) and event_time_ts > 0
            else None
        )

    def _update_state(self) -> None:
        """Update internal state from coordinator data."""
        self._update_state_internal()

    async def async_camera_image(
        self, width: Optional[int] = None, height: Optional[int] = None
    ) -> Optional[bytes]:
        """Return bytes of camera image."""
        now = utcnow()
        _LOGGER.debug("%s: Camera image requested.", self.entity_id)

        # Return cached image if valid and not expired
        if (
            self._cached_image is not None
            and self._cached_image_time is not None
            and (now - self._cached_image_time).total_seconds() < IMAGE_CACHE_SECONDS
        ):
            _LOGGER.debug("%s: Returning cached image.", self.entity_id)
            return self._cached_image
        elif self._cached_image is not None:
            _LOGGER.debug(
                "%s: Cache expired or invalid (time: %s).",
                self.entity_id,
                self._cached_image_time,
            )

        # Check if the image URL is present and not expired
        if not self._image_url:
            _LOGGER.warning("%s: No image URL available.", self.entity_id)
            return None
        if self._image_expires_at:
            _LOGGER.debug(
                "%s: Current time: %s, Image expires at: %s",
                self.entity_id,
                now,
                self._image_expires_at,
            )
            if now >= self._image_expires_at:
                _LOGGER.warning("%s: Image URL has expired.", self.entity_id)
                return None
        else:
            _LOGGER.warning("%s: Image expiration time is unknown.", self.entity_id)
            # Decide whether to proceed without expiration check or return None
            # Let's proceed for now, but log warning

        _LOGGER.debug(
            "%s: Fetching new image from URL: %s", self.entity_id, self._image_url
        )
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self._image_url) as response:
                _LOGGER.debug(
                    "%s: Received response status: %s", self.entity_id, response.status
                )
                response.raise_for_status()  # Raise an exception for bad status codes
                image_bytes = await response.read()
                # Cache the fetched image
                self._cached_image = image_bytes
                self._cached_image_time = now
                _LOGGER.info(
                    "%s: Successfully fetched and cached image (%d bytes).",
                    self.entity_id,
                    len(image_bytes),
                )
                return image_bytes
        except aiohttp.ClientError as err:
            _LOGGER.error("%s: Error fetching camera image: %s", self.entity_id, err)
            return None
        except Exception as err:
            _LOGGER.error(
                "Unexpected error fetching camera image for %s: %s", self.entity_id, err
            )
            return None


class BticinoSnapshotCamera(BticinoBaseEventCamera):
    """Representation of the Last Event Snapshot Camera."""

    _attr_name = "Last Event Snapshot"

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the snapshot camera."""
        super().__init__(coordinator, image_type="snapshot")


class BticinoVignetteCamera(BticinoBaseEventCamera):
    """Representation of the Last Event Vignette Camera."""

    _attr_name = "Last Event Vignette"

    def __init__(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Initialize the vignette camera."""
        super().__init__(coordinator, image_type="vignette")
