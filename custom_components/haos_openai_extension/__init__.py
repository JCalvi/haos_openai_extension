"""Read-only tools for Home Assistant's existing Assist LLM API."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Settings → Devices & services is the only configuration required.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Initialize the integration; LLM tools are discovered from llm.py."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Enable LLM tools after the user adds the integration in the UI."""
    _LOGGER.info("HAOS OpenAI Extension enabled: Recorder history for Assist")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Allow removing the integration through the UI."""
    _LOGGER.info("HAOS OpenAI Extension disabled; restart to clear cached tools")
    return True
