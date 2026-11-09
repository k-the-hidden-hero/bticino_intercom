"""Regression coverage for token recovery and authentication failures."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pybticino import AsyncAccount, AuthHandler, WebsocketClient
from pybticino.exceptions import ApiError, AuthError, PyBticinoException
from websockets.protocol import State

from custom_components.bticino_intercom.auth import async_with_auth_retry
from custom_components.bticino_intercom.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.mark.parametrize("password_valid", [True, False])
async def test_real_library_refresh_fallback_is_not_duplicated(hass, mock_config_entry, password_valid):
    """Verify the real get_access_token fallback before omitting a second login."""
    auth = AuthHandler(username="test@example.com", password="secret", session=MagicMock())
    auth.set_tokens("expired", "refresh", expires_at=0)

    async def login():
        if not password_valid:
            raise AuthError("Bad credentials")
        auth.set_tokens("new", "new-refresh", expires_at=9999999999)

    with (
        patch.object(auth, "_refresh_access_token", AsyncMock(side_effect=AuthError("Invalid refresh token"))),
        patch.object(auth, "authenticate", AsyncMock(side_effect=login)) as authenticate,
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
    ):
        if password_valid:
            assert await async_with_auth_retry(hass, mock_config_entry, auth, auth.get_access_token) == "new"
            clear.assert_not_awaited()
        else:
            with pytest.raises(ConfigEntryAuthFailed):
                await async_with_auth_retry(hass, mock_config_entry, auth, auth.get_access_token)
            clear.assert_awaited_once()
    authenticate.assert_awaited_once()


async def test_real_account_wrapped_http_401_recovers(hass, mock_config_entry):
    """AsyncAccount wraps HTTP ApiError in ApiError(0); retain its typed cause."""
    session = MagicMock(closed=False)
    rejected = MagicMock(status=401)
    rejected.text = AsyncMock(return_value="Unauthorized")
    rejected.json = AsyncMock(return_value={"error": {"message": "Unauthorized"}})
    accepted = MagicMock(status=200)
    accepted.json = AsyncMock(return_value={"body": {"homes": []}})
    contexts = [MagicMock(), MagicMock()]
    contexts[0].__aenter__ = AsyncMock(return_value=rejected)
    contexts[1].__aenter__ = AsyncMock(return_value=accepted)
    session.post.side_effect = contexts
    auth = AuthHandler(username="test@example.com", password="secret", session=session)
    account = AsyncAccount(auth)
    with (
        patch.object(auth, "get_access_token", AsyncMock(return_value="token")),
        patch.object(auth, "authenticate", AsyncMock()) as authenticate,
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
    ):
        await async_with_auth_retry(hass, mock_config_entry, auth, account.async_update_topology)
    authenticate.assert_awaited_once()
    clear.assert_awaited_once()
    assert session.post.call_count == 2


async def test_real_websocket_connect_preserves_auth_cause(hass, mock_config_entry):
    """Exercise pybticino's real exception wrapping, not a mocked connect error."""
    auth = MagicMock(spec=AuthHandler)
    auth.get_access_token = AsyncMock(side_effect=AuthError("Bad credentials"))
    auth.authenticate = AsyncMock()
    client = WebsocketClient(auth_handler=auth, message_callback=AsyncMock())
    connection = MagicMock(state=State.OPEN)
    connection.close = AsyncMock()
    with (
        patch("pybticino.websocket.websockets.connect", AsyncMock(return_value=connection)),
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_with_auth_retry(hass, mock_config_entry, auth, client.connect)
    clear.assert_awaited_once()
    connection.close.assert_awaited_once()
    auth.authenticate.assert_not_awaited()


async def test_wrapped_api_rejection_retries_once(hass, mock_config_entry):
    """WebSocket subscription wrappers must retain API-token recovery."""
    error = PyBticinoException("WebSocket subscription failed")
    error.__cause__ = ApiError(403, "Invalid access token")
    operation = AsyncMock(side_effect=[error, None])
    auth = AsyncMock()
    with patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock):
        await async_with_auth_retry(hass, mock_config_entry, auth, operation)
    auth.authenticate.assert_awaited_once()
    assert operation.await_count == 2


@pytest.mark.parametrize(
    "error",
    [
        ApiError(401, "Unauthorized"),
        ApiError(403, "Invalid access token"),
        ApiError(403, "Access token expired (code 3)"),
    ],
)
async def test_rejected_token_retries_once(hass, mock_config_entry, error):
    """A locally unexpired but rejected token needs a fresh password login."""
    auth = AsyncMock()
    operation = AsyncMock(side_effect=[error, "recovered"])
    with patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear:
        assert await async_with_auth_retry(hass, mock_config_entry, auth, operation) == "recovered"
    clear.assert_awaited_once_with(hass, mock_config_entry)
    auth.authenticate.assert_awaited_once()
    assert operation.await_count == 2


async def test_auth_error_does_not_repeat_library_password_fallback(hass, mock_config_entry):
    """pybticino already tried the stored password before raising AuthError."""
    auth = AsyncMock()
    operation = AsyncMock(side_effect=AuthError("Bad credentials"))
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_with_auth_retry(hass, mock_config_entry, auth, operation)
    clear.assert_awaited_once()
    auth.authenticate.assert_not_awaited()
    operation.assert_awaited_once()


@pytest.mark.parametrize("error", [ApiError(403, "Permission denied"), ApiError(503, "Service unavailable")])
async def test_other_api_errors_preserve_tokens(hass, mock_config_entry, error):
    """Permissions and outages must not cause another password login or reauth."""
    auth = AsyncMock()
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        pytest.raises(ApiError),
    ):
        await async_with_auth_retry(hass, mock_config_entry, auth, AsyncMock(side_effect=error))
    clear.assert_not_awaited()
    auth.authenticate.assert_not_awaited()


async def test_repeated_rejection_is_bounded_and_clears_new_tokens(hass, mock_config_entry):
    """If a fresh token is also rejected, stop after one login and request reauth."""
    auth = AsyncMock()
    operation = AsyncMock(side_effect=ApiError(403, "Invalid access token"))
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_with_auth_retry(hass, mock_config_entry, auth, operation)
    assert clear.await_count == 2
    auth.authenticate.assert_awaited_once()
    assert operation.await_count == 2


@pytest.mark.parametrize("error", [AuthError("Bad credentials"), ApiError(401, "Unauthorized")])
async def test_failed_password_login_requests_reauth(hass, mock_config_entry, error):
    """A forced password login failure must reach HA's reauth handling."""
    auth = AsyncMock()
    auth.authenticate.side_effect = error
    operation = AsyncMock(side_effect=ApiError(403, "Invalid access token"))
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_with_auth_retry(hass, mock_config_entry, auth, operation)
    operation.assert_awaited_once()


@pytest.mark.parametrize("method", ["async_update_topology", "async_get_home_status", "async_get_events"])
async def test_coordinator_recovers_rejected_token(coordinator, mock_account, method):
    """All three polling stages must propagate rejection and retry the update."""
    coordinator.account = mock_account
    operation = getattr(mock_account, method)
    operation.side_effect = [ApiError(403, "Invalid access token"), operation.return_value]
    with patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear:
        data = await coordinator._async_update_data()
    assert data["modules"]
    assert coordinator._consecutive_transient_errors == 0
    clear.assert_awaited_once()
    mock_account.auth_handler.authenticate.assert_awaited_once()
    assert operation.await_count == 2


@pytest.mark.parametrize("method", ["async_update_topology", "async_get_home_status", "async_get_events"])
async def test_coordinator_auth_error_requests_reauth(coordinator, mock_account, method):
    """Auth failures must never be hidden by stale status/events data."""
    coordinator.account = mock_account
    getattr(mock_account, method).side_effect = AuthError("Bad credentials")
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await coordinator._async_update_data()
    clear.assert_awaited_once()
    mock_account.auth_handler.authenticate.assert_not_awaited()


async def test_coordinator_password_service_outage_is_retryable(coordinator, mock_account):
    """A 503 during forced login is an update failure, not a reauth prompt."""
    coordinator.account = mock_account
    mock_account.async_update_topology.side_effect = ApiError(403, "Invalid access token")
    mock_account.auth_handler.authenticate.side_effect = ApiError(503, "Service unavailable")
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock),
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()


@pytest.mark.parametrize("valid_password", [True, False])
async def test_reauth_clears_tokens_only_after_validation(hass, mock_config_entry, valid_password):
    """Successful reauth removes stale persisted tokens before reload."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": mock_config_entry.entry_id},
        data=mock_config_entry.data,
    )
    order = []
    with (
        patch("custom_components.bticino_intercom.config_flow.validate_input", new_callable=AsyncMock) as validate,
        patch("custom_components.bticino_intercom.auth.Store") as store,
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as reload,
        patch.object(hass.config_entries, "async_update_entry", wraps=hass.config_entries.async_update_entry) as update,
    ):
        update.side_effect = lambda *args, **kwargs: order.append("update") or update._mock_wraps(*args, **kwargs)
        store.return_value.async_remove = AsyncMock(side_effect=lambda: order.append("remove"))
        reload.side_effect = lambda entry_id: order.append("reload")
        if not valid_password:
            validate.side_effect = AuthError("Bad credentials")
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "new-secret"})
        if valid_password:
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "reauth_successful"
            assert mock_config_entry.data["password"] == "new-secret"
            assert order == ["remove", "update", "reload"]
        else:
            assert result["errors"] == {"base": "invalid_auth"}
            assert mock_config_entry.data["password"] == "testpass"
            assert order == []


async def test_setup_continues_after_api_token_recovery(
    hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
):
    """Regression for 4873351: recovered API errors must not fall through to failure."""
    from .conftest import _setup_integration

    mock_auth_handler.get_access_token.side_effect = [ApiError(403, "Invalid access token"), "new-token"]
    with patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock):
        entry = await _setup_integration(
            hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
        )
    assert entry.state is config_entries.ConfigEntryState.LOADED
    mock_auth_handler.authenticate.assert_awaited_once()


@pytest.mark.parametrize("stage", ["connect", "resubscribe"])
async def test_websocket_auth_failure_requests_reauth_and_disconnects(
    hass: HomeAssistant,
    mock_config_entry,
    mock_auth_handler,
    mock_account,
    mock_websocket_client,
    mock_signaling_client,
    stage,
):
    """Manager exits through cleanup on auth failure, without reconnect storms."""
    from .conftest import _setup_integration

    release = asyncio.Event()
    listener = None
    if stage == "resubscribe":
        listener = asyncio.create_task(release.wait())
        mock_websocket_client.get_listener_task.return_value = listener
    getattr(mock_websocket_client, stage).side_effect = AuthError("Bad credentials")
    with (
        patch("custom_components.bticino_intercom.TOKEN_RESUBSCRIBE_INTERVAL", 0),
        patch("custom_components.bticino_intercom._wait_for_websocket_listener", AsyncMock(return_value=False)),
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        patch.object(mock_config_entry, "async_start_reauth", MagicMock()) as reauth,
    ):
        entry = await _setup_integration(
            hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
        )
        manager = hass.data[DOMAIN][entry.entry_id]["websocket_connection_task"]
        await asyncio.wait_for(manager, timeout=5)
    clear.assert_awaited_once()
    reauth.assert_called_once_with(hass)
    mock_websocket_client.disconnect.assert_awaited_once()
    mock_websocket_client.connect.assert_awaited_once()
    if listener is not None:
        assert listener.cancelled()


@pytest.mark.parametrize("failure", ["timeout", "network", "503", "429", "missing_tokens", "invalid_grant", "401"])
async def test_real_password_auth_classification(hass, mock_config_entry, failure):
    """Classify real pybticino wrappers, including refresh timeout then 503 (#93)."""
    session = MagicMock(closed=False)
    response = MagicMock(status=int(failure) if failure.isdigit() else 400 if failure == "invalid_grant" else 200)
    response.text = AsyncMock(return_value="token endpoint error")
    response.json = AsyncMock(return_value={"error": "invalid_grant" if failure == "invalid_grant" else "unavailable"})
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=response)
    if failure in ("timeout", "network"):
        context.__aenter__.side_effect = TimeoutError() if failure == "timeout" else aiohttp.ClientConnectionError()
    refresh_context = MagicMock()
    refresh_context.__aenter__ = AsyncMock(side_effect=TimeoutError())
    session.post.side_effect = [refresh_context, context]
    auth = AuthHandler(username="test@example.com", password="secret", session=session)
    auth.set_tokens("expired", "refresh", expires_at=0)
    with (
        patch.object(auth, "authenticate", wraps=auth.authenticate) as authenticate,
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
    ):
        expected = ConfigEntryAuthFailed if failure in ("invalid_grant", "401") else AuthError
        with pytest.raises(expected):
            await async_with_auth_retry(hass, mock_config_entry, auth, auth.get_access_token)
        authenticate.assert_awaited_once()
        if failure in ("invalid_grant", "401"):
            clear.assert_awaited_once()
        else:
            clear.assert_not_awaited()
            assert auth._refresh_token == "refresh"


@pytest.mark.parametrize("method", ["async_update_topology", "async_get_home_status", "async_get_events"])
@pytest.mark.parametrize("cause", [TimeoutError(), aiohttp.ClientConnectionError(), ApiError(503, "Unavailable"), None])
async def test_coordinator_transient_auth_error_is_retryable(coordinator, mock_account, method, cause):
    """Token-endpoint outages at any polling stage must not request reauth."""
    coordinator.account = mock_account
    error = AuthError("Authentication failed")
    error.__cause__ = cause
    operation = getattr(mock_account, method)
    operation.side_effect = error
    with (
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        pytest.raises(UpdateFailed),
    ):
        await coordinator._async_update_data()
    clear.assert_not_awaited()
    mock_account.auth_handler.authenticate.assert_not_awaited()
    operation.side_effect = None
    assert (await coordinator._async_update_data())["modules"]


async def test_setup_token_endpoint_outage_retries(
    hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
):
    """Setup must schedule a retry rather than discard credentials on an outage."""
    from .conftest import _setup_integration

    error = AuthError("Authentication failed")
    error.__cause__ = TimeoutError()
    mock_auth_handler.get_access_token.side_effect = error
    with patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear:
        entry = await _setup_integration(
            hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
        )
    assert entry.state is config_entries.ConfigEntryState.SETUP_RETRY
    clear.assert_not_awaited()


@pytest.mark.parametrize("stage", ["connect", "resubscribe"])
async def test_websocket_transient_auth_failure_reconnects(
    hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client, stage
):
    """An auth-service outage must clean up and reconnect without user input."""
    from .conftest import _setup_integration

    error = AuthError("Authentication failed")
    error.__cause__ = ApiError(503, "Unavailable")
    recovered = asyncio.Event()
    listener = None
    if stage == "resubscribe":
        listener = asyncio.create_task(asyncio.Event().wait())
        mock_websocket_client.get_listener_task.return_value = listener

    async def connect():
        if mock_websocket_client.connect.await_count == 1:
            if stage == "connect":
                raise error
        else:
            recovered.set()
            await asyncio.Event().wait()

    mock_websocket_client.connect.side_effect = connect
    mock_websocket_client.resubscribe.side_effect = error
    with (
        patch("custom_components.bticino_intercom.BOOT_RETRY_DELAYS", [0]),
        patch("custom_components.bticino_intercom.RUNTIME_RETRY_DELAYS", [0]),
        patch("custom_components.bticino_intercom.TOKEN_RESUBSCRIBE_INTERVAL", 0),
        patch("custom_components.bticino_intercom._wait_for_websocket_listener", AsyncMock(return_value=False)),
        patch("custom_components.bticino_intercom.auth.async_clear_tokens", new_callable=AsyncMock) as clear,
        patch.object(mock_config_entry, "async_start_reauth", MagicMock()) as reauth,
    ):
        entry = await _setup_integration(
            hass, mock_config_entry, mock_auth_handler, mock_account, mock_websocket_client, mock_signaling_client
        )
        manager = hass.data[DOMAIN][entry.entry_id]["websocket_connection_task"]
        try:
            await asyncio.wait_for(recovered.wait(), timeout=5)
        finally:
            manager.cancel()
            await manager
    clear.assert_not_awaited()
    reauth.assert_not_called()
    assert mock_websocket_client.connect.await_count == 2
    assert mock_websocket_client.disconnect.await_count == 2
    if listener is not None:
        assert listener.cancelled()
