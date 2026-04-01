"""Config flow for BTicino integration."""

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pybticino import AsyncAccount, AuthHandler
from pybticino.exceptions import ApiError, AuthError

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


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> AuthHandler:
    """Validate the user input allows us to connect and return the auth handler."""
    session = async_get_clientsession(hass)
    auth_handler = AuthHandler(
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        session=session,
    )
    await auth_handler.get_access_token()
    return auth_handler


class BticinoOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle BTicino options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            updated_options = {**self.config_entry.options, **user_input}
            return self.async_create_entry(title="", data=updated_options)

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
    _config_data: dict[str, Any]
    _config_title: str
    _homes_data: dict[str, Any] | None

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> BticinoOptionsFlowHandler:
        """Get the options flow for this handler."""
        return BticinoOptionsFlowHandler()

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth when credentials expire."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reauth confirmation step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_input(
                    self.hass,
                    {
                        CONF_USERNAME: self._reauth_entry.data[CONF_USERNAME],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
            except AuthError:
                errors["base"] = "invalid_auth"
            except ApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

            if not errors:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={
                "username": self._reauth_entry.data[CONF_USERNAME],
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        if not hasattr(self, "_config_data") or not self._config_data:
            self._config_data = {}
            self._config_title = ""
            self._homes_data = None

        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            auth_handler = None
            account = None
            try:
                auth_handler = await validate_input(self.hass, user_input)
                account = AsyncAccount(auth_handler)
                await account.async_update_topology()

            except AuthError:
                errors["base"] = "invalid_auth"
            except ApiError as err:
                _LOGGER.error("API Error during setup: %s", err)
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during setup")
                errors["base"] = "unknown"

            if not errors and account and account.homes:
                self._config_data[CONF_USERNAME] = user_input[CONF_USERNAME]
                self._config_data[CONF_PASSWORD] = user_input[CONF_PASSWORD]

                if len(account.homes) == 0:
                    return self.async_abort(reason="no_homes_found")
                elif len(account.homes) == 1:
                    home_id = next(iter(account.homes.keys()))
                    home_name = next(iter(account.homes.values())).name
                    self._config_data["home_id"] = home_id
                    self._config_title = home_name or user_input[CONF_USERNAME]
                    return await self.async_step_init_options()
                else:
                    self._homes_data = {
                        home.id: {"name": home.name, "module_count": len(home.modules)}
                        for home in account.homes.values()
                    }
                    return await self.async_step_select_home()

            elif not errors and account and not account.homes:
                return self.async_abort(reason="no_homes_found")

            if auth_handler:
                await auth_handler.close_session()

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_select_home(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the home selection step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected_home_id = user_input["home_id"]
            selected_home_name = self._homes_data[selected_home_id]["name"]

            self._config_data["home_id"] = selected_home_id
            self._config_title = selected_home_name or self._config_data[CONF_USERNAME]

            return await self.async_step_init_options()

        homes_selection = {
            home_id: f"{data['name']} ({data['module_count']} modules)" for home_id, data in self._homes_data.items()
        }

        select_home_schema = vol.Schema({vol.Required("home_id"): vol.In(homes_selection)})

        return self.async_show_form(step_id="select_home", data_schema=select_home_schema, errors=errors)

    async def async_step_init_options(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial options step (e.g., light_as_lock)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            options_data = {
                "light_as_lock": user_input.get("light_as_lock", False),
            }
            return self.async_create_entry(title=self._config_title, data=self._config_data, options=options_data)

        return self.async_show_form(step_id="init_options", data_schema=STEP_INIT_OPTIONS_SCHEMA, errors=errors)
