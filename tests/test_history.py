"""Tests for the event history store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bticino_intercom.history import EventHistoryStore


class _FakeResponse:
    """Minimal aiohttp-like response for download tests."""

    def __init__(self, status: int = 200, body: bytes = b"fakeimage") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    """aiohttp session stub returning prebuilt responses."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        if url in self._responses:
            return self._responses[url]
        return _FakeResponse(status=404, body=b"")


@pytest.fixture
def fake_session() -> _FakeSession:
    """Return a default fake aiohttp session."""
    return _FakeSession(
        {
            "https://example.com/snap.jpg": _FakeResponse(status=200, body=b"SNAPSHOT"),
            "https://example.com/vig.jpg": _FakeResponse(status=200, body=b"VIGNETTE"),
        }
    )


@pytest.fixture
def patch_session(fake_session: _FakeSession):
    """Patch ``async_get_clientsession`` used by the history store."""
    with patch(
        "custom_components.bticino_intercom.history.async_get_clientsession",
        return_value=fake_session,
    ):
        yield fake_session


async def test_record_and_resolve_event(
    hass: HomeAssistant,
    patch_session: _FakeSession,
    tmp_path,
) -> None:
    """Record a call and verify images are downloaded and resolvable."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_abc")
        await store.async_load()

        await store.async_record_call(
            event_id="sess-1",
            module_id="00:11:22:33:44:55",
            module_name="Front Door",
            snapshot_url="https://example.com/snap.jpg",
            vignette_url="https://example.com/vig.jpg",
            event_timestamp=1700000000,
        )

    events = store.list_events()
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == "sess-1"
    assert event["module_name"] == "Front Door"
    assert event["snapshot_file"] and event["vignette_file"]

    snap_path = store.resolve_image_path("sess-1", "snapshot")
    vig_path = store.resolve_image_path("sess-1", "vignette")
    assert snap_path is not None and snap_path.read_bytes() == b"SNAPSHOT"
    assert vig_path is not None and vig_path.read_bytes() == b"VIGNETTE"


async def test_close_call_marks_answered(
    hass: HomeAssistant,
    patch_session: _FakeSession,
    tmp_path,
) -> None:
    """``async_close_call`` should update event_type and ended_at."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_abc")
        await store.async_load()
        await store.async_record_call(
            event_id="sess-2",
            module_id="mod",
            module_name="Mod",
            snapshot_url="https://example.com/snap.jpg",
            vignette_url=None,
        )
        await store.async_close_call(
            event_id="sess-2",
            event_type="answered_elsewhere",
            ended_at=1700001000,
        )

    event = store.get_event("sess-2")
    assert event is not None
    assert event["event_type"] == "answered_elsewhere"
    assert event["answered"] is True
    assert event["ended_at"] == 1700001000


async def test_close_call_missing_event_is_noop(
    hass: HomeAssistant,
    tmp_path,
) -> None:
    """Closing an unknown event should not raise."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_xyz")
        await store.async_load()
        await store.async_close_call(event_id="ghost", event_type="terminated")
    assert store.list_events() == []


async def test_retention_by_days_and_count(
    hass: HomeAssistant,
    patch_session: _FakeSession,
    tmp_path,
) -> None:
    """Retention drops events older than the window AND beyond the count cap."""
    now = int(datetime.now(UTC).timestamp())
    old_ts = int((datetime.now(UTC) - timedelta(days=40)).timestamp())

    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_ret")
        await store.async_load()
        # Fresh event within window
        await store.async_record_call(
            event_id="fresh",
            module_id="m",
            module_name=None,
            snapshot_url="https://example.com/snap.jpg",
            vignette_url=None,
            event_timestamp=now,
        )
        # Old event beyond retention window
        await store.async_record_call(
            event_id="old",
            module_id="m",
            module_name=None,
            snapshot_url="https://example.com/snap.jpg",
            vignette_url=None,
            event_timestamp=old_ts,
        )

        purged = await store.async_apply_retention(retention_days=30, max_events=500)

    assert purged == 1
    assert [e["event_id"] for e in store.list_events()] == ["fresh"]


async def test_retention_enforces_max_events(
    hass: HomeAssistant,
    patch_session: _FakeSession,
    tmp_path,
) -> None:
    """The count cap drops the oldest events when exceeded."""
    now = int(datetime.now(UTC).timestamp())

    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_cap")
        await store.async_load()
        for i in range(5):
            await store.async_record_call(
                event_id=f"ev{i}",
                module_id="m",
                module_name=None,
                snapshot_url="https://example.com/snap.jpg",
                vignette_url=None,
                event_timestamp=now - i,
            )
        purged = await store.async_apply_retention(retention_days=365, max_events=2)

    assert purged == 3
    ids = [e["event_id"] for e in store.list_events()]
    assert ids == ["ev0", "ev1"]


async def test_resolve_image_path_rejects_traversal(
    hass: HomeAssistant,
    patch_session: _FakeSession,
    tmp_path,
) -> None:
    """Crafted paths pointing outside the store root must be rejected."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_sec")
        await store.async_load()
        # Inject a malicious record bypassing async_record_call.
        store._events.insert(
            0,
            {
                "event_id": "evil",
                "module_id": "m",
                "module_name": None,
                "event_type": "incoming_call",
                "timestamp": 1,
                "ended_at": None,
                "answered": None,
                "snapshot_file": "../../escape.jpg",
                "vignette_file": None,
            },
        )

    assert store.resolve_image_path("evil", "snapshot") is None


async def test_download_failure_leaves_record_without_image(
    hass: HomeAssistant,
    tmp_path,
) -> None:
    """A non-200 response should still create the record without the file."""
    session = _FakeSession({"https://example.com/snap.jpg": _FakeResponse(status=500, body=b"")})
    with (
        patch(
            "custom_components.bticino_intercom.history.async_get_clientsession",
            return_value=session,
        ),
        patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))),
    ):
        store = EventHistoryStore(hass, "entry_fail")
        await store.async_load()
        await store.async_record_call(
            event_id="e1",
            module_id="m",
            module_name=None,
            snapshot_url="https://example.com/snap.jpg",
            vignette_url=None,
        )

    event = store.get_event("e1")
    assert event is not None
    assert event["snapshot_file"] is None


async def test_clear_wipes_events_and_files(
    hass: HomeAssistant,
    patch_session: _FakeSession,
    tmp_path,
) -> None:
    """``async_clear`` removes both metadata and files from disk."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_clear")
        await store.async_load()
        await store.async_record_call(
            event_id="e",
            module_id="m",
            module_name=None,
            snapshot_url="https://example.com/snap.jpg",
            vignette_url=None,
        )
        path = store.resolve_image_path("e", "snapshot")
        assert path is not None and path.exists()

        await store.async_clear()

    assert store.list_events() == []
    assert not path.exists()


async def test_list_modules_returns_distinct_ids(
    hass: HomeAssistant,
    tmp_path,
) -> None:
    """``list_modules`` should return distinct module IDs in insertion order."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_mods")
        await store.async_load()
        for eid, mid in [("e1", "mod_a"), ("e2", "mod_b"), ("e3", "mod_a"), ("e4", "mod_c")]:
            await store.async_record_call(
                event_id=eid,
                module_id=mid,
                module_name=None,
                snapshot_url=None,
                vignette_url=None,
            )

    assert sorted(store.list_modules()) == ["mod_a", "mod_b", "mod_c"]


async def test_list_modules_empty_store(
    hass: HomeAssistant,
    tmp_path,
) -> None:
    """``list_modules`` on an empty store should return an empty list."""
    with patch.object(hass.config, "path", side_effect=lambda *parts: str(tmp_path.joinpath(*parts))):
        store = EventHistoryStore(hass, "entry_empty")
        await store.async_load()

    assert store.list_modules() == []
