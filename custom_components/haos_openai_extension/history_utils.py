"""Pure helpers for summarising Recorder state changes.

Keep event ordering and 'most recent' selection deterministic rather than
expecting an LLM to infer them from a chronological list.
"""


def summarize_transitions(
    transitions: list[dict], state_filter: str | None, max_events: int
) -> dict:
    """Summarise events, sorting by actual UTC timestamps newest first.

    Each transition has 'state', 'time' (local ISO timestamp) and 'sort_time'
    (UTC epoch seconds). The internal numeric sort_time is not sent to the LLM.
    """
    ordered = sorted(
        transitions,
        key=lambda event: event["sort_time"],
        reverse=True,
    )
    latest_by_state: dict[str, dict] = {}
    for event in ordered:
        state = event["state"]
        if state not in latest_by_state:
            latest_by_state[state] = {
                "state": state,
                "time": event["time"],
            }

    matching = [
        event for event in ordered
        if state_filter is None or event["state"].casefold() == state_filter
    ]

    def public_event(event: dict) -> dict:
        return {"state": event["state"], "time": event["time"]}

    return {
        "latest_matching_transition": public_event(matching[0]) if matching else None,
        "latest_by_raw_state": latest_by_state,
        "matching_transitions": len(matching),
        "events_newest_first": [public_event(event) for event in matching[:max_events]],
        "truncated": len(matching) > max_events,
    }
