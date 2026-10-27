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
    same time window. Do not mix incompletely covered histories into a
    claimed definitive winner.
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
        result = {
            "entity_id": entry["entity_id"],
            "name": entry["name"],
            "current_state": entry["current_state"],
            "matching_transitions": count,
            "duration_seconds": seconds,
            "duration_human": duration_data.get("human", "0 seconds"),
            "coverage_complete": coverage,
        }
        result["score"] = seconds if metric == "total_duration" else count
        ranked.append(result)

    ranked.sort(key=lambda x: (-x["score"], x["name"].casefold(), x["entity_id"]))
    missing = sum(not item["coverage_complete"] for item in ranked)
    has_data = bool(ranked)
    return {
        "compared_entities": len(ranked),
        "results_returned": min(len(ranked), limit),
        "coverage_incomplete_entities": missing,
        "ranking_complete": has_data and missing == 0,
        "ranking_basis": (
            "recorded time in state" if metric == "total_duration"
            else "recorded transitions into state"
        ),
        "results": [
            {key: value for key, value in item.items() if key != "score"}
            for item in ranked[:limit]
        ],
        "note": (
            "Ranking uses recorded states only; unavailable data, missed "
            "transitions or retention gaps can affect comparisons."
        ) if missing else None,
    }
