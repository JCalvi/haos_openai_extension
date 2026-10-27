"""Adaptive last-event history lookup without changing full-history behaviour."""

from collections.abc import Awaitable, Callable

from .history_utils import summarize_transitions

# Expand only when the previous shorter window lacks a matching real change.
RECENT_LOOKBACK_HOURS = (12, 48, 240)


async def async_find_latest(
    fetch_observations: Callable[[int], Awaitable[list[dict]]],
    *,
    state: str | None,
    max_hours: int,
    query_end_ts: float,
) -> tuple[list[dict], dict, int, int]:
    """Find latest genuine transition with at most three Recorder queries.

    The query function must supply an initial Recorder state snapshot so a
    repeated off state at restart cannot count as a transition.
    All returned summaries come from the same existing history algorithm.
    """
    attempts = 0
    for hours in RECENT_LOOKBACK_HOURS:
        hours = min(hours, max_hours)
        attempts += 1
        observations = await fetch_observations(hours)
        summary = summarize_transitions(
            observations, state, 80,
            start_timestamp=query_end_ts - hours * 3600,
        )
        if summary["latest_matching_transition"] is not None or hours == max_hours:
            return observations, summary, hours, attempts
    # Unreachable with max_hours >= 12; retained for explicit failure.
    raise ValueError("No suitable last-event history window")
