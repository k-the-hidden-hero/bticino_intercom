"""End-to-end wiring tests: coordinator → history store."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import EXT_UNIT_MODULE_ID


class _FakeResponse:
    """aiohttp response stub."""

    def __init__(self, status: int = 200, body: bytes = b"bytes") -> None:
        self.status = status
        self._body = body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        return _FakeResponse(status=200, body=b"IMAGE_" + url.encode())


async def test_incoming_call_push_downloads_images(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """An ``incoming_call`` push should persist a history record with images."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    history = hass.data[DOMAIN][mock_setup_entry.entry_id]["history"]
    assert history is not None

    session = _FakeSession()
    with patch(
        "custom_components.bticino_intercom.history.async_get_clientsession",
        return_value=session,
    ):
        updated = coordinator._process_websocket_event(
            {
                "push_type": "BNC1-incoming_call",
                "extra_params": {
                    "event_type": "incoming_call",
                    "device_id": EXT_UNIT_MODULE_ID,
                    "session_id": "sess-xyz",
                    "snapshot_url": "https://azure.example/snap.jpg",
                    "vignette_url": "https://azure.example/vig.jpg",
                },
            }
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert updated is True
    events = history.list_events()
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == "sess-xyz"
    assert event["module_id"] == EXT_UNIT_MODULE_ID
    assert event["snapshot_file"] and event["vignette_file"]
    assert sorted(session.calls) == [
        "https://azure.example/snap.jpg",
        "https://azure.example/vig.jpg",
    ]


async def test_terminate_event_closes_history_record(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """A terminate RTC event should mark the matching history record as closed."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    history = hass.data[DOMAIN][mock_setup_entry.entry_id]["history"]
    assert history is not None

    session = _FakeSession()
    with patch(
        "custom_components.bticino_intercom.history.async_get_clientsession",
        return_value=session,
    ):
        # Start: incoming_call push records the event.
        coordinator._process_websocket_event(
            {
                "push_type": "BNC1-incoming_call",
                "extra_params": {
                    "event_type": "incoming_call",
                    "device_id": EXT_UNIT_MODULE_ID,
                    "session_id": "sess-close",
                    "snapshot_url": "https://azure.example/snap.jpg",
                    "vignette_url": None,
                },
            }
        )
        await hass.async_block_till_done(wait_background_tasks=True)

        # Follow-up: RTC terminate with the same session_id.
        coordinator._process_websocket_event(
            {
                "extra_params": {
                    "device_id": EXT_UNIT_MODULE_ID,
                    "data": {
                        "session_description": {
                            "type": "terminate",
                            "module_id": EXT_UNIT_MODULE_ID,
                            "session_id": "sess-close",
                        }
                    },
                }
            }
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    event = history.get_event("sess-close")
    assert event is not None
    assert event["event_type"] == "terminated"
    assert event["answered"] is False
    assert event["ended_at"] is not None


async def test_history_disabled_skips_recording(
    hass: HomeAssistant,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client,
) -> None:
    """With history disabled via options, no store is created and pushes are ignored."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="No history",
        data={"username": "u@x", "password": "p", "home_id": "home_test_123"},
        options={"light_as_lock": False, "history_enabled": False},
        unique_id="u@x",
        version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id]["history"] is None
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.history is None

    session = _FakeSession()
    with patch(
        "custom_components.bticino_intercom.history.async_get_clientsession",
        return_value=session,
    ):
        coordinator._process_websocket_event(
            {
                "push_type": "BNC1-incoming_call",
                "extra_params": {
                    "event_type": "incoming_call",
                    "device_id": EXT_UNIT_MODULE_ID,
                    "session_id": "ignored",
                    "snapshot_url": "https://azure.example/snap.jpg",
                    "vignette_url": None,
                },
            }
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert session.calls == []
