"""Extra read-only tools for the built-in Assist LLM API."""
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Make the LLM platform available to Home Assistant."""
    return True
