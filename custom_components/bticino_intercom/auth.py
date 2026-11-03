"""Recover rejected tokens without repeating pybticino's password fallback."""

from collections.abc import Awaitable, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from pybticino import AuthHandler
from pybticino.exceptions import ApiError, AuthError, PyBticinoException

from .const import DOMAIN, TOKEN_STORAGE_VERSION


def is_invalid_token_error(err: ApiError) -> bool:
    """Recognize rejected credentials, without treating every 403 as expiry."""
    cause: BaseException | None = err
    seen: set[int] = set()
    while isinstance(cause, ApiError) and id(cause) not in seen:
        seen.add(id(cause))
        if cause.status_code == 401 or "invalid access token" in cause.error_message.lower():
            return True
        # AsyncAccount can wrap an HTTP ApiError in ApiError(0, ...).
        cause = cause.__cause__
    return False


async def async_clear_tokens(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted credentials before reauth or a forced password login."""
    await Store(hass, TOKEN_STORAGE_VERSION, f"{DOMAIN}.tokens.{entry.entry_id}").async_remove()


def _authentication_error(err: PyBticinoException) -> AuthError | ApiError | None:
    """Unwrap typed causes: WebsocketClient wraps subscription auth errors."""
    cause: BaseException | None = err
    seen: set[int] = set()
    while isinstance(cause, PyBticinoException) and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, AuthError):
            return cause
        if isinstance(cause, ApiError):
            return cause if is_invalid_token_error(cause) else None
        cause = cause.__cause__
    return None


async def async_with_auth_retry[T](
    hass: HomeAssistant,
    entry: ConfigEntry,
    auth_handler: AuthHandler,
    operation: Callable[[], Awaitable[T]],
) -> T:
    """Retry an API-rejected token once; let HA handle failed password auth.

    get_access_token already falls back from refresh to password authentication.
    An AuthError therefore must not cause another password login here. An API
    can reject a locally unexpired token, however, requiring explicit recovery.
    """
    try:
        return await operation()
    except PyBticinoException as err:
        auth_error = _authentication_error(err)
        if auth_error is None:
            raise
        if isinstance(auth_error, AuthError):
            await async_clear_tokens(hass, entry)
            raise ConfigEntryAuthFailed from err

    await async_clear_tokens(hass, entry)
    try:
        await auth_handler.authenticate()
        return await operation()
    except PyBticinoException as err:
        if _authentication_error(err) is not None:
            await async_clear_tokens(hass, entry)
            raise ConfigEntryAuthFailed from err
        raise
