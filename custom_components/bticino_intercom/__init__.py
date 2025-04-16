"""The BTicino integration."""

import asyncio
import logging
from functools import partial  # Import partial if needed for callback, but likely not
import websockets  # Import websockets for exception handling

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
    # EVENT_HOMEASSISTANT_START, # Removed
    # EVENT_HOMEASSISTANT_STOP, # Removed
)
from homeassistant.core import (
    HomeAssistant,
    # Event as HAEvent, # Removed if not used elsewhere
    # CoreState, # Removed if not used elsewhere
)
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

# Import the coordinator
from .coordinator import BticinoIntercomCoordinator
from .const import (
    DOMAIN,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY = 30  # Seconds to wait before attempting reconnect
WEBSOCKET_TASK_KEY = "websocket_connection_task"
WEBSOCKET_CLIENT_KEY = "websocket_client"
COORDINATOR_KEY = "coordinator"

# Removed START/STOP_LISTENER_REMOVE_KEY


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
    # Ensure the callback method exists on the coordinator instance
    websocket_client = WebsocketClient(
        auth_handler=auth_handler,
        message_callback=coordinator._handle_websocket_message,
        # Pass app_version etc. if needed/customized
    )
    # Assign the created client back to the coordinator (already done in coordinator __init__?)
    # Let's keep it explicit here for clarity, assuming coordinator might not always get it in init
    coordinator.websocket_client = websocket_client

    # Store coordinator and websocket client in hass.data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        COORDINATOR_KEY: coordinator,
        WEBSOCKET_CLIENT_KEY: websocket_client,
        # WEBSOCKET_TASK_KEY will be added later
    }

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

    # Forward the setup to platforms BEFORE starting the connection manager task
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # --- WebSocket Connection Manager Task ---
    async def _websocket_connection_manager() -> None:
        """Manage the WebSocket connection and reconnection."""
        _LOGGER.info("Starting WebSocket connection manager loop")
        while hass.is_running and entry.state == config_entries.ConfigEntryState.LOADED:
            _LOGGER.debug(
                "WebSocket manager loop iteration start. Entry state: %s", entry.state
            )
            listener_task = None
            try:
                # Attempt to connect (includes subscription and starting listener)
                _LOGGER.info("Attempting WebSocket connect...")
                await websocket_client.connect()
                _LOGGER.info("WebSocket connect call successful.")

                # Get the listener task from the client
                listener_task = websocket_client.get_listener_task()
                if listener_task:
                    # Wait for the listener task to complete (indicates disconnection)
                    _LOGGER.debug("Waiting for WebSocket listener task to complete...")
                    await listener_task
                    _LOGGER.info("WebSocket listener task finished cleanly.")
                else:
                    _LOGGER.warning(
                        "Could not get listener task after connect. Treating as disconnection."
                    )

            except asyncio.CancelledError:
                _LOGGER.info(
                    "WebSocket connection manager task cancelled during connect/listen."
                )
                break  # Exit the loop if cancelled
            except AuthError as err:
                _LOGGER.error(
                    "WebSocket connection failed due to AuthError: %s. Will retry.", err
                )
                # No need to raise ConfigEntryAuthFailed here, let it retry.
            except (
                PyBticinoException,
                websockets.exceptions.WebSocketException,
            ) as err:
                _LOGGER.warning(
                    "WebSocket connection error (%s): %s. Retrying in %d seconds...",
                    type(err).__name__,
                    err,
                    RECONNECT_DELAY,
                )
            except Exception as err:
                # Catch-all for other unexpected errors during connect/listen
                _LOGGER.exception(
                    "Unexpected error in WebSocket connection manager during connect/listen. Retrying in %d seconds...",
                    RECONNECT_DELAY,
                )
            finally:
                # Ensure disconnect is called before retrying or exiting
                _LOGGER.debug("WebSocket manager: Entering finally block for cleanup.")
                # Cancel listener task explicitly if it's still somehow running
                if listener_task and not listener_task.done():
                    listener_task.cancel()
                await websocket_client.disconnect()  # disconnect handles its own listener task cancellation too

            # Wait before attempting to reconnect, unless HA is stopping or entry unloaded
            if (
                hass.is_running
                and entry.state == config_entries.ConfigEntryState.LOADED
            ):
                _LOGGER.debug(
                    "Waiting %d seconds before WebSocket reconnect attempt...",
                    RECONNECT_DELAY,
                )
                try:
                    _LOGGER.debug(
                        "Waiting %d seconds before WebSocket reconnect attempt...",
                        RECONNECT_DELAY,
                    )
                    await asyncio.sleep(RECONNECT_DELAY)
                except asyncio.CancelledError:
                    _LOGGER.info(
                        "Reconnect delay interrupted by cancellation. Exiting manager loop."
                    )
                    break  # Exit loop if cancelled during sleep

        _LOGGER.info("WebSocket connection manager loop finished.")

    # Create and store the connection manager task
    connection_task = hass.async_create_task(
        _websocket_connection_manager(),
        name=f"{DOMAIN} WebSocket Manager - {entry.entry_id}",
    )
    hass.data[DOMAIN][entry.entry_id][WEBSOCKET_TASK_KEY] = connection_task

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Retrieve data from hass.data
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        _LOGGER.warning(
            "Cannot unload entry %s, data not found in hass.data", entry.entry_id
        )
        return False  # Or True depending on desired behavior if data is missing

    coordinator: BticinoIntercomCoordinator = entry_data.get(COORDINATOR_KEY)
    websocket_client: WebsocketClient = entry_data.get(WEBSOCKET_CLIENT_KEY)
    connection_task: asyncio.Task = entry_data.get(WEBSOCKET_TASK_KEY)

    # Remove the old listeners if they somehow still exist (shouldn't)
    # if remove_start_listener := entry_data.get(START_LISTENER_REMOVE_KEY):
    #     remove_start_listener()
    # if remove_stop_listener := entry_data.get(STOP_LISTENER_REMOVE_KEY):
    #     remove_stop_listener()

    # Cancel the connection manager task first
    if connection_task and not connection_task.done():
        _LOGGER.debug("Cancelling WebSocket connection manager task")
        connection_task.cancel()
        try:
            await connection_task
        except asyncio.CancelledError:
            _LOGGER.debug("WebSocket connection manager task cancelled successfully")
        except Exception:
            _LOGGER.exception("Error waiting for WebSocket task cancellation")

    # Then disconnect the client
    if websocket_client:
        _LOGGER.debug("Disconnecting WebSocket client")
        await websocket_client.disconnect()  # disconnect handles its own listener task

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove data from hass.data
    if unload_ok:
        # Pop the specific entry data
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # If the domain entry is now empty, remove the domain key itself
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)

    return unload_ok
