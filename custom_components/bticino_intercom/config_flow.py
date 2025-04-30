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
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import config_validation as cv, selector

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)

# Schema for the initial options step
STEP_INIT_OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional("light_as_lock", default=False): selector.BooleanSelector(),
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


class BticinoOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle BTicino options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Merge new options with existing ones, keeping existing if not provided
            updated_options = {**self.config_entry.options, **user_input}
            # Use self.async_create_entry which handles merging correctly
            return self.async_create_entry(title="", data=updated_options)

        # Get current options or set defaults for the form schema
        schema = vol.Schema(
            {
                vol.Optional(
                    "light_as_lock",
                    default=self.config_entry.options.get("light_as_lock", False),
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
        )


class BticinoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BTicino."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL
    # Store data between steps
    _config_data: dict[str, Any] = {}  # Store config data (user/pass/home_id)
    _config_title: str = ""  # Store title
    _homes_data: dict[str, Any] | None = None

    # Add static method to get options flow
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> BticinoOptionsFlowHandler:
        """Get the options flow for this handler."""
        return BticinoOptionsFlowHandler(config_entry)

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
                # Store user input temporarily
                self._config_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                self._config_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]

                if len(account.homes) == 0:
                    # No homes found for the account
                    return self.async_abort(reason="no_homes_found")
                elif len(account.homes) == 1:
                    # Only one home, store details and move to options step
                    home_id = list(account.homes.keys())[0]
                    home_name = list(account.homes.values())[0].name
                    self._config_data["home_id"] = home_id
                    self._config_title = home_name or user_input[CONF_USERNAME]
                    # Proceed to options step
                    return await self.async_step_init_options()
                else:
                    # Multiple homes, proceed to selection step
                    # Store homes data for selection step
                    self._homes_data = {
                        home.id: {"name": home.name, "module_count": len(home.modules)}
                        for home in account.homes.values()
                    }
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

            # Store selected home details
            self._config_data["home_id"] = selected_home_id
            self._config_title = selected_home_name or self._config_data[CONF_USERNAME]

            # Proceed to options step
            return await self.async_step_init_options()

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

    async def async_step_init_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial options step (e.g., light_as_lock)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # User submitted options, create the entry
            # Combine config data and options
            options_data = {
                "light_as_lock": user_input.get("light_as_lock", False),
            }
            return self.async_create_entry(
                title=self._config_title, data=self._config_data, options=options_data
            )

        # Show the options form
        return self.async_show_form(
            step_id="init_options", data_schema=STEP_INIT_OPTIONS_SCHEMA, errors=errors
        )


# Optional: Add support for reauthentication if tokens expire
# class BticinoOptionsFlowHandler(config_entries.OptionsFlow):
#     ... # Implementation for options flow if needed later
