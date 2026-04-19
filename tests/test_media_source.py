"""Tests for the BTicino media source provider."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.core import HomeAssistant

from custom_components.bticino_intercom.const import DOMAIN
from custom_components.bticino_intercom.history import EventHistoryStore
from custom_components.bticino_intercom.media_source import (
    BticinoMediaSource,
    async_get_media_source,
)


@pytest.fixture
async def seeded_store(hass: HomeAssistant, tmp_path):
    """Return a history store with a couple of recorded events."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_main")
        await store.async_load()
        # Simulate that images have been fetched by writing files directly.
        mod_dir = store._root / "mod_a"
        mod_dir.mkdir(parents=True, exist_ok=True)
        (mod_dir / "evt1_snapshot.jpg").write_bytes(b"snap")
        (mod_dir / "evt1_vignette.jpg").write_bytes(b"vig")
        store._events = [
            {
                "event_id": "evt1",
                "module_id": "mod_a",
                "module_name": "Front Door",
                "event_type": "incoming_call",
                "timestamp": 1700000000,
                "ended_at": None,
                "answered": None,
                "snapshot_file": "mod_a/evt1_snapshot.jpg",
                "vignette_file": "mod_a/evt1_vignette.jpg",
            },
        ]
    hass.data.setdefault(DOMAIN, {})["entry_main"] = {"history": store}
    yield store
    hass.data.get(DOMAIN, {}).pop("entry_main", None)


async def test_async_get_media_source_returns_provider(hass: HomeAssistant) -> None:
    """The factory function must return a BticinoMediaSource."""
    ms = await async_get_media_source(hass)
    assert isinstance(ms, BticinoMediaSource)
    assert ms.domain == DOMAIN


async def test_browse_root_lists_entries(hass: HomeAssistant, seeded_store) -> None:
    """Browsing the root should list every entry that has a history store."""
    ms = BticinoMediaSource(hass)
    root = await ms.async_browse_media(MediaSourceItem(hass, DOMAIN, "", None))
    assert root.title == "BTicino Intercom"
    assert root.children is not None
    assert any(c.identifier == "entry_main" for c in root.children)


async def test_browse_entry_lists_modules(hass: HomeAssistant, seeded_store) -> None:
    """Browsing an entry should list its modules."""
    ms = BticinoMediaSource(hass)
    node = await ms.async_browse_media(MediaSourceItem(hass, DOMAIN, "entry_main", None))
    assert node.children is not None
    assert [c.identifier for c in node.children] == ["entry_main/mod_a"]


async def test_browse_module_lists_events(hass: HomeAssistant, seeded_store) -> None:
    """Browsing a module should list its events."""
    ms = BticinoMediaSource(hass)
    node = await ms.async_browse_media(MediaSourceItem(hass, DOMAIN, "entry_main/mod_a", None))
    assert node.children is not None
    assert [c.identifier for c in node.children] == ["entry_main/mod_a/evt1"]


async def test_browse_event_lists_images(hass: HomeAssistant, seeded_store) -> None:
    """Browsing an event should list snapshot and vignette leaves."""
    ms = BticinoMediaSource(hass)
    node = await ms.async_browse_media(MediaSourceItem(hass, DOMAIN, "entry_main/mod_a/evt1", None))
    assert node.children is not None
    kinds = sorted(c.identifier.rsplit("/", 1)[-1] for c in node.children)
    assert kinds == ["snapshot", "vignette"]


async def test_resolve_returns_play_media(hass: HomeAssistant, seeded_store) -> None:
    """Resolving a leaf identifier must return a playable image path."""
    ms = BticinoMediaSource(hass)
    play = await ms.async_resolve_media(
        MediaSourceItem(hass, DOMAIN, "entry_main/mod_a/evt1/snapshot", None),
    )
    assert play.mime_type == "image/jpeg"
    assert play.url == "/api/bticino_intercom/image/entry_main/evt1/snapshot"
    assert play.path is not None and play.path.exists()


async def test_resolve_unknown_identifier_raises(hass: HomeAssistant, seeded_store) -> None:
    """Malformed or unknown identifiers should raise ``Unresolvable``."""
    ms = BticinoMediaSource(hass)
    with pytest.raises(Unresolvable):
        await ms.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, "entry_main/mod_a/evt1", None),
        )
    with pytest.raises(Unresolvable):
        await ms.async_resolve_media(
            MediaSourceItem(hass, DOMAIN, "entry_main/mod_a/ghost/snapshot", None),
        )
