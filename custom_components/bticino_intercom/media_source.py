"""Media Source provider for BTicino Intercom call history.

Exposes recorded snapshot/vignette images under the
``media-source://bticino_intercom/...`` URI scheme so users can browse past
events from the standard Home Assistant Media Browser.

Identifier layout:
    <entry_id>/<module_id>/<event_id>/<kind>

Where ``kind`` is ``snapshot`` or ``vignette``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .history import IMAGE_KINDS, EventHistoryStore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

MIME_TYPE = "image/jpeg"
SOURCE_NAME = "BTicino Intercom"


def _fmt_timestamp(ts: int | None) -> str:
    """Format a unix timestamp for display."""
    if not ts:
        return "unknown time"
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return "unknown time"


def _event_title(event: dict) -> str:
    """Build a human-readable title for an event."""
    module = event.get("module_name") or event.get("module_id") or "module"
    state = event.get("event_type") or "incoming_call"
    return f"{_fmt_timestamp(event.get('timestamp'))} — {module} ({state})"


@callback
def _get_store(hass: HomeAssistant, entry_id: str) -> EventHistoryStore | None:
    """Return the history store for a given config entry id, if available."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return None
    return entry_data.get("history")


async def async_get_media_source(hass: HomeAssistant) -> BticinoMediaSource:
    """Set up the BTicino media source."""
    return BticinoMediaSource(hass)


class BticinoMediaSource(MediaSource):
    """Provide BTicino call events as a media source."""

    name: str = SOURCE_NAME

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item identifier to a playable URL."""
        parts = (item.identifier or "").split("/")
        if len(parts) != 4:
            raise Unresolvable(f"Invalid BTicino media identifier: {item.identifier!r}")
        entry_id, _module_id, event_id, kind = parts
        if kind not in IMAGE_KINDS:
            raise Unresolvable(f"Unknown image kind: {kind!r}")

        store = _get_store(self.hass, entry_id)
        if store is None:
            raise Unresolvable(f"No history store available for entry {entry_id!r}")
        path = store.resolve_image_path(event_id, kind)
        if path is None or not path.is_file():
            raise Unresolvable(f"Image not found: {item.identifier!r}")

        url = BticinoHistoryImageView.build_url(entry_id, event_id, kind)
        return PlayMedia(url=url, mime_type=MIME_TYPE, path=path)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse stored events for all configured entries."""
        identifier = item.identifier or ""
        parts = [p for p in identifier.split("/") if p]
        if not parts:
            return self._browse_root()
        if len(parts) == 1:
            return self._browse_entry(parts[0])
        if len(parts) == 2:
            return self._browse_module(parts[0], parts[1])
        if len(parts) == 3:
            return self._browse_event(parts[0], parts[1], parts[2])
        raise Unresolvable(f"Cannot browse identifier {identifier!r}")

    # ------------------------------------------------------------------
    # Browse helpers
    # ------------------------------------------------------------------

    def _browse_root(self) -> BrowseMediaSource:
        """List all configured BTicino entries that have a history store."""
        entries: dict[str, dict] = self.hass.data.get(DOMAIN, {})
        children: list[BrowseMediaSource] = []
        for entry_id, entry_data in entries.items():
            store: EventHistoryStore | None = entry_data.get("history")
            if store is None:
                continue
            config_entry = self._get_config_entry(entry_id)
            title = (config_entry.title if config_entry else entry_id) or entry_id
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=entry_id,
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.IMAGE,
                    title=title,
                    can_play=False,
                    can_expand=True,
                    children_media_class=MediaClass.DIRECTORY,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=SOURCE_NAME,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_entry(self, entry_id: str) -> BrowseMediaSource:
        """List modules that have recorded events for an entry."""
        store = _get_store(self.hass, entry_id)
        if store is None:
            raise Unresolvable(f"Entry {entry_id!r} not loaded")

        children: list[BrowseMediaSource] = []
        for module_id in store.list_modules():
            sample = next(
                (e for e in store.list_events() if e.get("module_id") == module_id),
                None,
            )
            title = (sample or {}).get("module_name") or module_id
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{module_id}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.IMAGE,
                    title=title,
                    can_play=False,
                    can_expand=True,
                    children_media_class=MediaClass.DIRECTORY,
                )
            )
        config_entry = self._get_config_entry(entry_id)
        title = (config_entry.title if config_entry else entry_id) or entry_id
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=entry_id,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    def _browse_module(self, entry_id: str, module_id: str) -> BrowseMediaSource:
        """List recorded events for a module."""
        store = _get_store(self.hass, entry_id)
        if store is None:
            raise Unresolvable(f"Entry {entry_id!r} not loaded")
        events = store.list_events(module_id=module_id)
        children: list[BrowseMediaSource] = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"{entry_id}/{module_id}/{event['event_id']}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.IMAGE,
                title=_event_title(event),
                can_play=False,
                can_expand=True,
                thumbnail=BticinoHistoryImageView.build_url(entry_id, event["event_id"], "vignette")
                if event.get("vignette_file")
                else None,
                children_media_class=MediaClass.IMAGE,
            )
            for event in events
        ]
        module_name = (events[0].get("module_name") if events else None) or module_id
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{module_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=module_name,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.IMAGE,
        )

    def _browse_event(self, entry_id: str, module_id: str, event_id: str) -> BrowseMediaSource:
        """List snapshot/vignette images for a single event."""
        store = _get_store(self.hass, entry_id)
        if store is None:
            raise Unresolvable(f"Entry {entry_id!r} not loaded")
        event = store.get_event(event_id)
        if event is None:
            raise Unresolvable(f"Event {event_id!r} not found")

        children: list[BrowseMediaSource] = []
        for kind in IMAGE_KINDS:
            if not event.get(f"{kind}_file"):
                continue
            url = BticinoHistoryImageView.build_url(entry_id, event_id, kind)
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"{entry_id}/{module_id}/{event_id}/{kind}",
                    media_class=MediaClass.IMAGE,
                    media_content_type=MIME_TYPE,
                    title=kind.capitalize(),
                    can_play=True,
                    can_expand=False,
                    thumbnail=url,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"{entry_id}/{module_id}/{event_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.IMAGE,
            title=_event_title(event),
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.IMAGE,
        )

    def _get_config_entry(self, entry_id: str) -> ConfigEntry | None:
        """Fetch the config entry object from HA, if still present."""
        return self.hass.config_entries.async_get_entry(entry_id)


class BticinoHistoryImageView(HomeAssistantView):
    """Serve history images from disk with HA authentication."""

    url = "/api/bticino_intercom/image/{entry_id}/{event_id}/{kind}"
    name = "api:bticino_intercom:history_image"

    @staticmethod
    def build_url(entry_id: str, event_id: str, kind: str) -> str:
        """Return the HTTP URL for a given history image."""
        return f"/api/bticino_intercom/image/{entry_id}/{event_id}/{kind}"

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        event_id: str,
        kind: str,
    ) -> web.Response:
        """Return the stored image bytes."""
        hass: HomeAssistant = request.app["hass"]
        if kind not in IMAGE_KINDS:
            raise web.HTTPBadRequest
        store = _get_store(hass, entry_id)
        if store is None:
            raise web.HTTPNotFound
        path = store.resolve_image_path(event_id, kind)
        if path is None:
            raise web.HTTPNotFound

        def _exists() -> bool:
            return path.is_file()

        if not await hass.async_add_executor_job(_exists):
            raise web.HTTPNotFound
        return web.FileResponse(path=path, headers={"Content-Type": MIME_TYPE})
