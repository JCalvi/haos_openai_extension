"""Read-only Recorder history tools for the existing Assist LLM API."""

from datetime import timedelta
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
from .history_utils import summarize_transitions

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
        "Read historical state transitions from Recorder for an Assist-exposed "
        "Home Assistant entity. Call for 'when was X last opened/closed/on/off', "
        "'how many times', or 'what happened yesterday'. Identify the entity "
        "by friendly name (name) or exact entity_id. Use hours up to 240. "
        "The optional state filter must be a raw state such as on/off, or "
        "open/closed only for binary sensors with an appropriate door class. "
        "The tool explicitly returns the latest matching transition, "
        "and the latest occurrence of each raw state. Use those values "
        "instead of selecting a timestamp from a list. "
        "For binary sensors of class power, on/off describes power, NOT "
        "whether a door is open or closed."
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

        end = dt_util.utcnow()
        start = end - timedelta(hours=hours)
        try:
            # Use Recorder's executor (not the HA event loop) for database I/O.
            # Do not include the start-time snapshot: it may predate the window.
            records = await get_instance(hass).async_add_executor_job(
                history.get_significant_states,
                hass,
                start,
                end,
                [entity.entity_id],
                None,   # filters
                False,  # include_start_time_state
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

        # Recorder returns the history in chronological order, but never
        # depend on an LLM deciding which entry is most recent. Sort by
        # absolute timestamps and include an explicit latest matching event.
        transitions = []
        for item in records.get(entity.entity_id, []):
            if item.state in {"unknown", "unavailable"}:
                continue
            changed_at = item.last_changed or item.last_updated
            transitions.append(
                {
                    "state": item.state,
                    "time": dt_util.as_local(changed_at).isoformat(),
                    "sort_time": changed_at.timestamp(),
                }
            )

        summary = summarize_transitions(transitions, state_filter, MAX_EVENTS)
        last_by_state = summary["latest_by_raw_state"]

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
            "state_filter": state_filter,
            **summary,
            "semantic_warning": semantic_warning,
            "note": (
                "Use latest_matching_transition.time for questions about the "
                "most recent occurrence of the requested state. Entries are "
                "ordered newest first, not oldest first. When no filter was "
                "specified, use latest_by_raw_state for the relevant state. "
                "Recorder history is limited to the requested period. The "
                "entity shown in source_entity_id is the source of these "
                "timestamps; it may differ from the device/cover history. "
                "Unknown or unavailable readings are omitted."
            ),
        }
        if entity.state.casefold() not in {"unknown", "unavailable"}:
            result["current_state"] = entity.state
            result["current_state_since"] = dt_util.as_local(
                entity.last_changed
            ).isoformat()

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
            "When asked about the last time a device changed state or its "
            "past activity, call haos_openai_extension__get_history, using "
            "the entity friendly name if its entity_id is unknown. "
            "For the MOST RECENT opening/closing/on/off request, use the "
            "latest_matching_transition or last_opened_at/last_closed_at "
            "from the history tool; NEVER pick an older entry from its "
            "events_newest_first list. Check source_entity_id matches the "
            "intended sensor. Never invent history or assume that a "
            "power-class binary sensor indicates an open or closed door. "
            "Times are already local."
        ),
    )
