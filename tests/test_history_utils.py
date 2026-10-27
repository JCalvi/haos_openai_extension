"""Regression checks for deterministic last-event selection (stdlib only)."""

from pathlib import Path
from datetime import datetime
import importlib.util
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "haos_openai_extension"
    / "history_utils.py"
)
spec = importlib.util.spec_from_file_location("haos_history_utils", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
summarize_transitions = module.summarize_transitions


class HistorySummaryTests(unittest.TestCase):
    def setUp(self):
        # Intentionally unsorted: an older 11:48 closing must never win
        # over the newer 13:18 closing shown in the user's HA history.
        timestamps = [
            ("off", "2026-10-27T11:48:11+11:00"),
            ("off", "2026-10-27T13:18:10+11:00"),
            ("on", "2026-10-27T13:16:08+11:00"),
        ]
        self.events = [
            {
                "state": state,
                "time": timestamp,
                "sort_time": datetime.fromisoformat(timestamp).timestamp(),
            }
            for state, timestamp in timestamps
        ]

    def test_last_closed_is_118pm_not_1148am(self):
        result = summarize_transitions(self.events, "off", 80)
        self.assertEqual(
            result["latest_matching_transition"]["time"],
            "2026-10-27T13:18:10+11:00",
        )
        self.assertEqual(result["matching_transitions"], 2)
        self.assertEqual(result["events_newest_first"][0]["state"], "off")

    def test_unfiltered_has_latest_for_each_raw_state(self):
        result = summarize_transitions(self.events, None, 80)
        self.assertEqual(
            result["latest_by_raw_state"]["off"]["time"],
            "2026-10-27T13:18:10+11:00",
        )
        self.assertEqual(
            result["latest_by_raw_state"]["on"]["time"],
            "2026-10-27T13:16:08+11:00",
        )

    def test_empty_history_never_invents_time(self):
        result = summarize_transitions([], "off", 80)
        self.assertIsNone(result["latest_matching_transition"])
        self.assertEqual(result["latest_by_raw_state"], {})

    def test_event_limit_preserves_newest(self):
        result = summarize_transitions(self.events, "off", 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(
            result["events_newest_first"][0]["time"],
            "2026-10-27T13:18:10+11:00",
        )


if __name__ == "__main__":
    unittest.main()
