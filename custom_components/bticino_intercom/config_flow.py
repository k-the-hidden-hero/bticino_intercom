"""Config flow for BTicino integration."""

import logging
from typing import Any

import voluptuous as vol
from pybticino import AuthHandler
from pybticino.exceptions import AuthError, ApiError

from homeassistant import config_entries
from homeassistant.const import (
    CONF_USERNAME,
    CONF_PASSWORD,
    # CONF_CLIENT_ID, # Removed
    # CONF_CLIENT_SECRET, # Removed
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

# Import AsyncAccount needed for topology fetch
from pybticino import AsyncAccount


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> AuthHandler:
    """Validate the user input allows us to connect and return the auth handler."""
    session = async_get_clientsession(hass)
    auth_handler = AuthHandler(  # client_id/secret handled internally by library
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        session=session,
    )
    # Validate credentials by attempting to get a token
    await auth_handler.get_access_token()

    # Return the validated auth handler
    return auth_handler


class BticinoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BTicino."""

    VERSION = 1
    # Store data between steps
    _user_input: dict[str, Any] | None = None
    _homes_data: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            # Prevent duplicate entries for the same username
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            auth_handler = None
            account = None
            try:
                # Validate credentials and get auth handler
                auth_handler = await validate_input(self.hass, user_input)
                # Create account client to fetch topology
                account = AsyncAccount(auth_handler)
                await account.async_update_topology()

            except AuthError:
                errors["base"] = "invalid_auth"
            except ApiError as err:
                # Catch API errors during validation or topology fetch
                _LOGGER.error("API Error during setup: %s", err)
                errors["base"] = "cannot_connect"
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"

            # If no errors after validation and topology fetch
            if not errors and account and account.homes:
                # Store user input for later use
                self._user_input = user_input
                # Store homes data for selection step
                self._homes_data = {
                    home.id: {"name": home.name, "module_count": len(home.modules)}
                    for home in account.homes.values()
                }

                if len(account.homes) == 0:
                    # No homes found for the account
                    return self.async_abort(reason="no_homes_found")
                elif len(account.homes) == 1:
                    # Only one home, create entry directly
                    home_id = list(account.homes.keys())[0]
                    home_name = list(account.homes.values())[0].name
                    config_data = {
                        CONF_USERNAME: user_input[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "home_id": home_id,  # Add selected home_id
                    }
                    # Use home name as title if available, otherwise username
                    title = home_name or user_input[CONF_USERNAME]
                    return self.async_create_entry(title=title, data=config_data)
                else:
                    # Multiple homes, proceed to selection step
                    return await self.async_step_select_home()

            # If errors occurred or no homes found after successful auth/topology
            elif not errors and account and not account.homes:
                return self.async_abort(reason="no_homes_found")

            # If errors occurred during validation/topology, show form again
            # Close the session if auth_handler was created but setup failed
            if auth_handler:
                await auth_handler.close_session()

        # Show the initial form again if user_input is None or errors occurred
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_select_home(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the home selection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_home_id = user_input["home_id"]
            selected_home_name = self._homes_data[selected_home_id]["name"]

            # Combine original user input with selected home_id
            config_data = {
                CONF_USERNAME: self._user_input[CONF_USERNAME],
                CONF_PASSWORD: self._user_input[CONF_PASSWORD],
                "home_id": selected_home_id,
            }
            # Use home name as title if available, otherwise username
            title = selected_home_name or self._user_input[CONF_USERNAME]

            # Ensure unique ID is set based on username + home_id? Or just username?
            # Using just username might prevent adding the same account for different homes.
            # Let's stick to username for now, but this might need review.
            # await self.async_set_unique_id(f"{self._user_input[CONF_USERNAME]}_{selected_home_id}")

            return self.async_create_entry(title=title, data=config_data)

        # Prepare the list for the dropdown
        homes_selection = {
            home_id: f"{data['name']} ({data['module_count']} modules)"
            for home_id, data in self._homes_data.items()
        }

        select_home_schema = vol.Schema(
            {vol.Required("home_id"): vol.In(homes_selection)}
        )

        return self.async_show_form(
            step_id="select_home", data_schema=select_home_schema, errors=errors
        )


# Optional: Add support for reauthentication if tokens expire
# class BticinoOptionsFlowHandler(config_entries.OptionsFlow):
#     ... # Implementation for options flow if needed later
