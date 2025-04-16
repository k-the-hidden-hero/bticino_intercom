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
    EVENT_HOMEASSISTANT_START,  # Import event for delayed start
    EVENT_HOMEASSISTANT_STOP,  # Import event for stopping WS
)
from homeassistant.core import (
    HomeAssistant,
    Event as HAEvent,
    CoreState,
)  # Import HAEvent type hint and CoreState
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Import the coordinator
from .coordinator import BticinoIntercomCoordinator
from .const import (
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

# Key to store the listener removal function in runtime_data
START_LISTENER_REMOVE_KEY = "start_listener_remove"
STOP_LISTENER_REMOVE_KEY = "stop_listener_remove"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BTicino from a config entry."""
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

    # Forward the setup to platforms.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Define the function to start the websocket listener
    async def start_websocket_listener(event: HAEvent) -> None:
        """Start the WebSocket listener after Home Assistant has started."""
        _LOGGER.debug("Home Assistant started, starting WebSocket listener.")
        # Ensure coordinator is still available (might have been unloaded)
        if coord := entry.runtime_data:
            await coord.async_start_websocket()

    # Define the function to stop the websocket listener on HA stop
    async def stop_websocket_listener(event: HAEvent) -> None:
        """Stop the WebSocket listener when Home Assistant stops."""
        _LOGGER.debug("Home Assistant stopping, stopping WebSocket listener.")
        if coord := entry.runtime_data:
            await coord.async_stop_websocket()

    # Schedule the WebSocket start after HA starts
    if hass.state == CoreState.running:
        # HA is already running, start WS immediately
        await coordinator.async_start_websocket()
        start_listener_remove = None  # No listener needed
    else:
        # HA is starting, listen for the start event
        start_listener_remove = hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_START, start_websocket_listener
        )

    # Listen for HA stop event to gracefully disconnect WebSocket
    stop_listener_remove = hass.bus.async_listen_once(
        EVENT_HOMEASSISTANT_STOP, stop_websocket_listener
    )

    # Store the removal functions in runtime_data
    entry.runtime_data = {
        "coordinator": coordinator,
        START_LISTENER_REMOVE_KEY: start_listener_remove,
        STOP_LISTENER_REMOVE_KEY: stop_listener_remove,
    }

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    runtime_data = entry.runtime_data
    coordinator: BticinoIntercomCoordinator = runtime_data["coordinator"]

    # Remove the listeners if they exist
    if remove_start_listener := runtime_data.get(START_LISTENER_REMOVE_KEY):
        remove_start_listener()
    if remove_stop_listener := runtime_data.get(STOP_LISTENER_REMOVE_KEY):
        remove_stop_listener()

    # Stop the WebSocket listener task via the coordinator
    await coordinator.async_stop_websocket()

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # No need to manually pop from hass.data[DOMAIN] when using entry.runtime_data

    return unload_ok
