"""Read-only Recorder history tools for the existing Assist LLM API."""

import json
import logging
from datetime import timedelta
from time import perf_counter

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

from .comparison_utils import (
    MAX_COMPARE_ENTITIES,
    MAX_COMPARE_RESULTS,
    rank_entities,
    select_entities,
)
from .const import DOMAIN
from .history_utils import shape_history_detail, summarize_transitions
from .metrics import calculate_history_metrics
from .query_utils import async_find_latest
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


def _log_tool_performance(tool: str, started_at: float, payload: dict) -> None:
    """Report cost only at DEBUG level; never log query data or entity IDs."""
    if _LOGGER.isEnabledFor(logging.DEBUG):
        _LOGGER.debug(
            "%s: total_ms=%.1f, result_bytes=%d",
            tool,
            (perf_counter() - started_at) * 1000,
            len(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        )


async def _async_read_observations(
    hass: HomeAssistant, start, end, entity_ids: list[str]
) -> dict[str, list[dict]]:
    """One Recorder executor operation for any number of authorised entities."""
    begun = perf_counter()
    records = await get_instance(hass).async_add_executor_job(
        history.get_significant_states,
        hass,
        start,
        end,
        entity_ids,
        None,   # filters
        True,   # include_start_time_state (transition baseline)
        True,   # significant_changes_only
        False,  # minimal_response
        True,   # no_attributes
    )
    _LOGGER.debug(
        "Recorder query: entities=%d, duration_ms=%.1f",
        len(entity_ids),
        (perf_counter() - begun) * 1000,
    )
    return {
        entity_id: [
            {
                "state": item.state,
                "time": dt_util.as_local(
                    item.last_changed or item.last_updated
                ).isoformat(),
                "sort_time": item.last_updated.timestamp(),
            }
            for item in records.get(entity_id, [])
        ]
        for entity_id in entity_ids
    }


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
        "sensor cannot establish physical door opening. "
        "For ONLY the last opening/closing/on/off event, pass "
        "query_mode=latest to search 12h then 48h then 240h only as "
        "needed. For elapsed/total durations, counts, specific days "
        "or full histories use query_mode=complete (the default). "
        "If query_mode=latest is combined with an explicit period, the "
        "tool automatically uses complete mode for the requested dates."
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
            probatio.Optional("query_mode", default="complete"): str,
        }
    )

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Return a bounded set of read-only history events."""
        started_at = perf_counter()
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

        query_mode = args["query_mode"].strip().lower()
        if query_mode not in {"complete", "latest"}:
            return ToolResult(
                data={"error": "query_mode must be complete or latest"},
                error=True,
            )
        if query_mode == "latest" and (
            args.get("period") or args.get("start_time") or args.get("end_time")
        ):
            # The caller has specified an exact period. Use a single query
            # over that period and preserve its boundaries, rather than
            # rejecting an otherwise valid request and wasting an LLM retry.
            query_mode = "complete"

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
        entity_ids = [entity.entity_id]
        try:
            if query_mode == "latest":
                async def _fetch_recent(lookback_hours: int) -> list[dict]:
                    recent_start = end - timedelta(hours=lookback_hours)
                    return (
                        await _async_read_observations(
                            hass, recent_start, end, entity_ids
                        )
                    )[entity.entity_id]

                observations, summary, searched_hours, query_count = (
                    await async_find_latest(
                        _fetch_recent,
                        state=state_filter,
                        max_hours=hours,
                        query_end_ts=end.timestamp(),
                    )
                )
                _LOGGER.debug(
                    "Last-event lookup: attempts=%d, window_hours=%d",
                    query_count, searched_hours,
                )
                output = {
                    "source_entity_id": entity.entity_id,
                    "source_name": entity.name,
                    "current_state": (
                        entity.state if entity.state not in {
                            "unknown", "unavailable"
                        } else None
                    ),
                    "latest_matching_transition": (
                        summary["latest_matching_transition"]
                    ),
                    "latest_by_raw_state": summary["latest_by_raw_state"],
                    "searched_hours": searched_hours,
                    "query_now": local_now.isoformat(),
                }
                if entity.domain == "binary_sensor" and (
                    device_class in OPENING_CLASSES
                ):
                    output["last_opened_at"] = (
                        summary["latest_by_raw_state"].get("on", {}).get("time")
                    )
                    output["last_closed_at"] = (
                        summary["latest_by_raw_state"].get("off", {}).get("time")
                    )
                _log_tool_performance("get_history/latest", started_at, output)
                return ToolResult(data=output)

            observations = (
                await _async_read_observations(hass, start, end, entity_ids)
            )[entity.entity_id]
        except Exception:
            _LOGGER.exception("Recorder history request failed")
            return ToolResult(
                data={"error": "Recorder history query failed; check HA logs"},
                error=True,
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

        if detail != "full":
            # Repeated long instructions cost tokens on every request.
            # The LLM API prompt already documents result semantics.
            result.pop("note", None)

        _log_tool_performance("get_history/complete", started_at, result)
        return ToolResult(data=result)


class CompareHistoryTool(Tool):
    """Compare recorded state durations or event counts across devices."""

    name = "haos_openai_extension__compare_history"
    title = "Compare Home Assistant entity history"
    integration = DOMAIN
    description = (
        "ONE Recorder query to compare ALL Assist-exposed devices in a domain "
        "(e.g. 'which light was on longest today', 'which switch was used "
        "most yesterday', 'which alarm spent longest disarmed'). Use domain "
        "light/switch/fan/binary_sensor/input_boolean/climate/"
        "alarm_control_panel. Supports metric=total_duration (cumulative "
        "observed time in raw state) or metric=transition_count. State defaults "
        "to on; climate state cool/heat describes selected HVAC MODE and "
        "does NOT prove compressor runtime. Period defaults to today; "
        "supply hours without period for a rolling window, or use period "
        "and ISO start_time/end_time as with get_history. "
        "Results contain up to ten entities ranked deterministically, "
        "and coverage flags. More than max_entities matching entities "
        "returns an error rather than a misleading partial ranking. "
        "Use name_contains to narrow scope. Do not infer identity or "
        "control security-sensitive devices."
    )
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    parameters = probatio.Schema({
        "domain": str,
        probatio.Optional("state", default="on"): str,
        probatio.Optional("metric", default="total_duration"): str,
        probatio.Optional("period"): str,
        probatio.Optional("hours", default=0): int,
        probatio.Optional("start_time"): str,
        probatio.Optional("end_time"): str,
        probatio.Optional("max_entities", default=30): int,
        probatio.Optional("max_results", default=5): int,
        probatio.Optional("name_contains"): str,
    })

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        started_at = perf_counter()
        args = self.parameters(tool_input.tool_args)
        max_entities = args["max_entities"]
        max_results = args["max_results"]
        hours = args["hours"]
        if isinstance(hours, bool) or not 0 <= hours <= MAX_HOURS:
            return ToolResult(
                data={"error": "hours must be 1..240, or omitted"}, error=True
            )
        domain = args["domain"].strip().casefold()
        raw_state = args["state"].strip().casefold()
        metric = args["metric"].strip().lower()
        try:
            selected = select_entities(
                hass.states.async_all(),
                domain=domain,
                exposed=lambda eid: async_should_expose(
                    hass, llm_context.assistant, eid
                ),
                limit=max_entities,
                name_contains=args.get("name_contains"),
            )
            if not selected:
                return ToolResult(
                    data={
                        "error": "No matching Assist-exposed entities; "
                        "check domain, exposure and name_contains."
                    },
                    error=True,
                )
            if isinstance(max_results, bool) or not (
                1 <= max_results <= MAX_COMPARE_RESULTS
            ):
                raise ValueError(
                    f"max_results must be 1..{MAX_COMPARE_RESULTS}"
                )
            period = args.get("period")
            # No time options means 'today'. Explicit hours with no period
            # means a rolling lookback instead, e.g. last 4 hours.
            if not (
                period or args.get("start_time") or args.get("end_time")
            ) and hours == 0:
                period = "today"
            local_now = dt_util.now()
            start_local, end_local, period_name = resolve_history_window(
                local_now,
                hours=hours or 24,
                period=period,
                start_time=args.get("start_time"),
                end_time=args.get("end_time"),
                max_hours=MAX_HOURS,
            )
            start = dt_util.as_utc(start_local)
            end = dt_util.as_utc(end_local)
            # Restrict first, then query Recorder once for all entities.
            entity_ids = [entity.entity_id for entity in selected]
            observations_by_entity = await _async_read_observations(
                hass, start, end, entity_ids
            )
            entries = []
            for entity in selected:
                observations = observations_by_entity[entity.entity_id]
                summary = summarize_transitions(
                    observations, raw_state, 0,
                    start_timestamp=start.timestamp(),
                )
                metrics = calculate_history_metrics(
                    observations,
                    start_timestamp=start.timestamp(),
                    end_timestamp=end.timestamp(),
                    current_state=None,
                    is_live_window=False,
                )
                entries.append({
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "current_state": (
                        entity.state if entity.state not in (
                            "unknown", "unavailable"
                        ) else None
                    ),
                    "summary": summary,
                    "metrics": metrics,
                })

            comparison = rank_entities(
                entries, metric=metric, state=raw_state, limit=max_results
            )
        except ValueError as err:
            return ToolResult(data={"error": str(err)}, error=True)
        except Exception:
            _LOGGER.exception("Multi-entity Recorder history request failed")
            return ToolResult(
                data={"error": "Recorder comparison failed; check HA logs"},
                error=True,
            )

        response = {
            "domain": domain,
            "raw_state": raw_state,
            "metric": metric,
            "period_name": period_name,
            "period_start": dt_util.as_local(start).isoformat(),
            "period_end": dt_util.as_local(end).isoformat(),
            **comparison,
        }
        _log_tool_performance("compare_history", started_at, response)
        return ToolResult(data=response)


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> llm.LLMTools | None:
    """Contribute history lookups directly to the existing Assist API."""
    if api_id != LLM_API_ASSIST:
        return None
    return llm.LLMTools(
        tools=[HistoryTool(), CompareHistoryTool()],
        prompt=(
            "For single-device past events, timings, durations or counts, "
            "call haos_openai_extension__get_history. For 'which device "
            "was on longest/most often' comparisons across lights, "
            "switches or sensors, call haos_openai_extension__compare_history "
            "ONCE, rather than get_history for each entity. "
            "Use query_mode=latest for simple 'when last on/off/opened/closed' "
            "questions with no explicit date range; it searches recent "
            "history first. Other queries use complete history.  Identify a sensor via "
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
