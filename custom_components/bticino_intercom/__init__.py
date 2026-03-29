"""The BTicino integration."""

import asyncio
import logging
from contextlib import suppress

import websockets  # Import websockets for exception handling
from homeassistant import (
    config_entries,
)  # Needed for config_entries.ConfigEntryNotReady
from homeassistant.config_entries import ConfigEntry  # Needed for type hints
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    # CONF_CLIENT_ID, # Keep commented out
    # CONF_CLIENT_SECRET, # Keep commented out
    EVENT_HOMEASSISTANT_START,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.core import (
    CoreState,  # Re-add CoreState for check
    HomeAssistant,
)
from homeassistant.core import (
    Event as HAEvent,  # Re-add HAEvent type hint
)
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pybticino import AsyncAccount, AuthHandler, WebsocketClient
from pybticino.exceptions import ApiError, AuthError, PyBticinoException

from .const import (
    DOMAIN,
    PLATFORMS,
)
from .coordinator import BticinoIntercomCoordinator

_LOGGER = logging.getLogger(__name__)

RECONNECT_DELAY = 30  # Default delay, overridden by smart backoff
BOOT_RETRY_DELAYS = [5, 10, 30, 30, 30, 60]  # Backoff al boot
RUNTIME_RETRY_DELAYS = [5, 15, 30, 60, 120]  # Backoff durante vita normale
WEBSOCKET_TASK_KEY = "websocket_connection_task"
WEBSOCKET_CLIENT_KEY = "websocket_client"
COORDINATOR_KEY = "coordinator"
START_LISTENER_REMOVE_KEY = "start_listener_remove"


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
    coordinator = BticinoIntercomCoordinator(hass, entry, account, None)  # Pass None for websocket initially

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

    # Forward the setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Add the update listener
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # --- Define WebSocket Connection Manager Task and Start Logic ---
    async def _websocket_connection_manager() -> None:
        """Manage the WebSocket connection and reconnection."""
        # This function now runs *after* HA has started
        _LOGGER.info("Starting WebSocket connection manager task function (triggered by HA start)")
        is_boot = True  # First connection attempt after startup
        attempt = 0  # Retry counter, resets on successful connection
        # Outer try block to catch unexpected errors in the loop structure itself
        try:
            while hass.is_running and entry.state == config_entries.ConfigEntryState.LOADED:
                _LOGGER.debug(
                    "WebSocket manager loop iteration start. hass.is_running=%s, entry.state=%s",
                    hass.is_running,
                    entry.state,
                )
                listener_task = None
                try:
                    # Attempt to connect (includes subscription and starting listener)
                    _LOGGER.info("Attempting WebSocket connect... (calling websocket_client.connect)")
                    await websocket_client.connect()
                    _LOGGER.info("WebSocket connect call completed without raising immediate exception.")
                    # Connection succeeded: reset retry state
                    attempt = 0
                    is_boot = False

                    # Get the listener task from the client
                    listener_task = websocket_client.get_listener_task()
                    if listener_task:
                        # Wait for the listener task, but periodically check for stale WS
                        _LOGGER.debug("Waiting for WebSocket listener task to complete...")
                        while not listener_task.done():
                            try:
                                await asyncio.wait_for(asyncio.shield(listener_task), timeout=60)
                            except TimeoutError:
                                # Check if coordinator flagged WS as stale
                                if coordinator.ws_stale:
                                    _LOGGER.warning("WebSocket flagged as stale by coordinator, forcing reconnect")
                                    listener_task.cancel()
                                    with suppress(asyncio.CancelledError):
                                        await listener_task
                                    break
                                # Otherwise keep waiting
                            except asyncio.CancelledError:
                                raise
                        else:
                            _LOGGER.info("WebSocket listener task finished cleanly.")
                    else:
                        _LOGGER.warning("Could not get listener task after connect. Treating as disconnection.")

                except asyncio.CancelledError:
                    _LOGGER.info("WebSocket connection manager task cancelled during connect/listen.")
                    # Important: Re-raise CancelledError so the outer try block doesn't treat it as an unexpected error
                    raise
                except AuthError as err:
                    _LOGGER.error(
                        "WebSocket connection failed due to AuthError: %s. Will retry.",
                        err,
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
                except Exception:
                    # Catch-all for other unexpected errors during connect/listen
                    _LOGGER.exception(
                        "Unexpected error in WebSocket connection manager during connect/listen. Retrying in %d seconds...",
                        RECONNECT_DELAY,
                    )
                finally:  # Finally for the inner try (connect/listen attempt)
                    # Ensure disconnect is called before retrying or exiting this attempt
                    _LOGGER.debug("WebSocket manager: Entering finally block for cleanup after attempt.")
                    # Cancel listener task explicitly if it's still somehow running
                    if listener_task and not listener_task.done():
                        _LOGGER.debug("Cancelling listener task in finally block.")
                        listener_task.cancel()
                        # Wait briefly for cancellation to propagate if needed
                        with suppress(asyncio.CancelledError):
                            await listener_task
                    await websocket_client.disconnect()  # disconnect handles its own listener task cancellation too
                    _LOGGER.debug("WebSocket client disconnected in finally block.")

                # --- Smart Reconnect Delay Logic ---
                _LOGGER.debug("WebSocket manager: Reached reconnect delay logic.")
                if hass.is_running and entry.state == config_entries.ConfigEntryState.LOADED:
                    # Pick delay from appropriate backoff schedule
                    delays = BOOT_RETRY_DELAYS if is_boot else RUNTIME_RETRY_DELAYS
                    delay = delays[min(attempt, len(delays) - 1)]
                    attempt += 1
                    _LOGGER.info(
                        "WebSocket reconnect attempt %d (%s), waiting %ds...",
                        attempt,
                        "boot" if is_boot else "runtime",
                        delay,
                    )
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        _LOGGER.info("Reconnect delay interrupted by cancellation. Exiting manager loop.")
                        break  # Exit while loop if cancelled during sleep
                else:
                    # If HA is stopping or entry unloaded, exit the loop
                    _LOGGER.info("Exiting WebSocket manager loop because hass is not running or entry is not loaded.")
                    break  # Exit while loop

        # Catch any unexpected error in the main loop logic itself (outside the inner try)
        # Also catch CancelledError here to log the final exit reason properly
        except asyncio.CancelledError:
            _LOGGER.info("WebSocket connection manager task explicitly cancelled.")
        except Exception as loop_err:
            _LOGGER.exception(
                "Unexpected error caught in WebSocket connection manager outer try block: %s",
                loop_err,
            )
        finally:
            # This finally block corresponds to the outer try
            _LOGGER.info(
                "WebSocket connection manager task function finished. hass.is_running=%s, entry.state=%s",
                hass.is_running,
                entry.state,
            )

    async def _async_start_websocket_manager(event: HAEvent | None = None) -> None:
        """Start the WebSocket manager task."""
        # Ensure the task isn't already running (e.g., if HA restarts quickly)
        # Check if entry still exists in hass.data
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not entry_data or entry_data.get(WEBSOCKET_TASK_KEY):
            _LOGGER.debug("WebSocket manager task already exists or entry data missing.")
            return

        _LOGGER.debug("Creating and storing WebSocket connection manager task.")
        connection_task = hass.async_create_background_task(
            _websocket_connection_manager(),
            name=f"{DOMAIN} WebSocket Manager - {entry.entry_id}",
        )
        # Store task in the existing entry_data dictionary
        entry_data[WEBSOCKET_TASK_KEY] = connection_task

    # Schedule the WebSocket start after HA starts
    if hass.state == CoreState.running:
        # HA is already running, start WS manager task immediately
        _LOGGER.debug("Home Assistant already running, starting WebSocket manager directly.")
        await _async_start_websocket_manager()
        start_listener_remove = None  # No listener needed
    else:
        # HA is starting, listen for the start event
        _LOGGER.debug("Home Assistant starting, scheduling WebSocket manager via listener.")
        start_listener_remove = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, _async_start_websocket_manager)

    # Store the removal function for the start listener
    hass.data[DOMAIN][entry.entry_id][START_LISTENER_REMOVE_KEY] = start_listener_remove

    # Cancel WebSocket task on HA shutdown to prevent blocking
    async def _async_cancel_websocket_on_stop(event: HAEvent) -> None:
        """Cancel the WebSocket manager task when HA is stopping."""
        entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        ws_task = entry_data.get(WEBSOCKET_TASK_KEY)
        if ws_task and not ws_task.done():
            _LOGGER.debug("Cancelling WebSocket manager task on HA stop.")
            ws_task.cancel()
            with suppress(asyncio.CancelledError):
                await ws_task

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_cancel_websocket_on_stop))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading BTicino integration entry: %s", entry.entry_id)

    # Stop the WebSocket connection manager task
    if entry_data := hass.data.get(DOMAIN, {}).get(entry.entry_id):
        websocket_task = entry_data.pop(WEBSOCKET_TASK_KEY, None)
        if websocket_task:
            _LOGGER.debug("Cancelling WebSocket manager task during unload.")
            websocket_task.cancel()
            with suppress(asyncio.CancelledError):
                await websocket_task
            _LOGGER.debug("WebSocket manager task cancelled.")

        # Clean up websocket client (disconnect is handled by task cancellation finally)
        entry_data.pop(WEBSOCKET_CLIENT_KEY, None)

        # Clean up start listener if it exists
        start_listener_remove = entry_data.pop(START_LISTENER_REMOVE_KEY, None)
        if start_listener_remove:
            _LOGGER.debug("Removing HA start listener during unload.")
            try:
                start_listener_remove()
            except ValueError:
                _LOGGER.debug("Listener was already removed.")
            except Exception as exc:  # Catch other potential errors during removal
                _LOGGER.warning("Error removing start listener: %s", exc)

    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Clean up hass.data[DOMAIN][entry.entry_id] only if unload succeeded
    if unload_ok and DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.debug("Removed entry data for %s from hass.data", entry.entry_id)
        # If this was the last entry, remove the domain key
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
            _LOGGER.debug("Removed domain %s from hass.data", DOMAIN)

    _LOGGER.info("BTicino integration entry %s unloaded: %s", entry.entry_id, unload_ok)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    _LOGGER.info("Reloading BTicino integration entry due to options update: %s", entry.entry_id)
    # Explicitly call the reload function to ensure the unload/setup cycle runs
    await hass.config_entries.async_reload(entry.entry_id)
    # The listener registration itself ensures this function is called.
    # The call above forces HA to unload and set up the entry again.
