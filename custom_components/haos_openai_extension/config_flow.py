"""UI setup for HAOS OpenAI Extension."""

from typing import Any, override

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class HaosOpenAIExtensionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Enable HAOS OpenAI Extension through Settings → Devices & services."""

    VERSION = 1

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One-click confirmation: there are no credentials or settings."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            return self.async_create_entry(
                title="HAOS OpenAI Extension",
                data={},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema({}),
        )
