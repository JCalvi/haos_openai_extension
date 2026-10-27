"""Read-only Recorder history tool contributed to the existing Assist LLM API."""
from datetime import timedelta

import probatio

from homeassistant.components import llm
from homeassistant.components.recorder import history
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent
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

MAX_HOURS = 240
MAX_EVENTS = 80


class HistoryTool(Tool):
    """Read recent state transitions for an Assist-exposed entity."""

    name = "haos_openai_extension__get_history"
    title = "Get entity state history"
    integration = DOMAIN
    description = (
        "Retrieve recorded state changes for an entity exposed to Assist. "
        "Use when asked when a door last opened or closed, a device turned on "
        "or off, or how many times a state occurred. "
        "The optional state is an exact raw state, such as on or off. "
        "Search at most 240 hours. Use the exact entity_id from Assist."
    )
    annotations = ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    parameters = probatio.Schema({
        "entity_id": str,
        probatio.Optional("hours", default=24): int,
        probatio.Optional("state"): str,
    })

    async def async_call(
        self, hass: HomeAssistant, tool_input: ToolInput, llm_context: LLMContext
    ) -> ToolResult:
        """Query Recorder without granting the model SQL or control access."""
        args = self.parameters(tool_input.tool_args)
        entity_id = args["entity_id"]
        hours = args["hours"]
        state_filter = args.get("state")
        if isinstance(hours, bool) or not 1 <= hours <= MAX_HOURS:
            raise HomeAssistantError("hours must be between 1 and 240")
        if not llm_context.assistant or not intent.async_entity_exposed(
            hass, llm_context.assistant, entity_id
        ):
            raise HomeAssistantError("Entity is not exposed to this Assist assistant")

        end = dt_util.utcnow()
        start = end - timedelta(hours=hours)
        records = await hass.async_add_executor_job(
            lambda: history.get_significant_states(
                hass, start, end, entity_ids=[entity_id],
                significant_changes_only=False, minimal_response=False,
                no_attributes=True,
            )
        )
        states = records.get(entity_id, [])
        events = []
        previous = None
        for index, item in enumerate(states):
            state = item.state
            if state in ("unknown", "unavailable"):
                previous = state
                continue
            # Recorder may prepend the state already active at the start.
            # Never claim that first record is a transition.
            if index == 0:
                previous = state
                continue
            if state == previous:
                continue
            previous = state
            if state_filter is not None and state != state_filter:
                continue
            timestamp = item.last_changed or item.last_updated
            events.append({
                "state": state,
                "time": dt_util.as_local(timestamp).isoformat(),
            })

        return ToolResult(data={
            "entity_id": entity_id,
            "period_hours": hours,
            "state_filter": state_filter,
            "matching_transitions": len(events),
            "events": events[-MAX_EVENTS:],
            "truncated": len(events) > MAX_EVENTS,
            "note": (
                "The initial state is excluded because it may predate the search. "
                "Only recorded changes within the selected period are included. "
                "Missing records do not prove that an event never happened."
            ),
        })


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> llm.LLMTools | None:
    """Contribute the tool to the existing Assist device-control API."""
    if api_id != LLM_API_ASSIST:
        return None
    return llm.LLMTools(
        tools=[HistoryTool()],
        prompt=(
            "For questions about previous states or event times, use "
            "haos_openai_extension__get_history. Never invent timestamps. "
            "State when Recorder does not contain enough history."
        ),
    )
