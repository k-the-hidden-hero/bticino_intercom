"""The BTicino integration."""

import asyncio
import logging

from pybticino import AuthHandler, AsyncAccount, WebsocketClient
from pybticino.exceptions import AuthError, ApiError, PyBticinoException

from homeassistant import (
    config_entries,
)  # Needed for config_entries.ConfigEntryNotReady
from homeassistant.config_entries import ConfigEntry  # Needed for type hints
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    # CONF_CLIENT_ID, # Keep commented out
    # CONF_CLIENT_SECRET, # Keep commented out
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Import the coordinator
from .coordinator import BticinoIntercomCoordinator
from .const import (
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BTicino from a config entry."""
    # hass.data.setdefault(DOMAIN, {}) # Not needed when using entry.runtime_data
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    # Remove client_id and client_secret from AuthHandler call
    auth_handler = AuthHandler(
        username=username,
        password=password,
        session=session,
    )

    try:
        # Validate credentials by attempting to get a token
        await auth_handler.get_access_token()
    except AuthError as err:
        _LOGGER.error("Authentication failed: %s", err)
        raise ConfigEntryAuthFailed from err
    except ApiError as err:
        _LOGGER.error("Client error during authentication: %s", err)
        raise ConfigEntryNotReady from err

    account = AsyncAccount(auth_handler)

    # Create the coordinator first, so we can pass its message handler
    coordinator = BticinoIntercomCoordinator(
        hass, entry, account, None
    )  # Pass None for websocket initially

    # Now create the websocket client, passing the coordinator's handler
    websocket_client = WebsocketClient(
        auth_handler, message_callback=coordinator._handle_websocket_message
    )
    # Assign the created client back to the coordinator
    coordinator.websocket_client = websocket_client

    # Store coordinator in runtime_data BEFORE first refresh
    entry.runtime_data = coordinator

    # Fetch initial data using the coordinator's standard method
    try:
        await coordinator.async_config_entry_first_refresh()
    except config_entries.ConfigEntryNotReady:
        # Let the coordinator raise ConfigEntryNotReady if fetch fails
        raise
    except (ApiError, PyBticinoException, AuthError) as err:
        # Catch specific library errors during first refresh if needed,
        # but UpdateFailed raised by _async_update_data should trigger ConfigEntryNotReady
        _LOGGER.error("Error during first coordinator refresh: %s", err)
        # AuthError is already handled above and raises ConfigEntryAuthFailed
        raise ConfigEntryNotReady(f"Failed to fetch initial data: {err}") from err

    # Start the WebSocket listener managed by the coordinator AFTER initial data fetch
    await coordinator.async_start_websocket()

    # Forward the setup to platforms.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: BticinoIntercomCoordinator = entry.runtime_data

    # Stop the WebSocket listener task via the coordinator
    await coordinator.async_stop_websocket()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # No need to manually pop from hass.data[DOMAIN] when using entry.runtime_data

    return unload_ok


# Optional: Add async_migrate_entry if needed for future schema changes
# async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
#     """Migrate old entry."""
#     _LOGGER.debug("Migrating from version %s", config_entry.version)
#     # Migration logic here
#     return True
