"""Config flow for Fluidra Pool integration."""

import logging
from typing import Any

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResult
import voluptuous as vol

from .const import DOMAIN
from .fluidra_api import FluidraPoolAPI

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fluidra Pool."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL]
            password = user_input[CONF_PASSWORD]

            # Check if already configured
            await self.async_set_unique_id(email)
            self._abort_if_unique_id_configured()

            # Test connection
            api = FluidraPoolAPI(email, password)

            try:
                await api.authenticate()
                await api.get_pools()

                await api.close()

                # Accepter même si aucune piscine n'est trouvée pour l'instant
                # L'intégration peut être étendue pour d'autres APIs
                return self.async_create_entry(
                    title=f"Fluidra Pool ({email})",
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                )

            except Exception as err:
                _LOGGER.error("Error testing Fluidra Pool connection: %s", err)
                if "auth" in str(err).lower() or "login" in str(err).lower():
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            finally:
                await api.close()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""
