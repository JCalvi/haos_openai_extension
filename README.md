# HAOS OpenAI Extension

**Experimental v0.1.6** — extra read-only LLM tools for the **existing** Home Assistant Assist API. Built to work with the official OpenAI conversation integration, including Alexa requests forwarded to that conversation agent.

The first feature, `haos_openai_extension__get_history`, reads state transitions from Home Assistant's Recorder history.

## Install with HACS

1. In HACS, add `https://github.com/JCalvi/haos_openai_extension` as a **custom repository** of type **Integration**.
2. Download the latest release in HACS.
3. Restart Home Assistant after HACS downloads the integration.
4. Open **Settings → Devices & services → Add integration** and search for **HAOS OpenAI Extension**.
5. Click **Submit** to enable it. No fields, API key, or YAML changes are needed.
6. Leave **Assist / Device control** enabled in your existing OpenAI conversation agent. No separate conversation agent or Alexa Lambda changes are needed.

**After upgrading in HACS:** restart Home Assistant to load the changed Python module.

## Test

In Home Assistant Assist, using your existing OpenAI agent, ask:

> Use the haos_openai_extension history tool to show state changes for Garage Door Input in the last 240 hours.

In the OpenAI conversation trace (**Show details**), look for a call to `haos_openai_extension__get_history`. A call only to `homeassistant__GetLiveContext` does not query history.

If there is no history tool call:

- Confirm **HAOS OpenAI Extension** appears as configured under **Settings → Devices & services** (rather than only in HACS).
- Check **Settings → System → Logs** for `HAOS OpenAI Extension enabled: Recorder history for Assist`.
- Confirm HACS downloaded the current release, restart, and start a *new conversation* in Assist.
- Ensure the target entity is exposed under **Settings → Voice assistants → Expose → Assist**.

## Existing YAML installations

Versions up to v0.1.2 required a `haos_openai_extension:` entry in `configuration.yaml`. Starting in v0.1.3 the integration is configured entirely through the UI. New installations do not require YAML. If you previously configured it in YAML, remove that old line when migrating to the UI-based setup.

## Faster voice history and reliable durations (v0.1.6)

The original `haos_openai_extension__get_history` tool remains available.
Its original parameters (`name`, `entity_id`, `hours`, `state`) still work.
The tool now calculates durations and calendar boundaries inside Home Assistant
instead of asking the LLM to do clock arithmetic.

| Natural question | What the tool returns |
| --- | --- |
| When was the garage door last closed? | Latest actual on → off transition |
| How long has the garage door been closed? | Difference between the last actual closing and the **current HA time** |
| How long was it open last time? | Latest completed on-state interval |
| How many times did it open yesterday? | Number of actual transitions to on during local yesterday |
| How long was it open yesterday? | Total observed on-state duration within yesterday |
| List each opening from last week | Full transition history when available within the 10-day limit |

**Compact by default:** `detail=summary` returns the event count, most recent
events by state and calculated durations, but omits the full list of timestamps.
The model can request `detail=recent` (five newest events) or
`detail=full` (up to **80 events**, unchanged from previous versions).
This reduces token usage and Alexa response payloads **without discarding
stored history** or weakening event counts.

New optional parameters:
- `period`: `today`, `yesterday`, `this_week`, `last_week`,
  `last_night`, `last_hour`, `last_24_hours`, `last_7_days`,
  `last_10_days`. Weeks start Monday; *last night* means 6 pm yesterday
  to 6 am today in Home Assistant's timezone.
- `start_time` and `end_time`: custom ISO 8601 date/time boundaries.
  Datetimes without timezone offsets are interpreted in Home Assistant's
  configured timezone.
- `detail`: `summary` (default), `recent`, `full`.
- `max_events`: maximum returned timestamp entries (1–80) for
  `recent` / `full`. The count always covers all matching events.

The range is rejected (rather than silently shortened) if its start is more
than 240 hours before the request, or if it requires future history. Rolling
hour windows are computed in UTC for DST accuracy; calendar boundaries use
the local timezone.

**History quality:** Recorder may include repeated `off` records during a
restart. Those are not counted as new closings. `unknown` and `unavailable`
are not treated as opening/closing events. If the device was unavailable during
a measured duration, the tool flags the uncertainty. Durations are based on
recorded observations, not proof that the device stayed unchanged while offline.

**Security:** The history tool remains read-only and only queries entities
exposed to Assist. Continue exposing only `binary_sensor.garage_door_input`,
never the actual garage cover or a script that can actuate it.

## Capabilities and safety

- Query by **friendly name** (e.g. `Garage Door Input`) or **entity ID**.
- Retrieve recorded changes within up to **240 hours (10 days)**, with at most 80 events in the returned result.
- Optional filtering by raw state such as `on` or `off`.
- **Only real state transitions** are reported. Recorder may write repeat `off` rows on restart, restore or attribute changes; repeated `off → off` is never treated as a new closing. Similarly, `on → on` is not a new opening.
- The first valid historical observation establishes a baseline; it is not counted as a change unless a prior known state is available.
- For state comparison, `unknown` and `unavailable` readings are skipped without forgetting the previous known valid state. Observed changes while a device is offline cannot be recovered.
- Real transitions are sorted **newest-first**, with explicit `latest_matching_transition`, `latest_by_raw_state`, and (for opening-class binary sensors) `last_opened_at` and `last_closed_at` fields.
- `current_state_since` is deliberately not returned; its timestamp can reflect HA restoration rather than physical device activity.
- Invalid states are excluded from all returned history and summaries.
- The history response identifies its `source_entity_id` so the answer can be checked against the correct entity history (for example, a garage-door binary sensor rather than the cover).
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

- Counts cover only observed state transitions in the requested time window. If Recorder does not contain a previous valid state, the first reading cannot be counted as an event. Completed intervals spanning missing history can be unknown or uncertain.
- Detailed history is typically retained for 10 days by default unless configured otherwise.
- Alexa's synchronous response deadline is short; slow LLM + Recorder queries may time out.
- This build has been checked against Home Assistant's current LLM and Recorder source API but **has not been runtime-tested on your HAOS installation**.

## Releases

A GitHub Actions workflow automatically creates a release tag (for example `v0.1.1`) whenever the `version` in `custom_components/haos_openai_extension/manifest.json` is increased on the `main` branch. No manual release creation is required.
