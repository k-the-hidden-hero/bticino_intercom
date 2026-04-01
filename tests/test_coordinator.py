"""Tests for the BTicino Intercom coordinator WebSocket event processing."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.core import HomeAssistant

from custom_components.bticino_intercom.const import (
    DATA_LAST_EVENT,
    EVENT_TYPE_ACCEPTED_CALL,
    EVENT_TYPE_ANSWERED_ELSEWHERE,
    EVENT_TYPE_INCOMING_CALL,
    EVENT_TYPE_MISSED_CALL,
    EVENT_TYPE_TERMINATED,
    SIGNAL_CALL_RECEIVED,
)
from custom_components.bticino_intercom.coordinator import BticinoIntercomCoordinator

from .conftest import BRIDGE_MAC, EXTERNAL_UNIT_ID, SESSION_ID

# =============================================================================
# Format A: RTC events
# =============================================================================


class TestRtcOffer:
    """Test RTC offer event processing."""

    async def test_offer_sets_active_call(self, coordinator: BticinoIntercomCoordinator, ws_rtc_offer: dict) -> None:
        """An RTC offer should store the active call state."""
        result = coordinator._process_websocket_event(ws_rtc_offer)

        assert result is True
        assert coordinator.active_call is not None
        assert coordinator.active_call["session_id"] == SESSION_ID
        assert coordinator.active_call["module_id"] == EXTERNAL_UNIT_ID
        assert coordinator.active_call["device_id"] == BRIDGE_MAC
        assert coordinator.active_call["sdp"] is not None
        assert len(coordinator.active_call["modules"]) == 2

    async def test_offer_updates_last_event(self, coordinator: BticinoIntercomCoordinator, ws_rtc_offer: dict) -> None:
        """An RTC offer should update last_event as incoming_call."""
        coordinator._process_websocket_event(ws_rtc_offer)

        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_INCOMING_CALL
        assert last["module_id"] == EXTERNAL_UNIT_ID

    async def test_offer_dispatches_call_signal(
        self, coordinator: BticinoIntercomCoordinator, ws_rtc_offer: dict
    ) -> None:
        """An RTC offer should dispatch SIGNAL_CALL_RECEIVED(True) for the calling module."""
        signals = []

        def capture_signal(*args):
            signals.append(args)

        with patch(
            "custom_components.bticino_intercom.coordinator.async_dispatcher_send",
            side_effect=capture_signal,
        ):
            coordinator._process_websocket_event(ws_rtc_offer)

        assert len(signals) == 1
        assert signals[0] == (coordinator.hass, SIGNAL_CALL_RECEIVED, True, EXTERNAL_UNIT_ID)

    async def test_offer_fires_logbook_event(
        self,
        hass: HomeAssistant,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
    ) -> None:
        """An RTC offer should fire a logbook event."""
        from custom_components.bticino_intercom.const import EVENT_LOGBOOK_INCOMING_CALL

        events = []
        hass.bus.async_listen(EVENT_LOGBOOK_INCOMING_CALL, lambda e: events.append(e))

        coordinator._process_websocket_event(ws_rtc_offer)
        await hass.async_block_till_done()

        assert len(events) == 1
        assert events[0].data["module_id"] == EXTERNAL_UNIT_ID


class TestRtcTerminate:
    """Test RTC terminate event processing."""

    async def test_terminate_clears_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_terminate: dict,
    ) -> None:
        """A terminate after an offer should clear the active call."""
        coordinator._process_websocket_event(ws_rtc_offer)
        assert coordinator.active_call is not None

        result = coordinator._process_websocket_event(ws_rtc_terminate)

        assert result is True
        assert coordinator.active_call is None

    async def test_terminate_dispatches_off_signal_from_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_terminate: dict,
    ) -> None:
        """Terminate should dispatch SIGNAL_CALL_RECEIVED(False) using module_id from active call."""
        # First process the offer to populate active_call
        coordinator._process_websocket_event(ws_rtc_offer)

        signals = []

        def capture_signal(*args):
            signals.append(args)

        with patch(
            "custom_components.bticino_intercom.coordinator.async_dispatcher_send",
            side_effect=capture_signal,
        ):
            coordinator._process_websocket_event(ws_rtc_terminate)

        # Should dispatch with the external unit module_id (from active_call),
        # NOT the bridge MAC (which is the device_id in the terminate event)
        assert len(signals) == 1
        assert signals[0] == (coordinator.hass, SIGNAL_CALL_RECEIVED, False, EXTERNAL_UNIT_ID)

    async def test_terminate_updates_last_event(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_terminate: dict,
    ) -> None:
        """Terminate should update last_event as terminated."""
        coordinator._process_websocket_event(ws_rtc_offer)
        coordinator._process_websocket_event(ws_rtc_terminate)

        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_TERMINATED

    async def test_terminate_without_prior_offer(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_terminate: dict,
    ) -> None:
        """Terminate without a prior offer should still succeed (using device_id)."""
        result = coordinator._process_websocket_event(ws_rtc_terminate)

        assert result is True
        assert coordinator.active_call is None
        # Last event should use the bridge MAC since no active_call was set
        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_TERMINATED
        assert last["module_id"] == BRIDGE_MAC


class TestRtcRescind:
    """Test RTC rescind event processing."""

    async def test_rescind_clears_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_rescind: dict,
    ) -> None:
        """Rescind should clear the active call."""
        coordinator._process_websocket_event(ws_rtc_offer)
        result = coordinator._process_websocket_event(ws_rtc_rescind)

        assert result is True
        assert coordinator.active_call is None

    async def test_rescind_dispatches_off_signal_from_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_rescind: dict,
    ) -> None:
        """Rescind should dispatch using module_id from active_call."""
        coordinator._process_websocket_event(ws_rtc_offer)

        signals = []

        def capture_signal(*args):
            signals.append(args)

        with patch(
            "custom_components.bticino_intercom.coordinator.async_dispatcher_send",
            side_effect=capture_signal,
        ):
            coordinator._process_websocket_event(ws_rtc_rescind)

        assert len(signals) == 1
        assert signals[0] == (coordinator.hass, SIGNAL_CALL_RECEIVED, False, EXTERNAL_UNIT_ID)

    async def test_rescind_updates_last_event(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_rescind: dict,
    ) -> None:
        """Rescind should set last_event type to answered_elsewhere."""
        coordinator._process_websocket_event(ws_rtc_offer)
        coordinator._process_websocket_event(ws_rtc_rescind)

        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_ANSWERED_ELSEWHERE


# =============================================================================
# Format B: Status events
# =============================================================================


class TestStatusIncomingCall:
    """Test BNC1-incoming_call status event processing."""

    async def test_incoming_call_stores_snapshot_urls(
        self, coordinator: BticinoIntercomCoordinator, ws_incoming_call: dict
    ) -> None:
        """incoming_call should store snapshot and vignette URLs."""
        result = coordinator._process_websocket_event(ws_incoming_call)

        assert result is True
        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_INCOMING_CALL
        assert last["snapshot_url"] == "https://example.com/snapshot.jpg"
        assert last["vignette_url"] == "https://example.com/vignette.jpg"
        assert last["module_id"] == BRIDGE_MAC

    async def test_incoming_call_preserves_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_incoming_call: dict,
    ) -> None:
        """incoming_call should not clear the active_call set by offer."""
        coordinator._process_websocket_event(ws_rtc_offer)
        coordinator._process_websocket_event(ws_incoming_call)

        # active_call should still be set from the offer
        assert coordinator.active_call is not None


class TestStatusMissedCall:
    """Test BNC1-missed_call status event processing."""

    async def test_missed_call_clears_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_missed_call: dict,
    ) -> None:
        """missed_call should clear the active call."""
        coordinator._process_websocket_event(ws_rtc_offer)
        result = coordinator._process_websocket_event(ws_missed_call)

        assert result is True
        assert coordinator.active_call is None

    async def test_missed_call_dispatches_off_signal_for_calling_module(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_missed_call: dict,
    ) -> None:
        """missed_call should turn off binary sensor using module_id from active_call."""
        coordinator._process_websocket_event(ws_rtc_offer)

        signals = []

        def capture_signal(*args):
            signals.append(args)

        with patch(
            "custom_components.bticino_intercom.coordinator.async_dispatcher_send",
            side_effect=capture_signal,
        ):
            coordinator._process_websocket_event(ws_missed_call)

        # Should use the external unit module_id from active_call
        assert len(signals) == 1
        assert signals[0] == (coordinator.hass, SIGNAL_CALL_RECEIVED, False, EXTERNAL_UNIT_ID)

    async def test_missed_call_updates_last_event(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_missed_call: dict,
    ) -> None:
        """missed_call should set last_event type to missed_call."""
        coordinator._process_websocket_event(ws_missed_call)

        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_MISSED_CALL
        assert last["module_id"] == BRIDGE_MAC

    async def test_missed_call_without_prior_offer_no_signal(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_missed_call: dict,
    ) -> None:
        """missed_call without a prior offer should not dispatch any signal."""
        signals = []

        def capture_signal(*args):
            signals.append(args)

        with patch(
            "custom_components.bticino_intercom.coordinator.async_dispatcher_send",
            side_effect=capture_signal,
        ):
            coordinator._process_websocket_event(ws_missed_call)

        assert len(signals) == 0


class TestStatusAcceptedCall:
    """Test BNC1-accepted_call status event processing."""

    async def test_accepted_call_does_not_clear_active_call(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_accepted_call: dict,
    ) -> None:
        """accepted_call should NOT clear active_call (call may be active on another device)."""
        coordinator._process_websocket_event(ws_rtc_offer)
        coordinator._process_websocket_event(ws_accepted_call)

        assert coordinator.active_call is not None

    async def test_accepted_call_updates_last_event(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_accepted_call: dict,
    ) -> None:
        """accepted_call should update last_event."""
        coordinator._process_websocket_event(ws_accepted_call)

        last = coordinator.data[DATA_LAST_EVENT]
        assert last["type"] == EVENT_TYPE_ACCEPTED_CALL


# =============================================================================
# Full call sequence (integration-style)
# =============================================================================


class TestCallSequence:
    """Test realistic call sequences matching captured data."""

    async def test_offer_then_incoming_then_terminate_then_missed(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_incoming_call: dict,
        ws_rtc_terminate: dict,
        ws_missed_call: dict,
    ) -> None:
        """Simulate: offer -> incoming_call -> terminate -> missed_call."""
        # 1. Offer arrives
        coordinator._process_websocket_event(ws_rtc_offer)
        assert coordinator.active_call is not None
        assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_INCOMING_CALL

        # 2. incoming_call with snapshot (overwrites last_event but keeps active_call)
        coordinator._process_websocket_event(ws_incoming_call)
        assert coordinator.active_call is not None
        assert coordinator.data[DATA_LAST_EVENT]["snapshot_url"] == "https://example.com/snapshot.jpg"

        # 3. Terminate
        coordinator._process_websocket_event(ws_rtc_terminate)
        assert coordinator.active_call is None
        assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_TERMINATED

        # 4. Missed call
        coordinator._process_websocket_event(ws_missed_call)
        assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_MISSED_CALL

    async def test_offer_then_rescind_then_accepted(
        self,
        coordinator: BticinoIntercomCoordinator,
        ws_rtc_offer: dict,
        ws_rtc_rescind: dict,
        ws_accepted_call: dict,
    ) -> None:
        """Simulate: offer -> rescind -> accepted_call (answered on another device)."""
        coordinator._process_websocket_event(ws_rtc_offer)
        assert coordinator.active_call is not None

        coordinator._process_websocket_event(ws_rtc_rescind)
        assert coordinator.active_call is None
        assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_ANSWERED_ELSEWHERE

        coordinator._process_websocket_event(ws_accepted_call)
        assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_ACCEPTED_CALL

    async def test_unknown_event_returns_false(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Unknown event types should return False without changing state."""
        unknown = {
            "push_type": "BNC1-something_else",
            "extra_params": {"data": {"type": "unknown"}},
        }
        result = coordinator._process_websocket_event(unknown)
        assert result is False
