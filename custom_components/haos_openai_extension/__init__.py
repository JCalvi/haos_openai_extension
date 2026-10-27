"""Extra read-only tools for Home Assistant's built-in Assist LLM API."""

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Load this integration; the llm.py platform is discovered lazily."""
    _LOGGER.info(
        "HAOS OpenAI Extension loaded; Recorder history tool available via Assist"
    )
    return True
