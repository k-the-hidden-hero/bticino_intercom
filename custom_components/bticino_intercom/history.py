"""Event history storage for the BTicino Intercom integration.

Stores call events (incoming_call / terminated / answered_elsewhere) together
with their snapshot and vignette images on disk. Images are downloaded
immediately when the WebSocket push arrives (Azure SAS URLs expire quickly),
so the history remains viewable even after the signed URLs become invalid.

Storage layout:
    <config>/bticino_intercom/events/<entry_id>/<module_id>/<event_id>_snapshot.jpg
    <config>/bticino_intercom/events/<entry_id>/<module_id>/<event_id>_vignette.jpg

Metadata is persisted via ``helpers.storage.Store`` under the key
``bticino_intercom.history.<entry_id>`` with atomic writes.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    HISTORY_DEFAULT_MAX_EVENTS,
    HISTORY_DEFAULT_RETENTION_DAYS,
    HISTORY_MAX_MAX_EVENTS,
    HISTORY_MAX_RETENTION_DAYS,
    HISTORY_STORAGE_VERSION,
    OPT_HISTORY_ENABLED,
    OPT_HISTORY_MAX_EVENTS,
    OPT_HISTORY_RETENTION_DAYS,
)

_LOGGER = logging.getLogger(__name__)

IMAGE_KINDS: tuple[str, ...] = ("snapshot", "vignette")
_DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=15)


def _now_ts() -> int:
    """Return current UTC timestamp as int."""
    return int(datetime.now(UTC).timestamp())


def _clamp(value: int, low: int, high: int) -> int:
    """Clamp ``value`` into ``[low, high]``."""
    return max(low, min(high, value))


class EventHistoryStore:
    """Persist doorbell events with their associated images."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store for a specific config entry."""
        self.hass = hass
        self.entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass,
            HISTORY_STORAGE_VERSION,
            f"{DOMAIN}.history.{entry_id}",
        )
        self._root: Path = Path(hass.config.path(DOMAIN, "events", entry_id))
        self._events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._loaded = False

    # ---------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load persisted events and ensure storage directory exists."""
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict):
            events = data.get("events")
            if isinstance(events, list):
                self._events = [e for e in events if isinstance(e, dict)]
        await self.hass.async_add_executor_job(self._ensure_root)
        self._loaded = True
        _LOGGER.debug("Loaded %d historical events for entry %s", len(self._events), self.entry_id)

    async def async_unload(self) -> None:
        """Flush pending writes (no-op beyond the Store's own semantics)."""
        # Store.async_save() is already awaited on each mutation; nothing to do.

    # ---------------------------------------------------------------------
    # Queries
    # ---------------------------------------------------------------------

    def list_events(
        self,
        *,
        limit: int | None = None,
        module_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return a copy of stored events, most recent first."""
        items = self._events
        if module_id is not None:
            items = [e for e in items if e.get("module_id") == module_id]
        if limit is not None:
            items = items[:limit]
        return [dict(e) for e in items]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        """Return a single event by id."""
        for event in self._events:
            if event.get("event_id") == event_id:
                return dict(event)
        return None

    def resolve_image_path(self, event_id: str, image_kind: str) -> Path | None:
        """Return the absolute path of the stored image, or None."""
        if image_kind not in IMAGE_KINDS:
            return None
        event = self.get_event(event_id)
        if not event:
            return None
        rel = event.get(f"{image_kind}_file")
        if not rel:
            return None
        path = self._root / rel
        try:
            path.resolve().relative_to(self._root.resolve())
        except (ValueError, OSError):
            _LOGGER.warning("Rejected out-of-root image path for %s/%s", event_id, image_kind)
            return None
        return path

    def list_modules(self) -> list[str]:
        """Return the distinct module ids that have recorded events."""
        seen: dict[str, None] = {}
        for event in self._events:
            mod = event.get("module_id")
            if mod and mod not in seen:
                seen[mod] = None
        return list(seen.keys())

    # ---------------------------------------------------------------------
    # Mutations
    # ---------------------------------------------------------------------

    async def async_record_call(
        self,
        *,
        event_id: str,
        module_id: str,
        module_name: str | None,
        snapshot_url: str | None,
        vignette_url: str | None,
        event_timestamp: int | None = None,
    ) -> dict[str, Any]:
        """Record an incoming call and download its images (if provided).

        If an event with the same ``event_id`` already exists, it is updated
        in place (new URLs replace missing ones) rather than duplicated.
        """
        async with self._lock:
            record = next((e for e in self._events if e.get("event_id") == event_id), None)
            created = record is None
            if record is None:
                record = {
                    "event_id": event_id,
                    "module_id": module_id,
                    "module_name": module_name,
                    "event_type": "incoming_call",
                    "timestamp": event_timestamp or _now_ts(),
                    "ended_at": None,
                    "answered": None,
                    "snapshot_file": None,
                    "vignette_file": None,
                }
                self._events.insert(0, record)
            else:
                if module_name and not record.get("module_name"):
                    record["module_name"] = module_name

            urls = {"snapshot": snapshot_url, "vignette": vignette_url}
            for kind, url in urls.items():
                if not url or record.get(f"{kind}_file"):
                    continue
                stored = await self._download_image(module_id, event_id, kind, url)
                if stored:
                    record[f"{kind}_file"] = stored

            await self._persist_locked()
            if created:
                _LOGGER.info(
                    "Recorded call event %s (module=%s, snapshot=%s, vignette=%s)",
                    event_id,
                    module_id,
                    bool(record.get("snapshot_file")),
                    bool(record.get("vignette_file")),
                )
            return dict(record)

    async def async_close_call(
        self,
        *,
        event_id: str,
        event_type: str,
        ended_at: int | None = None,
    ) -> None:
        """Mark an existing event as closed (terminated / answered_elsewhere)."""
        async with self._lock:
            record = next((e for e in self._events if e.get("event_id") == event_id), None)
            if record is None:
                _LOGGER.debug("async_close_call: no record for event_id=%s", event_id)
                return
            record["event_type"] = event_type
            record["ended_at"] = ended_at or _now_ts()
            record["answered"] = event_type == "answered_elsewhere"
            await self._persist_locked()

    async def async_apply_retention(
        self,
        *,
        retention_days: int,
        max_events: int,
    ) -> int:
        """Drop events older than retention window or beyond count cap.

        Returns the number of purged events.
        """
        retention_days = _clamp(retention_days, 1, HISTORY_MAX_RETENTION_DAYS)
        max_events = _clamp(max_events, 1, HISTORY_MAX_MAX_EVENTS)
        cutoff = int((datetime.now(UTC) - timedelta(days=retention_days)).timestamp())

        async with self._lock:
            sorted_events = sorted(
                self._events,
                key=lambda e: e.get("timestamp") or 0,
                reverse=True,
            )
            keep: list[dict[str, Any]] = []
            drop: list[dict[str, Any]] = []
            for event in sorted_events:
                ts = event.get("timestamp") or 0
                if ts < cutoff or len(keep) >= max_events:
                    drop.append(event)
                else:
                    keep.append(event)
            if not drop:
                return 0
            self._events = keep
            for event in drop:
                await self._delete_event_files(event)
            await self._persist_locked()

        _LOGGER.info("Purged %d historical events beyond retention", len(drop))
        return len(drop)

    async def async_clear(self) -> None:
        """Remove every persisted event and all files."""
        async with self._lock:
            self._events = []
            await self._store.async_save({"events": []})
            await self.hass.async_add_executor_job(self._rmtree, self._root)
            await self.hass.async_add_executor_job(self._ensure_root)

    def _ensure_root(self) -> None:
        """Create the storage directory if it does not exist."""
        self._root.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------------

    async def _persist_locked(self) -> None:
        """Persist the in-memory list (caller must hold ``self._lock``)."""
        await self._store.async_save({"events": self._events})

    async def _download_image(
        self,
        module_id: str,
        event_id: str,
        kind: str,
        url: str,
    ) -> str | None:
        """Download an image and return its path relative to ``_root``."""
        session = async_get_clientsession(self.hass)
        safe_event = event_id.replace("/", "_").replace("\\", "_")
        safe_module = module_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        rel = f"{safe_module}/{safe_event}_{kind}.jpg"
        target = self._root / rel
        try:
            async with session.get(url, timeout=_DOWNLOAD_TIMEOUT) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "Failed to download %s for event %s: HTTP %s",
                        kind,
                        event_id,
                        resp.status,
                    )
                    return None
                data = await resp.read()
        except (TimeoutError, aiohttp.ClientError) as err:
            _LOGGER.warning("Error downloading %s for event %s: %s", kind, event_id, err)
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            # Defensive catch-all: the coordinator fires this as a
            # background task, and unhandled exceptions there surface as
            # noisy warnings without affecting the call flow. Downloads
            # are best-effort.
            _LOGGER.warning("Unexpected error downloading %s for event %s", kind, event_id, exc_info=True)
            return None

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

        try:
            await self.hass.async_add_executor_job(_write)
        except OSError as err:
            _LOGGER.warning("Error writing %s for event %s: %s", kind, event_id, err)
            return None
        _LOGGER.debug("Stored %s image for event %s (%d bytes)", kind, event_id, len(data))
        return rel

    async def _delete_event_files(self, event: dict[str, Any]) -> None:
        """Remove image files for a single event (best effort)."""
        paths = [event.get(f"{kind}_file") for kind in IMAGE_KINDS]
        abs_paths = [self._root / p for p in paths if p]
        if not abs_paths:
            return
        await self.hass.async_add_executor_job(self._unlink_many, abs_paths)

    @staticmethod
    def _unlink_many(paths: list[Path]) -> None:
        """Remove files, ignoring missing ones."""
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                _LOGGER.debug("Could not remove %s", path, exc_info=True)

    @staticmethod
    def _rmtree(path: Path) -> None:
        """Recursively remove a directory, ignoring missing."""
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)


def get_history_options(options: dict[str, Any]) -> tuple[bool, int, int]:
    """Return (enabled, retention_days, max_events) from entry options."""
    enabled = bool(options.get(OPT_HISTORY_ENABLED, True))
    retention = _clamp(
        int(options.get(OPT_HISTORY_RETENTION_DAYS, HISTORY_DEFAULT_RETENTION_DAYS)),
        1,
        HISTORY_MAX_RETENTION_DAYS,
    )
    max_events = _clamp(
        int(options.get(OPT_HISTORY_MAX_EVENTS, HISTORY_DEFAULT_MAX_EVENTS)),
        1,
        HISTORY_MAX_MAX_EVENTS,
    )
    return enabled, retention, max_events
