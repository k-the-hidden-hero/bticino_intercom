"""Tests for BTicino integration setup and unload."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_START
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import entity_registry as er
from pybticino.exceptions import ApiError, AuthError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
)

from custom_components.bticino_intercom import (
    RUNTIME_RETRY_DELAYS,
    WEBSOCKET_CLIENT_KEY,
    WS_STABLE_CONNECTION_SECONDS,
    _next_websocket_retry,
    _refresh_push_connection,
    _wait_for_websocket_listener,
)
from custom_components.bticino_intercom.const import DOMAIN

from .conftest import EXTERNAL_UNIT_ID, HOME_ID, _setup_integration

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def test_short_websocket_sessions_keep_escalating_backoff() -> None:
    """A successful handshake followed by a quick close is still a failure."""
    attempt = 0

    for expected_attempt, expected_delay in enumerate(RUNTIME_RETRY_DELAYS, start=1):
        attempt, delay, stable = _next_websocket_retry(
            attempt=attempt,
            is_boot=False,
            session_duration=WS_STABLE_CONNECTION_SECONDS - 1,
            received_message=False,
        )

        assert attempt == expected_attempt
        assert delay == expected_delay
        assert stable is False

    assert attempt == 5


@pytest.mark.parametrize(
    ("session_duration", "received_message"),
    [
        (WS_STABLE_CONNECTION_SECONDS, False),
        (1, True),
    ],
)
def test_healthy_websocket_session_resets_backoff(
    session_duration: float,
    received_message: bool,
) -> None:
    """A stable session or real message re-arms the shortest retry delay."""
    attempt, delay, stable = _next_websocket_retry(
        attempt=4,
        is_boot=False,
        session_duration=session_duration,
        received_message=received_message,
    )

    assert attempt == 1
    assert delay == RUNTIME_RETRY_DELAYS[0]
    assert stable is True


def test_failed_boot_connection_uses_boot_backoff() -> None:
    """A connection that never completed keeps the boot retry schedule."""
    attempt, delay, stable = _next_websocket_retry(
        attempt=1,
        is_boot=True,
        session_duration=None,
        received_message=False,
    )

    assert attempt == 2
    assert delay == 10
    assert stable is False


async def test_listener_timeout_then_exception_is_consumed_by_manager() -> None:
    """A polling timeout must not leave a shield future with an unhandled error."""
    release_listener = asyncio.Event()

    async def _listener() -> None:
        await release_listener.wait()
        raise RuntimeError("listener failed")

    listener_task = asyncio.create_task(_listener())

    assert await _wait_for_websocket_listener(listener_task, timeout=0) is False

    release_listener.set()
    with pytest.raises(RuntimeError, match="listener failed"):
        await _wait_for_websocket_listener(listener_task, timeout=1)

    assert listener_task.done()


class TestPushConnectionRefresh:
    """A Subscribe sent again on the same socket adds a subscription, and every
    push then arrives once more (5 copies of each push after four hourly
    re-subscribes on 2026-11-16). The fresh token goes on a new connection."""

    @pytest.fixture
    def clients(self, hass: HomeAssistant, mock_config_entry: MockConfigEntry):
        """The current connection, the one replacing it, and the order things happen in."""
        order: list[str] = []
        current = AsyncMock()
        current.disconnect.side_effect = lambda: order.append("close current")
        fresh = AsyncMock()
        fresh.connect.side_effect = lambda: order.append("open fresh")
        coordinator = MagicMock(websocket_client=current)
        hass.data[DOMAIN] = {mock_config_entry.entry_id: {WEBSOCKET_CLIENT_KEY: current}}
        return current, fresh, coordinator, order

    async def test_opens_the_new_connection_before_closing_the_old(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, clients
    ) -> None:
        """No push falls in a gap: the old connection closes once the new one is subscribed."""
        current, fresh, coordinator, order = clients

        with patch("custom_components.bticino_intercom.WebsocketClient", return_value=fresh):
            kept = await _refresh_push_connection(hass, mock_config_entry, MagicMock(), coordinator, current)

        assert kept is fresh
        assert order == ["open fresh", "close current"]
        assert coordinator.websocket_client is fresh
        assert hass.data[DOMAIN][mock_config_entry.entry_id][WEBSOCKET_CLIENT_KEY] is fresh

    async def test_keeps_the_current_connection_when_the_new_one_fails(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, clients
    ) -> None:
        """The current connection still works, so a failed attempt changes nothing."""
        current, fresh, coordinator, _ = clients
        fresh.connect.side_effect = ApiError(503, "Service Unavailable")

        with patch("custom_components.bticino_intercom.WebsocketClient", return_value=fresh):
            kept = await _refresh_push_connection(hass, mock_config_entry, MagicMock(), coordinator, current)

        assert kept is current
        current.disconnect.assert_not_awaited()
        assert coordinator.websocket_client is current
        assert hass.data[DOMAIN][mock_config_entry.entry_id][WEBSOCKET_CLIENT_KEY] is current

    async def test_cancelled_refresh_closes_the_new_connection(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, clients
    ) -> None:
        """An unload during the refresh must not leave a subscribed socket behind.

        It would keep handing pushes to the unloaded coordinator, which still
        fires its events.
        """
        current, fresh, coordinator, _ = clients
        fresh.connect.side_effect = asyncio.CancelledError

        with (
            patch("custom_components.bticino_intercom.WebsocketClient", return_value=fresh),
            pytest.raises(asyncio.CancelledError),
        ):
            await _refresh_push_connection(hass, mock_config_entry, MagicMock(), coordinator, current)

        fresh.disconnect.assert_awaited_once()
        current.disconnect.assert_not_awaited()

    async def test_rejected_credentials_still_request_reauth(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry, clients
    ) -> None:
        """The manager handles reauth and closes the current connection itself."""
        current, fresh, coordinator, _ = clients
        fresh.connect.side_effect = AuthError("Bad credentials")

        with (
            patch("custom_components.bticino_intercom.WebsocketClient", return_value=fresh),
            patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock),
            pytest.raises(ConfigEntryAuthFailed),
        ):
            await _refresh_push_connection(hass, mock_config_entry, MagicMock(), coordinator, current)

        current.disconnect.assert_not_awaited()

    async def test_manager_follows_the_new_connection(
        self,
        hass: HomeAssistant,
        mock_config_entry: MockConfigEntry,
        mock_auth_handler: AsyncMock,
        mock_account: AsyncMock,
        mock_websocket_client: AsyncMock,
        mock_signaling_client: AsyncMock,
    ) -> None:
        """After the refresh the manager waits on the new listener, and closes it on shutdown."""
        old_listener = asyncio.create_task(asyncio.Event().wait())
        new_listener = asyncio.create_task(asyncio.Event().wait())
        mock_websocket_client.get_listener_task.return_value = old_listener
        fresh = AsyncMock()
        fresh.get_listener_task = MagicMock(return_value=new_listener)
        refresh_due = asyncio.Event()
        watching_new = asyncio.Event()

        async def wait_for_listener(listener: asyncio.Task, timeout: float) -> bool:
            if listener is old_listener:
                await refresh_due.wait()
                return False
            watching_new.set()
            await asyncio.Event().wait()
            return True

        with (
            # Setup creates the first client; every later one is the fresh one.
            patch("custom_components.bticino_intercom.WebsocketClient", return_value=fresh),
            patch("custom_components.bticino_intercom.PUSH_CONNECTION_REFRESH_INTERVAL", 0),
            patch("custom_components.bticino_intercom._wait_for_websocket_listener", side_effect=wait_for_listener),
        ):
            entry = await _setup_integration(
                hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
            )
            manager = hass.data[DOMAIN][entry.entry_id]["websocket_connection_task"]
            refresh_due.set()
            await asyncio.wait_for(watching_new.wait(), timeout=5)

            fresh.connect.assert_awaited_once()
            mock_websocket_client.disconnect.assert_awaited_once()
            assert hass.data[DOMAIN][entry.entry_id][WEBSOCKET_CLIENT_KEY] is fresh

            manager.cancel()
            await manager

        fresh.disconnect.assert_awaited_once()
        assert new_listener.cancelled()
        old_listener.cancel()


async def test_setup_entry_success(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test successful setup of a config entry."""
    assert mock_setup_entry.state is ConfigEntryState.LOADED
    assert DOMAIN in hass.data
    assert mock_setup_entry.entry_id in hass.data[DOMAIN]
    assert "coordinator" in hass.data[DOMAIN][mock_setup_entry.entry_id]


async def test_setup_entry_auth_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setup fails with ConfigEntryAuthFailed on auth error."""
    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(side_effect=AuthError("Bad credentials"))
    mock_auth.set_tokens = MagicMock()

    mock_ws = AsyncMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.get_listener_task = MagicMock(return_value=None)

    mock_signaling = AsyncMock()
    mock_signaling.is_connected = False

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=AsyncMock()),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_ws),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    # ConfigEntryAuthFailed triggers reauth. Since async_step_reauth is
    # implemented, HA sets the entry state to SETUP_ERROR.
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_setup_entry_api_failure(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test setup raises ConfigEntryNotReady on API error."""
    mock_auth = MagicMock()
    mock_auth.get_access_token = AsyncMock(side_effect=ApiError(503, "Service unavailable"))
    mock_auth.set_tokens = MagicMock()

    mock_ws = AsyncMock()
    mock_ws.connect = AsyncMock()
    mock_ws.disconnect = AsyncMock()
    mock_ws.get_listener_task = MagicMock(return_value=None)

    mock_signaling = AsyncMock()
    mock_signaling.is_connected = False

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=AsyncMock()),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_ws),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        mock_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_empty_topology_at_boot_keeps_entities_and_retries(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
) -> None:
    """Regression test for issue #68.

    A transient empty topology at startup must NOT delete previously
    registered entities: setup has to fail with ConfigEntryNotReady
    (SETUP_RETRY) instead of proceeding with zero modules and letting
    cleanup_orphaned_entities wipe the registry.
    """
    mock_config_entry.add_to_hass(hass)

    # A lock entity registered by a previous healthy boot
    ent_reg = er.async_get(hass)
    lock_unique_id = f"{mock_config_entry.entry_id}_lock_d9a63b-a06f-2ef633a2f785"
    lock_entry = ent_reg.async_get_or_create("lock", DOMAIN, lock_unique_id, config_entry=mock_config_entry)

    # This boot, the cloud returns an empty topology
    mock_account.homes = {}

    with (
        patch("custom_components.bticino_intercom.AuthHandler", return_value=mock_auth_handler),
        patch("custom_components.bticino_intercom.AsyncAccount", return_value=mock_account),
        patch("custom_components.bticino_intercom.WebsocketClient", return_value=mock_websocket_client),
        patch("custom_components.bticino_intercom.SignalingClient", return_value=mock_signaling_client),
        patch("custom_components.bticino_intercom.Store") as mock_store_cls,
    ):
        mock_store_cls.return_value.async_load = AsyncMock(return_value=None)
        mock_store_cls.return_value.async_save = AsyncMock()
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert ent_reg.async_get(lock_entry.entity_id) is not None


async def test_unload_entry(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test unloading a config entry."""
    assert mock_setup_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_setup_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_setup_entry.state is ConfigEntryState.NOT_LOADED
    # Domain data should be cleaned up
    assert mock_setup_entry.entry_id not in hass.data.get(DOMAIN, {})


async def test_reload_after_start_leaves_the_spent_listener_alone(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An entry set up while HA starts can be reloaded without a core error.

    Set up before HA has started, the entry waits for EVENT_HOMEASSISTANT_START
    with a one-time listener. Once that event fires the listener is gone, so
    unloading the entry afterwards - any reload after a restart - must not ask
    HA to remove it again: HA logs that as "Unable to remove unknown job
    listener" with a traceback, at ERROR.
    """
    hass.set_state(CoreState.not_running)
    await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
    assert mock_config_entry.state is ConfigEntryState.LOADED

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert "Unable to remove unknown job listener" not in caplog.text


async def test_unload_before_start_removes_the_waiting_listener(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
) -> None:
    """An entry unloaded before HA has started takes its start listener with it.

    Otherwise EVENT_HOMEASSISTANT_START would still start the push connection
    of an entry that is no longer loaded.
    """
    hass.set_state(CoreState.not_running)
    await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )

    with patch("custom_components.bticino_intercom._deferred_start_websocket", AsyncMock()) as deferred:
        assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        hass.set_state(CoreState.running)
        hass.bus.async_fire(EVENT_HOMEASSISTANT_START)
        await hass.async_block_till_done()

    deferred.assert_not_awaited()


async def test_coordinator_has_data_after_setup(
    hass: HomeAssistant,
    mock_setup_entry: MockConfigEntry,
) -> None:
    """Test coordinator data is populated after setup."""
    coordinator = hass.data[DOMAIN][mock_setup_entry.entry_id]["coordinator"]

    assert coordinator.data is not None
    assert "modules" in coordinator.data
    assert "homes" in coordinator.data
    assert coordinator.home_id == HOME_ID
    assert coordinator.main_device_id is not None


async def test_reject_call_service(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """Test reject_call service terminates the active call."""
    entry = await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    # Simulate an active call
    coordinator._active_call = {
        "session_id": "test-session",
        "module_id": EXTERNAL_UNIT_ID,
    }

    events = async_capture_events(hass, "bticino_intercom_call")

    await hass.services.async_call(DOMAIN, "reject_call", blocking=True)
    await hass.async_block_till_done()

    assert coordinator._active_call is None
    signaling_client.send_terminate.assert_called_once_with(session_id="test-session")
    end_events = [e for e in events if e.data.get("type") == "end"]
    assert len(end_events) == 1
    assert end_events[0].data["reason"] == "rejected"


async def test_reject_call_without_session_terminates_nothing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """A call with no session id must not terminate whatever session is current.

    Without an id, the signaling client falls back to its most recent session,
    which may be a live view on another entrance.
    """
    entry = await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    coordinator._active_call = {"session_id": None, "module_id": EXTERNAL_UNIT_ID}

    events = async_capture_events(hass, "bticino_intercom_call")

    await hass.services.async_call(DOMAIN, "reject_call", blocking=True)
    await hass.async_block_till_done()

    signaling_client.send_terminate.assert_not_called()
    assert coordinator._active_call is None
    end_events = [e for e in events if e.data.get("type") == "end"]
    assert len(end_events) == 1
    assert end_events[0].data["reason"] == "rejected"


async def test_reject_call_service_no_active_call(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_auth_handler: AsyncMock,
    mock_account: AsyncMock,
    mock_websocket_client: AsyncMock,
    mock_signaling_client: AsyncMock,
    enable_custom_integrations: None,
) -> None:
    """Test reject_call service is a no-op when no call is active."""
    entry = await _setup_integration(
        hass,
        mock_config_entry,
        mock_auth_handler,
        mock_account,
        mock_websocket_client,
        mock_signaling_client,
    )
    signaling_client = hass.data[DOMAIN][entry.entry_id]["signaling_client"]

    events = async_capture_events(hass, "bticino_intercom_call")

    await hass.services.async_call(DOMAIN, "reject_call", blocking=True)
    await hass.async_block_till_done()

    signaling_client.send_terminate.assert_not_called()
    assert len(events) == 0


async def test_deferred_start_websocket_starts_when_loaded(hass: HomeAssistant) -> None:
    """The WebSocket manager starts immediately when the entry is already LOADED."""
    from custom_components.bticino_intercom import _deferred_start_websocket

    entry = MagicMock()
    entry.state = ConfigEntryState.LOADED
    start_fn = AsyncMock()

    await _deferred_start_websocket(hass, entry, start_fn)

    start_fn.assert_awaited_once()


async def test_deferred_start_websocket_waits_for_loaded(hass: HomeAssistant) -> None:
    """It waits while the entry is still setting up, then starts once LOADED.

    Guards the fix for the issue #58 symptom where the manager started during
    SETUP_IN_PROGRESS and broke out of its loop permanently. Both start paths
    (HA-already-running and EVENT_HOMEASSISTANT_START) now go through this helper.
    """
    from unittest.mock import PropertyMock

    from custom_components.bticino_intercom import _deferred_start_websocket

    entry = MagicMock()
    type(entry).state = PropertyMock(side_effect=[ConfigEntryState.SETUP_IN_PROGRESS, ConfigEntryState.LOADED])
    start_fn = AsyncMock()

    with patch("custom_components.bticino_intercom.asyncio.sleep", AsyncMock()):
        await _deferred_start_websocket(hass, entry, start_fn)

    start_fn.assert_awaited_once()


async def test_deferred_start_websocket_gives_up_if_never_loaded(hass: HomeAssistant) -> None:
    """If the entry never reaches LOADED, the manager is not started."""
    from custom_components.bticino_intercom import _deferred_start_websocket

    entry = MagicMock()
    entry.state = ConfigEntryState.SETUP_IN_PROGRESS
    start_fn = AsyncMock()

    with patch("custom_components.bticino_intercom.asyncio.sleep", AsyncMock()):
        await _deferred_start_websocket(hass, entry, start_fn)

    start_fn.assert_not_awaited()
