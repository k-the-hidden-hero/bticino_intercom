"""Tests for the BTicino Intercom coordinator WebSocket event processing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.bticino_intercom.const import (
    CALL_RETRANSMIT_WINDOW,
    CALL_SESSION_MAX_DURATION,
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

    async def test_offer_sets_signaling_session(
        self, coordinator: BticinoIntercomCoordinator, ws_rtc_offer: dict
    ) -> None:
        """An RTC offer should call set_session_from_push on the signaling client."""
        from unittest.mock import MagicMock

        mock_signaling = MagicMock()
        coordinator._signaling_client = mock_signaling

        coordinator._process_websocket_event(ws_rtc_offer)

        mock_signaling.set_session_from_push.assert_called_once_with(
            session_id=SESSION_ID,
            tag_id="dgo4dB6RqEk=",
            correlation_id="1499514006757899539",
            device_id=BRIDGE_MAC,
        )

    async def test_offer_without_signaling_client_does_not_crash(
        self, coordinator: BticinoIntercomCoordinator, ws_rtc_offer: dict
    ) -> None:
        """An RTC offer should work fine when signaling client is None."""
        coordinator._signaling_client = None
        result = coordinator._process_websocket_event(ws_rtc_offer)
        assert result is True
        assert coordinator.active_call is not None

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


# =============================================================================
# Call session management
# =============================================================================


class TestEndCallSession:
    """Test _end_call_session behavior."""

    async def test_end_call_session_removes_entry(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Session should be removed from _active_calls."""
        coordinator._active_calls["mod_x"] = {
            "started": datetime.now(UTC),
            "last_seen": datetime.now(UTC),
            "watchdog": None,
        }
        coordinator._end_call_session("mod_x", reason="terminate")
        assert "mod_x" not in coordinator._active_calls

    async def test_end_call_session_cancels_watchdog(self, coordinator: BticinoIntercomCoordinator) -> None:
        """An active watchdog task should be cancelled."""
        watchdog = AsyncMock(spec=asyncio.Task)
        watchdog.done.return_value = False
        coordinator._active_calls["mod_x"] = {
            "started": datetime.now(UTC),
            "last_seen": datetime.now(UTC),
            "watchdog": watchdog,
        }
        coordinator._end_call_session("mod_x", reason="rescind")
        watchdog.cancel.assert_called_once()

    async def test_end_call_session_noop_for_unknown_module(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Ending a session for a module that has no session should not crash."""
        coordinator._end_call_session("nonexistent", reason="terminate")
        assert "nonexistent" not in coordinator._active_calls


class TestCallSessionWatchdog:
    """Test _call_session_watchdog timeout behavior."""

    async def test_watchdog_closes_on_inactivity(
        self,
        hass: HomeAssistant,
        coordinator: BticinoIntercomCoordinator,
    ) -> None:
        """Watchdog should close the session when no retransmission arrives."""
        now = datetime.now(UTC)
        coordinator._active_calls[EXTERNAL_UNIT_ID] = {
            "started": now - timedelta(seconds=5),
            "last_seen": now - timedelta(seconds=CALL_RETRANSMIT_WINDOW + 1),
            "watchdog": None,
        }
        with patch(
            "custom_components.bticino_intercom.coordinator.asyncio.sleep",
            side_effect=[None, asyncio.CancelledError],
        ):
            await coordinator._call_session_watchdog(EXTERNAL_UNIT_ID)

        assert EXTERNAL_UNIT_ID not in coordinator._active_calls

    async def test_watchdog_closes_on_hard_timeout(
        self,
        hass: HomeAssistant,
        coordinator: BticinoIntercomCoordinator,
    ) -> None:
        """Watchdog should close the session if it exceeds max duration."""
        now = datetime.now(UTC)
        coordinator._active_calls[EXTERNAL_UNIT_ID] = {
            "started": now - timedelta(seconds=CALL_SESSION_MAX_DURATION + 1),
            "last_seen": now,
            "watchdog": None,
        }
        with patch(
            "custom_components.bticino_intercom.coordinator.asyncio.sleep",
            side_effect=[None, asyncio.CancelledError],
        ):
            await coordinator._call_session_watchdog(EXTERNAL_UNIT_ID)

        assert EXTERNAL_UNIT_ID not in coordinator._active_calls

    async def test_watchdog_exits_if_session_removed_externally(
        self,
        coordinator: BticinoIntercomCoordinator,
    ) -> None:
        """Watchdog should exit cleanly if the session was already removed."""
        with patch(
            "custom_components.bticino_intercom.coordinator.asyncio.sleep",
            side_effect=[None],
        ):
            await coordinator._call_session_watchdog(EXTERNAL_UNIT_ID)


class TestCloseHistoryEvent:
    """Test _close_history_event behavior."""

    async def test_close_history_event_noop_without_history(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Should not crash when history store is None."""
        coordinator.history = None
        coordinator._close_history_event("sess-123", EVENT_TYPE_TERMINATED)

    async def test_close_history_event_noop_without_event_id(self, coordinator: BticinoIntercomCoordinator) -> None:
        """Should not crash when event_id is None."""
        coordinator.history = AsyncMock()
        coordinator._close_history_event(None, EVENT_TYPE_TERMINATED)
        coordinator.history.async_close_call.assert_not_called()

    async def test_close_history_event_schedules_task(
        self,
        hass: HomeAssistant,
        coordinator: BticinoIntercomCoordinator,
    ) -> None:
        """Should schedule a background task when both history and event_id are present."""
        mock_history = AsyncMock()
        coordinator.history = mock_history
        coordinator._close_history_event("sess-123", EVENT_TYPE_TERMINATED)
        await hass.async_block_till_done(wait_background_tasks=True)
        mock_history.async_close_call.assert_awaited_once_with(event_id="sess-123", event_type=EVENT_TYPE_TERMINATED)


class TestUpdateLastEvent:
    """Test _update_last_event preserves existing subevents."""

    async def test_preserves_existing_subevents(
        self, coordinator: BticinoIntercomCoordinator, ws_rtc_offer: dict
    ) -> None:
        """Subevents from a prior incoming_call should survive _update_last_event."""
        existing_subevents = [{"time": 123, "snapshot": {"url": "https://example.com"}}]
        coordinator.data[DATA_LAST_EVENT] = {
            "type": EVENT_TYPE_INCOMING_CALL,
            "subevents": existing_subevents,
        }
        coordinator._update_last_event(
            EVENT_TYPE_TERMINATED,
            EXTERNAL_UNIT_ID,
            "Citofono",
            ws_rtc_offer,
            ws_rtc_offer["extra_params"],
        )
        assert coordinator.data[DATA_LAST_EVENT]["subevents"] == existing_subevents
        assert coordinator.data[DATA_LAST_EVENT]["type"] == EVENT_TYPE_TERMINATED


# =============================================================================
# bticino_intercom_call event emission
# =============================================================================


class TestCallEventEmission:
    """Test bticino_intercom_call event emission."""

    async def test_rtc_offer_fires_ring_event(self, hass, coordinator, ws_rtc_offer):
        """RTC offer should fire a ring event with type=ring."""
        events = async_capture_events(hass, "bticino_intercom_call")

        coordinator._process_websocket_event(ws_rtc_offer)
        await hass.async_block_till_done()

        ring_events = [e for e in events if e.data.get("type") == "ring"]
        assert len(ring_events) >= 1
        data = ring_events[0].data
        assert data["type"] == "ring"
        assert data["session_id"] is not None
        assert data["module_id"] is not None
        assert data["entry_id"] == coordinator.entry.entry_id
        assert "camera_entity_id" in data

    async def test_rtc_rescind_fires_end_event(self, hass, coordinator, ws_rtc_offer, ws_rtc_rescind):
        """RTC rescind should fire an end event with reason=rescind."""
        coordinator._process_websocket_event(ws_rtc_offer)
        await hass.async_block_till_done()

        events = async_capture_events(hass, "bticino_intercom_call")
        coordinator._process_websocket_event(ws_rtc_rescind)
        await hass.async_block_till_done()

        end_events = [e for e in events if e.data.get("type") == "end"]
        assert len(end_events) == 1
        assert end_events[0].data["reason"] == "rescind"

    async def test_rtc_terminate_fires_end_event(self, hass, coordinator, ws_rtc_offer, ws_rtc_terminate):
        """RTC terminate should fire an end event with reason=terminate."""
        coordinator._process_websocket_event(ws_rtc_offer)
        await hass.async_block_till_done()

        events = async_capture_events(hass, "bticino_intercom_call")
        coordinator._process_websocket_event(ws_rtc_terminate)
        await hass.async_block_till_done()

        end_events = [e for e in events if e.data.get("type") == "end"]
        assert len(end_events) == 1
        assert end_events[0].data["reason"] == "terminate"

    async def test_timeout_fires_end_event(self, hass, coordinator, ws_rtc_offer):
        """Binary sensor timeout should fire an end event with reason=timeout."""
        coordinator._process_websocket_event(ws_rtc_offer)
        await hass.async_block_till_done()

        events = async_capture_events(hass, "bticino_intercom_call")
        coordinator.fire_call_timeout(EXTERNAL_UNIT_ID)
        await hass.async_block_till_done()

        end_events = [e for e in events if e.data.get("type") == "end"]
        assert len(end_events) == 1
        assert end_events[0].data["reason"] == "timeout"
        # active_call should be cleared after timeout
        assert coordinator.active_call is None
