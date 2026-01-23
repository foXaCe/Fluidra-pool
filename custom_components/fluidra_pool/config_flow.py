"""Config flow for Fluidra Pool integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, CONF_SCAN_INTERVAL
from homeassistant.core import callback
import voluptuous as vol

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class FluidraPoolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fluidra Pool."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._reauth_entry: dict[str, Any] | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Get the options flow for this handler.

        🥇 Gold: Options flow pour configurer les paramètres avancés.
        """
        return FluidraPoolOptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            # Check if already configured
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            # Test connection
            error = await self._test_credentials(email, password)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"Fluidra Pool ({email})",
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle reauth flow triggered by ConfigEntryAuthFailed.

        🥈 Silver: Reauth flow obligatoire pour gérer les tokens expirés.
        """
        self._reauth_entry = entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reauth confirmation step.

        🥈 Silver: Permet à l'utilisateur de re-saisir ses credentials.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            # Test new credentials
            error = await self._test_credentials(email, password)
            if error:
                errors["base"] = error
            else:
                # Update the existing config entry
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                )

        # Pre-fill email from existing entry
        existing_email = ""
        if self._reauth_entry:
            existing_email = self._reauth_entry.get(CONF_EMAIL, "")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=existing_email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"email": existing_email},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguration flow.

        🥇 Gold: Reconfigure flow pour modifier les credentials sans supprimer l'entrée.
        """
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            # Test new credentials
            error = await self._test_credentials(email, password)
            if error:
                errors["base"] = error
            else:
                # Update unique_id if email changed
                if email.lower() != reconfigure_entry.unique_id:
                    await self.async_set_unique_id(email.lower())
                    self._abort_if_unique_id_configured()

                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                    reason="reconfigure_successful",
                )

        # Pre-fill with existing values
        existing_email = reconfigure_entry.data.get(CONF_EMAIL, "")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=existing_email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"email": existing_email},
        )

    def _get_reauth_entry(self) -> ConfigEntry:
        """Get the config entry being reauthenticated."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])

    def _get_reconfigure_entry(self) -> ConfigEntry:
        """Get the config entry being reconfigured."""
        return self.hass.config_entries.async_get_entry(self.context["entry_id"])

    async def _test_credentials(self, email: str, password: str) -> str | None:
        """Test credentials and return error key if failed.

        Returns:
            None if successful, error key string if failed.
        """
        api = FluidraPoolAPI(email, password)

        try:
            await api.authenticate()
            _LOGGER.info("Authentication successful for %s", email)
            return None
        except Exception as err:
            _LOGGER.error("Authentication failed for %s: %s", email, err)
            if "401" in str(err) or "unauthorized" in str(err).lower():
                return "invalid_auth"
            if "timeout" in str(err).lower() or "connect" in str(err).lower():
                return "cannot_connect"
            return "unknown"
        finally:
            await api.close()


class FluidraPoolOptionsFlowHandler(OptionsFlow):
    """Handle options flow for Fluidra Pool.

    🥇 Gold: Options flow pour configurer les paramètres avancés.
    """

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Get current values or defaults
        current_scan_interval = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=current_scan_interval,
                    ): vol.All(vol.Coerce(int), vol.Range(min=30, max=300)),
                }
            ),
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
