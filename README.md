# HAOS OpenAI Extension

**Experimental v0.1.2** — extra read-only LLM tools for the **existing** Home Assistant Assist API. Built to work with the official OpenAI conversation integration, including Alexa requests forwarded to that conversation agent.

The first feature, `haos_openai_extension__get_history`, reads state transitions from Home Assistant's Recorder history.

## Install with HACS

1. In HACS, add `https://github.com/JCalvi/haos_openai_extension` as a **custom repository** of type **Integration**.
2. Download the latest release in HACS.
3. Add the following to `configuration.yaml`:

   ```yaml
   haos_openai_extension:
   ```

4. Restart Home Assistant.
5. Leave **Assist / Device control** enabled in your existing OpenAI conversation agent. No extra checkbox, API key, separate conversation agent or Alexa Lambda changes are needed.

**After upgrading in HACS:** restart Home Assistant to load the changed Python module.

## Test

In Home Assistant Assist, using your existing OpenAI agent, ask:

> Use the haos_openai_extension history tool to show state changes for Garage Door Input in the last 240 hours.

In the OpenAI conversation trace (**Show details**), look for a call to `haos_openai_extension__get_history`. A call only to `homeassistant__GetLiveContext` does not query history.

If there is no history tool call:

- Confirm `haos_openai_extension:` is in `configuration.yaml`.
- Check **Settings → System → Logs** for `HAOS OpenAI Extension loaded`.
- Confirm HACS downloaded the current release, restart, and start a *new conversation* in Assist.
- Ensure the target entity is exposed under **Settings → Voice assistants → Expose → Assist**.

## Capabilities and safety

- Query by **friendly name** (e.g. `Garage Door Input`) or **entity ID**.
- Retrieve recorded changes within up to **240 hours (10 days)**, with at most 80 events in the returned result.
- Optional filtering by raw state such as `on` or `off`.
- For `binary_sensor` with device class `door`, `garage_door`, `opening` or `window`, `open` and `closed` can be translated to `on`/`off`.
- For `device_class: power`, **off means no power detected**, not necessarily a closed door. Check sensor wiring and select the correct device class before asking about open/closed events.
- Only entities exposed to the active Assist assistant may be queried.
- Recorder queries are read-only, restricted to a single matched entity and capped for response size.
- Events are returned with Home Assistant's **local timezone**.
- A missing transition is not evidence that it never happened: Recorder may have purged it or excluded the entity.

## Garage doors and other safety-sensitive covers

**Do not expose the actual garage door `cover` entity to Assist or native Alexa** if voice opening/closing must be prevented. Home Assistant's normal exposure toggle is not a read-only service-permission switch.

For status and history, use a **binary sensor** with a correctly configured device class, for example:

- Expose `binary_sensor.garage_door_input` to **Assist**.
- Set **Show as → Garage door** if `on` actually means open and `off` means closed.
- Keep the actual `cover` unexposed to Assist and to the separate Nabu Casa Alexa smart-home integration.
- Also avoid exposing scripts, scenes, or controls which trigger garage movement.

The history tool itself never controls a device, and it only returns data for exposed entities. Changing **Show as** improves the meaning of on/off but does not independently create Recorder history.

## Branding

The OpenAI-inspired / smart-home icon is bundled at
`custom_components/haos_openai_extension/brand/icon.png`.
Home Assistant 2026.3+ uses in-repository brand images automatically; some HACS builds may still display a placeholder in their own repository list.

## Current limitations

- Counts cover only recorded changes in the requested time window.
- Detailed history is typically retained for 10 days by default unless configured otherwise.
- Alexa's synchronous response deadline is short; slow LLM + Recorder queries may time out.
- This build has been checked against Home Assistant's current LLM and Recorder source API but **has not been runtime-tested on your HAOS installation**.

## Releases

A GitHub Actions workflow automatically creates a release tag (for example `v0.1.1`) whenever the `version` in `custom_components/haos_openai_extension/manifest.json` is increased on the `main` branch. No manual release creation is required.
