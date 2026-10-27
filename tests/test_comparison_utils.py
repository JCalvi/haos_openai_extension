"""Standard-library regression tests for comparison privacy and ranking."""

from pathlib import Path
import importlib.util
import unittest
from types import SimpleNamespace

PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "haos_openai_extension" / "comparison_utils.py"
)
spec = importlib.util.spec_from_file_location("haos_compare_utils", PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

select_entities = module.select_entities
rank_entities = module.rank_entities


def entity(entity_id: str, name: str):
    return SimpleNamespace(
        entity_id=entity_id, domain=entity_id.split(".")[0], name=name
    )


def result_entry(name, seconds, transitions, coverage=True):
    return {
        "entity_id": "light." + name.lower().replace(" ", "_"),
        "name": name,
        "current_state": "off",
        "summary": {"matching_transitions": transitions},
        "metrics": {
            "total_observed_duration_by_raw_state": {
                "on": {"seconds": seconds, "human": f"{seconds} seconds"}
            },
            "duration_coverage_complete": coverage,
        },
    }


class ComparisonTests(unittest.TestCase):
    def test_only_assist_exposed_lights_are_selected(self):
        states = [
            entity("light.kitchen", "Kitchen"),
            entity("light.secret", "Secret Light"),
            entity("cover.garage_door", "Garage Door"),
            entity("light.hallway", "Hallway"),
        ]
        allowed = {"light.kitchen", "light.hallway", "cover.garage_door"}
        seen = []
        def exposed(eid):
            seen.append(eid)
            return eid in allowed

        selected = select_entities(states, domain="light", exposed=exposed)
        self.assertEqual(
            [value.entity_id for value in selected],
            ["light.kitchen", "light.hallway"],
        )
        self.assertNotIn("cover.garage_door", seen)
        self.assertIn("light.secret", seen)

    def test_never_return_partial_ranking_when_limit_exceeded(self):
        states = [
            entity("light.one", "One"),
            entity("light.two", "Two"),
            entity("light.three", "Three"),
        ]
        with self.assertRaisesRegex(ValueError, "no partial ranking"):
            select_entities(
                states, domain="light", exposed=lambda eid: True, limit=2
            )

    def test_name_contains_can_refine_comparison(self):
        states = [
            entity("light.kitchen", "Kitchen"),
            entity("light.garage", "Garage"),
            entity("light.garage_workbench", "Garage Workbench"),
        ]
        selected = select_entities(
            states, domain="light", exposed=lambda eid: True,
            name_contains="GARAGE",
        )
        self.assertEqual(len(selected), 2)

    def test_no_cover_comparison_allowed(self):
        with self.assertRaisesRegex(ValueError, "Unsupported domain"):
            select_entities(
                [entity("cover.garage", "Garage")],
                domain="cover", exposed=lambda _: True,
            )

    def test_rank_by_duration_and_maintain_counts(self):
        items = [
            result_entry("Hallway", 80, 8),
            result_entry("Stove", 300, 3),
            result_entry("Pantry", 50, 12),
        ]
        result = rank_entities(items, metric="total_duration", state="on", limit=2)
        self.assertEqual(result["compared_entities"], 3)
        self.assertEqual(
            [item["name"] for item in result["results"]],
            ["Stove", "Hallway"],
        )
        self.assertEqual(
            result["results"][0]["matching_transitions"], 3
        )

    def test_event_count_ranks_independently_of_duration(self):
        items = [
            result_entry("Stove", 300, 3),
            result_entry("Pantry", 50, 12),
        ]
        result = rank_entities(
            items, metric="transition_count", state="on", limit=5
        )
        self.assertEqual(result["results"][0]["name"], "Pantry")

    def test_incomplete_coverage_disables_definitive_ranking(self):
        items = [
            result_entry("Stove", 300, 3, coverage=True),
            result_entry("Hallway", 80, 8, coverage=False),
        ]
        result = rank_entities(items, metric="total_duration", state="on", limit=5)
        self.assertFalse(result["ranking_complete"])
        self.assertEqual(result["coverage_incomplete_entities"], 1)
        self.assertTrue(result["note"])

    def test_stable_tie_sorting(self):
        items = [
            result_entry("Zed", 100, 3),
            result_entry("Alice", 100, 3),
        ]
        result = rank_entities(items, metric="total_duration", state="on", limit=5)
        self.assertEqual(result["results"][0]["name"], "Alice")

    def test_compare_limits_are_validated(self):
        items = [result_entry("Stove", 100, 3)]
        with self.assertRaisesRegex(ValueError, "max_results"):
            rank_entities(items, metric="total_duration", state="on", limit=11)
        with self.assertRaisesRegex(ValueError, "max_entities"):
            select_entities(
                [entity("light.x", "X")], domain="light",
                exposed=lambda _: True, limit=41
            )

    def test_invalid_raw_state_rejected(self):
        with self.assertRaisesRegex(ValueError, "meaningful raw state"):
            rank_entities(
                [], metric="transition_count", state="unavailable", limit=5
            )


if __name__ == "__main__":
    unittest.main()
