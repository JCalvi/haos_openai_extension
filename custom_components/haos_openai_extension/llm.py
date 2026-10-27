"""Read-only Recorder history tools for the existing Assist LLM API."""

import logging

import probatio

from homeassistant.components import llm
from homeassistant.components.homeassistant import async_should_expose
from homeassistant.components.recorder import get_instance, history
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.llm import (
    LLM_API_ASSIST,
    LLMContext,
    Tool,
    ToolAnnotations,
    ToolInput,
    ToolResult,
)
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .history_utils import shape_history_detail, summarize_transitions
from .metrics import calculate_history_metrics
from .window_utils import resolve_history_window

_LOGGER = logging.getLogger(__name__)
MAX_HOURS = 240
MAX_EVENTS = 80
OPENING_CLASSES = {"door", "garage_door", "opening", "window"}


def _resolve_entity(
    hass: HomeAssistant, assistant: str, entity_id: str | None, name: str | None
) -> tuple[State | None, str | None]:
    """Resolve an entity strictly from currently exposed states."""
    if entity_id:
        entity = hass.states.get(entity_id)
        if entity is None or not async_should_expose(hass, assistant, entity_id):
            return None, "Entity not found or not exposed to Assist"
        return entity, None

    if not name or not name.strip():
        return None, "Provide an entity_id or a friendly name"

    query = name.casefold().strip()
    candidates = hass.states.async_all()
    matches = [
        entity for entity in candidates
        if (entity.name.casefold() == query or entity.entity_id.casefold() == query)
        and async_should_expose(hass, assistant, entity.entity_id)
    ]
    if not matches:
        matches = [
            entity for entity in candidates
            if query in entity.name.casefold()
            and async_should_expose(hass, assistant, entity.entity_id)
        ]
    if not matches:
        return None, "No matching entity exposed to Assist"
    if len(matches) > 1:
        choices = ", ".join(entity.entity_id for entity in matches[:8])
        return None, f"Ambiguous name; specify entity_id. Matches: {choices}"
    return matches[0], None


class HistoryTool(Tool):
    """Read significant state changes for an exposed entity."""

    name = "haos_openai_extension__get_history"
    title = "Get Home Assistant state history"
    integration = DOMAIN
    description = (
        "Read-only Home Assistant Recorder state-transition and duration "
        "analytics for ONE entity exposed to Assist. Use for questions like "
        "'when was X last opened', 'how long has it been closed', 'how long "
        "was it open', 'how many times did X turn on yesterday', 'was it open "
        "last night', or 'list opening times last week'. Identify via name "
        "or entity_id. For today/yesterday/this_week/last_week/last_night "
        "(local 6pm-6am), last_hour/last_24_hours/last_7_days/last_10_days "
        "pass period. For custom dates pass start_time and end_time in ISO "
        "8601 (local timezone accepted). Otherwise hours 1..240 defaults "
        "to 240. Always pass state=on/off for relevant counts; open/closed "
        "map to on/off only for a proper door-class binary_sensor. "
        "Use detail=summary by default (small and fast). Use detail=recent "
        "for five newest events or detail=full for historical lists and "
        "cross-entity correlation (up to 80 events). "
        "The result supplies current_state_duration_human, "
        "latest_completed_interval_by_raw_state, state-duration totals, "
        "exact transition counts and the current query clock; do not guess "
        "elapsed times. Duplicate same-state observations and unknown/"
        "unavailable are excluded as transitions. A power-class binary "
        "sensor cannot establish physical door opening."
    )
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    parameters = probatio.Schema(
        {
            probatio.Optional("name"): str,
            probatio.Optional("entity_id"): str,
            probatio.Optional("hours", default=240): int,
            probatio.Optional("state"): str,
            probatio.Optional("period"): str,
            probatio.Optional("start_time"): str,
            probatio.Optional("end_time"): str,
            probatio.Optional("detail", default="summary"): str,
            probatio.Optional("max_events", default=80): int,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Return a bounded set of read-only history events."""
        args = self.parameters(tool_input.tool_args)
        hours = args["hours"]
        if isinstance(hours, bool) or not 1 <= hours <= MAX_HOURS:
            return ToolResult(data={"error": "hours must be 1 to 240"}, error=True)

        entity, error = _resolve_entity(
            hass,
            llm_context.assistant,
            args.get("entity_id"),
            args.get("name"),
        )
        if error or entity is None:
            return ToolResult(data={"error": error or "Entity not found"}, error=True)

        requested_state = args.get("state")
        device_class = entity.attributes.get("device_class")
        state_filter = requested_state.casefold().strip() if requested_state else None
        if state_filter in {"unknown", "unavailable"}:
            return ToolResult(
                data={"error": "Only meaningful state changes can be queried"},
                error=True,
            )
        if entity.domain == "binary_sensor" and state_filter in {
            "open", "opened", "close", "closed"
        }:
            if device_class not in OPENING_CLASSES:
                return ToolResult(
                    data={
                        "error": (
                            f"{entity.entity_id} has device_class={device_class!r}, "
                            "so open/closed cannot be inferred. Query raw on/off "
                            "transitions and confirm their physical meaning first."
                        )
                    },
                    error=True,
                )
            state_filter = (
                "on" if state_filter in {"open", "opened"} else "off"
            )

        detail = args["detail"].strip().lower()
        if detail not in {"summary", "recent", "full"}:
            return ToolResult(
                data={"error": "detail must be summary, recent or full"},
                error=True,
            )
        max_events = args["max_events"]
        if isinstance(max_events, bool) or not 1 <= max_events <= MAX_EVENTS:
            return ToolResult(
                data={"error": "max_events must be between 1 and 80"},
                error=True,
            )

        local_now = dt_util.now()
        try:
            start_local, end_local, range_name = resolve_history_window(
                local_now,
                hours=hours,
                period=args.get("period"),
                start_time=args.get("start_time"),
                end_time=args.get("end_time"),
                max_hours=MAX_HOURS,
            )
        except ValueError as err:
            return ToolResult(data={"error": str(err)}, error=True)
        start = dt_util.as_utc(start_local)
        end = dt_util.as_utc(end_local)
        is_live_window = abs((end - dt_util.as_utc(local_now)).total_seconds()) < 1
        try:
            # Use Recorder's executor (not the HA event loop) for database I/O.
            # Include the state just before the requested interval when
            # available, to establish a baseline without counting it as
            # an event.
            records = await get_instance(hass).async_add_executor_job(
                history.get_significant_states,
                hass,
                start,
                end,
                [entity.entity_id],
                None,   # filters
                True,   # include_start_time_state (baseline for comparisons)
                True,   # significant_changes_only
                False,  # minimal_response
                True,   # no_attributes
            )
        except Exception:
            _LOGGER.exception("Recorder history request failed")
            return ToolResult(
                data={"error": "Recorder history query failed; check HA logs"},
                error=True,
            )

        # Recorder rows are state observations, not necessarily state
        # transitions. Duplicate off/off readings (including after a
        # restart) MUST NOT count as additional garage-door closings.
        observations = []
        for item in records.get(entity.entity_id, []):
            changed_at = item.last_changed or item.last_updated
            observations.append(
                {
                    "state": item.state,
                    "time": dt_util.as_local(changed_at).isoformat(),
                    "sort_time": item.last_updated.timestamp(),
                }
            )

        summary = summarize_transitions(
            observations,
            state_filter,
            MAX_EVENTS,
            start_timestamp=start.timestamp(),
        )
        last_by_state = summary["latest_by_raw_state"]
        metrics = calculate_history_metrics(
            observations,
            start_timestamp=start.timestamp(),
            end_timestamp=end.timestamp(),
            current_state=entity.state,
            is_live_window=is_live_window,
        )

        # The full Recorder history is still counted; only its response
        # representation is shortened for the default voice summary.
        summary = shape_history_detail(summary, detail, max_events)

        # Continuous-value entities can have thousands of different states;
        # bound per-state breakdowns in compact responses only.
        if detail != "full":
            summary["latest_by_raw_state"] = dict(
                list(summary["latest_by_raw_state"].items())[:12]
            )
            for key in (
                "total_observed_duration_by_raw_state",
                "latest_completed_interval_by_raw_state",
            ):
                values = metrics[key]
                metrics[key] = dict(list(values.items())[-12:])

        semantic_warning = None
        if entity.domain == "binary_sensor" and device_class not in OPENING_CLASSES:
            semantic_warning = (
                f"The entity has device_class={device_class!r}. Do not equate "
                "on/off with open/closed without confirming the wiring."
            )

        result = {
            "source_entity_id": entity.entity_id,
            "source_name": entity.name,
            "device_class": device_class,
            "period_start": dt_util.as_local(start).isoformat(),
            "period_end": dt_util.as_local(end).isoformat(),
            "period_name": range_name,
            "query_now": local_now.isoformat(),
            "state_filter": state_filter,
            "detail": detail,
            **summary,
            **metrics,
            "semantic_warning": semantic_warning,
            "note": (
                "Use latest_matching_transition.time for questions about the "
                "most recent genuine observed transition to the requested "
                "state. Events are ordered newest first. Duplicate same-state "
                "observations (such as off after a restart) are NOT transitions. "
                "Use matching_transitions for counts, current_state_duration_"
                "human for 'how long has it been in this state', "
                "latest_completed_interval_by_raw_state for most recent "
                "completed on/off durations, and total_observed_duration_by_"
                "raw_state for totals within the selected period. "
                "No guessed timestamps. Invalid readings are omitted. "
                "Check duration_coverage_complete before claiming exact "
                "total durations. source_entity_id identifies the sensor."
            ),
        }
        if entity.domain == "binary_sensor" and device_class in OPENING_CLASSES:
            result["last_opened_at"] = (
                last_by_state.get("on", {}).get("time")
            )
            result["last_closed_at"] = (
                last_by_state.get("off", {}).get("time")
            )

        return ToolResult(data=result)


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> llm.LLMTools | None:
    """Contribute history lookups directly to the existing Assist API."""
    if api_id != LLM_API_ASSIST:
        return None
    return llm.LLMTools(
        tools=[HistoryTool()],
        prompt=(
            "For past events, timings, durations or counts, call "
            "haos_openai_extension__get_history. Identify a sensor via "
            "friendly name if entity ID is unknown. For today, yesterday, "
            "this week, last week or last night, pass the corresponding "
            "period; for a precise time interval, pass start_time/end_time "
            "as ISO 8601 local datetimes. For counts, request the relevant "
            "state and use matching_transitions exactly. For last events, "
            "use latest_matching_transition, last_opened_at or last_closed_at. "
            "For 'how long HAS it been closed/on', check the current_state "
            "and use current_state_duration_human, which is calculated up "
            "to the query_now timestamp. For 'how long WAS it open', use "
            "latest_completed_interval_by_raw_state.on.duration_human. "
            "For total time spent in a state during a period, use "
            "total_observed_duration_by_raw_state and disclose unknown gaps. "
            "Never calculate durations or current time yourself. "
            "If current_state_duration_may_include_gap is true, warn that "
            "continuous activity during offline periods is unverified. "
            "Ask for detail=summary for concise answers, detail=recent for "
            "last five events, detail=full for detailed lists/correlations. "
            "Never invent identities or match fingerprint events to door "
            "openings without corroborating access logs. A key ID is not "
            "proof of the person physically opening the door. "
            "Do not confuse a power-class sensor with an actual door contact."
        ),
    )
