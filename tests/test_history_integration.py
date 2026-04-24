"""End-to-end wiring tests: coordinator → history store."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bticino_intercom.const import DOMAIN

from .conftest import EXT_UNIT_MODULE_ID, HOME_ID


async def test_incoming_call_push_calls_history_record(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """An ``incoming_call`` push should call async_record_call with the right args."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    assert coordinator.history is not None

    mock_record = AsyncMock(return_value={"event_id": "sess-xyz"})
    coordinator.history.async_record_call = mock_record

    updated = await coordinator._process_websocket_event(
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
    await hass.async_block_till_done()

    assert updated is True
    mock_record.assert_awaited_once()
    call_kwargs = mock_record.call_args.kwargs
    assert call_kwargs["event_id"] == "sess-xyz"
    assert call_kwargs["module_id"] == EXT_UNIT_MODULE_ID
    assert call_kwargs["snapshot_url"] == "https://azure.example/snap.jpg"
    assert call_kwargs["vignette_url"] == "https://azure.example/vig.jpg"


async def test_incoming_call_download_completes_before_event_fires(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Images must be downloaded before the ring event fires."""
    from custom_components.bticino_intercom.const import EVENT_CALL

    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    assert coordinator.history is not None

    # Set up active call so the ring event fires
    coordinator._active_call = {"session_id": "s1", "module_id": EXT_UNIT_MODULE_ID}

    call_order: list[str] = []

    async def fake_record(**kwargs):
        call_order.append("download")
        return {"event_id": kwargs["event_id"]}

    coordinator.history.async_record_call = AsyncMock(side_effect=fake_record)

    events: list = []
    hass.bus.async_listen(EVENT_CALL, lambda e: (call_order.append("event"), events.append(e)))

    await coordinator._process_websocket_event(
        {
            "push_type": "BNC1-incoming_call",
            "extra_params": {
                "event_type": "incoming_call",
                "device_id": EXT_UNIT_MODULE_ID,
                "session_id": "sess-order",
                "snapshot_url": "https://azure.example/snap.jpg",
                "vignette_url": None,
            },
        }
    )
    await hass.async_block_till_done()

    assert call_order[0] == "download", "Download must complete before event fires"


async def test_terminate_event_closes_history_record(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """A terminate RTC event should mark the matching history record as closed."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]
    assert coordinator.history is not None

    coordinator.history.async_record_call = AsyncMock(return_value={"event_id": "sess-close"})
    mock_close = AsyncMock()
    coordinator.history.async_close_call = mock_close

    # Start: incoming_call push records the event.
    await coordinator._process_websocket_event(
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

    # Follow-up: RTC terminate with the same session_id.
    await coordinator._process_websocket_event(
        {
            "extra_params": {
                "device_id": EXT_UNIT_MODULE_ID,
                "session_id": "sess-close",
                "data": {
                    "type": "terminate",
                },
            }
        }
    )

    mock_close.assert_awaited_once_with(event_id="sess-close", event_type="terminated")


async def test_history_disabled_skips_recording(
    hass: HomeAssistant,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """With history disabled via options, no store is created and pushes are ignored."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="No history",
        data={"username": "u@x", "password": "p", "home_id": HOME_ID},
        options={"light_as_lock": False, "history_enabled": False},
        unique_id="u@x",
        version=1,
    )
    entry.add_to_hass(hass)

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth_handler),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=mock_account),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_websocket_client),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling_client),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
        patch("custom_components.bticino_intercom.history.Store") as mock_history_store_cls,
    ):
        for cls in (mock_store_cls, mock_history_store_cls):
            cls.return_value.async_load = AsyncMock(return_value=None)
            cls.return_value.async_save = AsyncMock()
            cls.return_value.async_remove = AsyncMock()
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert hass.data[DOMAIN][entry.entry_id]["history"] is None
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    assert coordinator.history is None

    await coordinator._process_websocket_event(
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
    await hass.async_block_till_done()
