"""Recover rejected tokens without repeating pybticino's password fallback."""

from collections.abc import Awaitable, Callable

import aiohttp
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
        if cause.status_code == 401 or any(
            message in str(cause.error_message).lower() for message in ("invalid access token", "access token expired")
        ):
            return True
        # AsyncAccount can wrap an HTTP ApiError in ApiError(0, ...).
        cause = cause.__cause__
    return False


async def async_clear_tokens(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove persisted credentials before reauth or a forced password login."""
    await Store(hass, TOKEN_STORAGE_VERSION, f"{DOMAIN}.tokens.{entry.entry_id}").async_remove()


def is_credential_rejection(err: AuthError) -> bool:
    """Require evidence of rejected credentials, not just pybticino's AuthError.

    pybticino also wraps transport errors, server responses and unexpected
    exceptions in AuthError. Follow explicit causes, not exception context:
    a failed refresh can be followed by an unrelated password-login failure.
    Unknown failures remain retryable until the library exposes typed errors.
    """
    cause: BaseException | None = err
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ApiError):
            return cause.status_code in (400, 401)
        if isinstance(cause, (aiohttp.ClientError, TimeoutError, OSError)):
            return False
        if isinstance(cause, AuthError) and cause.__cause__ is None:
            return any(
                message in str(cause).lower() for message in ("invalid credentials", "bad credentials", "invalid_grant")
            )
        cause = cause.__cause__
    return False


def _authentication_error(err: PyBticinoException) -> AuthError | ApiError | None:
    """Unwrap typed causes: WebsocketClient wraps subscription auth errors."""
    cause: BaseException | None = err
    seen: set[int] = set()
    while isinstance(cause, PyBticinoException) and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, AuthError):
            return cause if is_credential_rejection(cause) else None
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
    An AuthError therefore must not cause another password login here. Only a
    credential rejection requests reauth; transient and unknown failures retry
    normally. An API can reject a locally unexpired token, requiring recovery.
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
