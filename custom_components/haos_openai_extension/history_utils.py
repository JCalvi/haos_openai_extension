"""Pure helpers to identify genuine Recorder state transitions.

Recorder may record the same state repeatedly after an integration reload,
restore, or Home Assistant restart. Such rows are observations, not physical
state changes, and must not be reported as historical opening/closing events.
"""

INVALID_STATES = frozenset({"unknown", "unavailable"})


def summarize_transitions(
    observations: list[dict],
    state_filter: str | None,
    max_events: int,
    start_timestamp: float | None = None,
) -> dict:
    """Return real state changes, newest first, with latest-event summaries.

    observations are timestamped state samples, not presumed transitions.
    Each sample includes state, time (local ISO timestamp), and sort_time
    (a Unix timestamp of the actual Recorder observation).

    An initial valid sample establishes the baseline; it is not counted as
    an event because there is no preceding known state in this record set.
    Invalid states are ignored without erasing the last *known* state.
    Matching events are limited to the requested start_timestamp when set.
    """
    ordered_observations = sorted(
        observations, key=lambda item: item["sort_time"]
    )

    previous_known_state: str | None = None
    changes: list[dict] = []

    for observation in ordered_observations:
        state = observation["state"]
        normalized_state = state.casefold()

        if normalized_state in INVALID_STATES:
            continue

        if previous_known_state is None:
            previous_known_state = normalized_state
            continue

        if normalized_state == previous_known_state:
            # e.g. off → off after HA restart: NOT a garage closing event
            continue

        # A genuine observed change from the last known valid state.
        previous_known_state = normalized_state
        if start_timestamp is not None and observation["sort_time"] < start_timestamp:
            continue
        changes.append(observation)

    # The model receives only state transitions, never repeated state samples.
    newest_first = list(reversed(changes))
    latest_by_state: dict[str, dict] = {}
    for change in newest_first:
        state = change["state"]
        if state not in latest_by_state:
            latest_by_state[state] = {"state": state, "time": change["time"]}

    matching = [
        change
        for change in newest_first
        if state_filter is None or change["state"].casefold() == state_filter
    ]

    def public_change(change: dict) -> dict:
        return {"state": change["state"], "time": change["time"]}

    return {
        "latest_matching_transition": (
            public_change(matching[0]) if matching else None
        ),
        "latest_by_raw_state": latest_by_state,
        "matching_transitions": len(matching),
        "events_newest_first": [
            public_change(change) for change in matching[:max_events]
        ],
        "truncated": len(matching) > max_events,
    }
