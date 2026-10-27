"""Deterministic duration and state-period analytics for Recorder history.

The calculations use timestamps from the Home Assistant Recorder rather than
asking a language model to perform arithmetic. Only genuine changes between
known states create completed intervals. Unknown/unavailable gaps are flagged.
"""

INVALID_STATES = frozenset({"unknown", "unavailable"})


def format_duration(seconds: float) -> str:
    """Return a concise, voice-friendly elapsed time."""
    if seconds < 0:
        return "unknown"
    seconds_int = int(round(seconds))
    days, remainder = divmod(seconds_int, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    parts = []
    for amount, unit in (
        (days, "day"),
        (hours, "hour"),
        (minutes, "minute"),
    ):
        if amount:
            parts.append(f"{amount} {unit}{'' if amount == 1 else 's'}")
    if not parts:
        return f"{secs} second{'' if secs == 1 else 's'}"
    return " ".join(parts)


def calculate_history_metrics(
    observations: list[dict],
    *,
    start_timestamp: float,
    end_timestamp: float,
    current_state: str | None,
    is_live_window: bool,
) -> dict:
    """Compute counts/durations from genuine changes, with gap disclosure.

    Observations contain state, local ISO time, and absolute sort_time in
    seconds. All reported times lie within the requested query window, except
    the beginning of a completed interval when useful for context.
    """
    ordered = sorted(observations, key=lambda item: item["sort_time"])
    previous: str | None = None
    segment_start: float | None = None
    last_transition: dict | None = None
    last_transition_gap = False
    had_invalid_gaps = False
    saw_valid_baseline = False
    total_by_state: dict[str, float] = {}
    last_completed: dict[str, dict] = {}

    def add_segment(state: str, first: float | None, last: float) -> None:
        if first is None or last <= first:
            return
        covered_start = max(first, start_timestamp)
        covered_end = min(last, end_timestamp)
        if covered_end > covered_start:
            total_by_state[state] = (
                total_by_state.get(state, 0.0)
                + covered_end - covered_start
            )

    for observation in ordered:
        ts = observation["sort_time"]
        if ts > end_timestamp:
            break
        state = observation["state"].casefold()
        if state in INVALID_STATES:
            had_invalid_gaps = True
            if previous is not None:
                add_segment(previous, segment_start, ts)
            segment_start = None
            last_transition_gap = True
            continue

        if previous is None:
            previous = state
            saw_valid_baseline = ts <= start_timestamp
            segment_start = max(ts, start_timestamp)
            continue

        if state != previous:
            add_segment(previous, segment_start, ts)
            if last_transition is not None:
                elapsed = ts - last_transition["sort_time"]
                if elapsed >= 0:
                    last_completed[previous] = {
                        "started_at": last_transition["time"],
                        "ended_at": observation["time"],
                        "duration_seconds": round(elapsed),
                        "duration_human": format_duration(elapsed),
                        "contains_unavailable_gap": last_transition_gap,
                    }
            last_transition = observation
            previous = state
            segment_start = max(ts, start_timestamp)
            last_transition_gap = False
        elif segment_start is None:
            # Same valid state after an unavailable interval.
            segment_start = max(ts, start_timestamp)

    if previous is not None:
        add_segment(previous, segment_start, end_timestamp)

    totals = {
        state: {
            "seconds": round(duration),
            "human": format_duration(duration),
        }
        for state, duration in total_by_state.items()
    }

    result = {
        "total_observed_duration_by_raw_state": totals,
        "latest_completed_interval_by_raw_state": last_completed,
        "has_unavailable_gaps": had_invalid_gaps,
        "period_start_state_known": saw_valid_baseline,
        "duration_coverage_complete": (
            saw_valid_baseline and not had_invalid_gaps
        ),
    }

    # 'How long has it been closed?' is measured from the last genuine
    # off transition until NOW, never from last_updated after a restart.
    if is_live_window and current_state is not None:
        normalized_current = current_state.casefold()
        if normalized_current not in INVALID_STATES:
            result["current_state"] = current_state
            if (
                last_transition is not None
                and previous == normalized_current
                and end_timestamp >= last_transition["sort_time"]
            ):
                elapsed = end_timestamp - last_transition["sort_time"]
                result["current_state_duration_seconds"] = round(elapsed)
                result["current_state_duration_human"] = format_duration(elapsed)
                result["current_state_duration_since"] = last_transition["time"]
                result["current_state_duration_may_include_gap"] = (
                    last_transition_gap or had_invalid_gaps
                )
            else:
                result["current_state_duration_seconds"] = None
                result["current_state_duration_human"] = None

    return result
