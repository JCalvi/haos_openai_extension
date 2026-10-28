"""Pure helpers for safe, bounded cross-entity Recorder comparisons.

These helpers deliberately avoid Home Assistant imports so the data-reduction,
privacy checks and ranking can be tested with the standard library.
"""

from collections.abc import Callable, Iterable

MAX_COMPARE_ENTITIES = 40
MAX_COMPARE_RESULTS = 10
SUPPORTED_DOMAINS = frozenset({
    "light", "switch", "binary_sensor", "fan", "input_boolean",
    "climate", "alarm_control_panel",
})


def select_entities(
    states: Iterable,
    *,
    domain: str,
    exposed: Callable[[str], bool],
    limit: int = MAX_COMPARE_ENTITIES,
    name_contains: str | None = None,
) -> list:
    """Select only exposed matching entities, rejecting oversized comparisons.

    Do not quietly drop entities when at the safety limit: doing so could
    produce a convincing but false 'which device ran the longest' ranking.
    """
    if domain not in SUPPORTED_DOMAINS:
        raise ValueError(
            "Unsupported domain; choose light, switch, binary_sensor, fan, "
            "input_boolean, climate or alarm_control_panel."
        )
    if isinstance(limit, bool) or not 1 <= limit <= MAX_COMPARE_ENTITIES:
        raise ValueError(f"max_entities must be 1..{MAX_COMPARE_ENTITIES}")

    needle = (name_contains or "").strip().casefold()
    matching = []
    for entity in states:
        if entity.domain != domain:
            continue
        if needle and needle not in entity.name.casefold():
            continue
        if not exposed(entity.entity_id):
            continue
        matching.append(entity)
        if len(matching) > limit:
            raise ValueError(
                f"More than {limit} Assist-exposed {domain} entities match. "
                "Refine using name_contains or increase max_entities "
                f"(maximum {MAX_COMPARE_ENTITIES}); no partial ranking returned."
            )
    return matching


def rank_entities(
    entries: list[dict], *, metric: str, state: str, limit: int
) -> dict:
    """Return a small ranking using already calculated Recorder summaries.

    Entries contain a per-entity summary and metrics generated using the
    same time window. Each missing interval could be entirely in the
    requested state; compare the observed leader's conservative lower
    bound against EVERY other device's upper bound before proving a
    winner. Counts cannot be bounded during unavailable intervals.
    """
    if metric not in {"total_duration", "transition_count"}:
        raise ValueError("metric must be total_duration or transition_count")
    if isinstance(limit, bool) or not 1 <= limit <= MAX_COMPARE_RESULTS:
        raise ValueError(f"max_results must be 1..{MAX_COMPARE_RESULTS}")
    state = state.casefold().strip()
    if not state or state in {"unknown", "unavailable"}:
        raise ValueError("state must be a meaningful raw state (e.g. on/off)")

    ranked = []
    for entry in entries:
        metrics = entry["metrics"]
        summary = entry["summary"]
        duration_data = metrics["total_observed_duration_by_raw_state"].get(
            state, {}
        )
        seconds = duration_data.get("seconds", 0)
        count = summary["matching_transitions"]
        coverage = metrics["duration_coverage_complete"]
        # An incomplete history without a measured unknown interval cannot
        # establish a winner. This also supports older helper callers.
        missing_seconds = metrics.get(
            "unknown_duration_seconds", 0 if coverage else None
        )
        if missing_seconds is not None:
            missing_seconds = max(0, missing_seconds)
        # A missing initial state is different from an actual unavailable
        # interval. Report them separately instead of implying bad sensors.
        # All compared values are observed time; neither missing region
        # is silently assumed to be off.
        start_unknown = not metrics.get("period_start_state_known", coverage)
        gaps = metrics.get("has_unavailable_gaps", False)
        issues = []
        if start_unknown:
            issues.append("start_state_unknown")
        if gaps:
            issues.append("unavailable_gap")
        result = {
            "entity_id": entry["entity_id"],
            "name": entry["name"],
            "current_state": entry["current_state"],
            "matching_transitions": count,
            "duration_seconds": seconds,
            "duration_human": duration_data.get("human", "0 seconds"),
            "coverage_complete": coverage,
        }
        if missing_seconds is not None:
            # Integer-duration fields are rounded. Add a one-second
            # conservative margin to bounds used for winner proof so
            # sub-second rounding can never create a false winner.
            result["_lower_bound"] = max(0, seconds - 1)
            result["_upper_bound"] = seconds + missing_seconds + 1
        if issues:
            result["coverage_issues"] = issues
            if start_unknown:
                result["initial_unknown_seconds"] = metrics.get(
                    "initial_unknown_seconds"
                )
            result["unknown_duration_seconds"] = missing_seconds
            if gaps:
                result["unavailable_gap_seconds"] = metrics.get(
                    "unavailable_gap_seconds"
                )
            if metric == "total_duration" and missing_seconds is not None:
                result["maximum_possible_duration_seconds"] = result[
                    "_upper_bound"
                ]
        result["score"] = seconds if metric == "total_duration" else count
        ranked.append(result)

    ranked.sort(key=lambda x: (-x["score"], x["name"].casefold(), x["entity_id"]))
    missing = sum(not item["coverage_complete"] for item in ranked)
    unknown_starts = sum(
        "start_state_unknown" in item.get("coverage_issues", [])
        for item in ranked
    )
    unavailable_gaps = sum(
        "unavailable_gap" in item.get("coverage_issues", [])
        for item in ranked
    )
    has_data = bool(ranked)

    # Calculations cover the entire set, NOT just the top N returned.
    # This prevents a hidden lower-ranked device with an enormous
    # outage from incorrectly being excluded as a possible winner.
    winner_proven = False
    winner_margin = None
    if ranked:
        leader = ranked[0]
        others = ranked[1:]
        if metric == "total_duration":
            if "_lower_bound" in leader and all(
                "_upper_bound" in item for item in others
            ):
                strongest_challenger = max(
                    (item["_upper_bound"] for item in others),
                    default=-1,
                )
                winner_margin = leader["_lower_bound"] - strongest_challenger
                winner_proven = winner_margin > 0
        elif missing == 0:
            # A zero-length outage can still conceal any number of
            # transitions. Only fully covered count rankings are proven.
            runner_up = max(
                (item["score"] for item in others), default=-1
            )
            winner_proven = leader["score"] > runner_up

    return {
        "compared_entities": len(ranked),
        "results_returned": min(len(ranked), limit),
        "coverage_incomplete_entities": missing,
        "start_state_unknown_entities": unknown_starts,
        "unavailable_gap_entities": unavailable_gaps,
        "ranking_complete": has_data and missing == 0,
        "winner_proven": winner_proven,
        "winner_margin_min_seconds": (
            winner_margin if winner_proven and metric == "total_duration"
            else None
        ),
        "ranking_basis": (
            "recorded time in state" if metric == "total_duration"
            else "recorded transitions into state"
        ),
        "results": [
            {
                key: value for key, value in item.items()
                if key not in {
                    "score", "_lower_bound", "_upper_bound"
                }
            }
            for item in ranked[:limit]
        ],
        "note": (
            "Recorded winner is still certain: its observed lower bound "
            "exceeds every other device's possible upper bound. "
            "Individual on-times may be underestimated."
            if winner_proven and missing and metric == "total_duration"
            else (
                "Ranking uses observed states only. Unknown initial states "
                "and unavailable intervals may hide additional usage; "
                "the highest recorded value is not a guaranteed winner."
                if missing else None
            )
        ),
    }
