# HAOS OpenAI Extension

Experimental custom integration that adds **read-only Recorder history** as an LLM tool to Home Assistant's existing **Assist** API. Designed to work with the built-in OpenAI conversation agent and Alexa bridge without changing either.

**Status: v0.1.0 experimental, not yet tested on the target HAOS installation.**

## Installation

1. Copy `custom_components/haos_openai_extension` into `/config/custom_components/`.
2. Add to `configuration.yaml`:
   ```yaml
   haos_openai_extension:
   ```
3. Restart Home Assistant. Keep **Assist** enabled in the OpenAI conversation agent.
4. Test in Home Assistant before using Alexa: "When did binary_sensor.front_door last change?" Replace with an actual exposed entity.

## Scope and limits

- Adds a tool to the **existing** Assist API; does not register a new checkbox or replace the OpenAI agent.
- Only entities exposed to the active Assist assistant may be queried.
- Recorder read-only; no SQL, no device-control operations.
- Maximum lookback: 240 hours (10 days); at most 80 returned transitions.
- Initial Recorder state is excluded, since it may have started before the window.
- Event times use Home Assistant's local timezone.
- For exact on/off events, use the entity's actual raw states. A binary sensor's friendly meaning (open/closed) depends on its device class.
- If the requested event is outside retained Recorder history, it cannot be recovered.
- Alexa's response deadline may be exceeded by slow Recorder/LLM queries.

## Development

Source files are in `custom_components/haos_openai_extension/`. Future tools can be added in this integration without changing its domain.

Please test startup logs and a simple history question before relying on results.
