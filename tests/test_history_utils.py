"""Regression tests for true Recorder transitions (Python stdlib only)."""

from datetime import datetime
from pathlib import Path
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


def sample(state: str, timestamp: str) -> dict:
    return {
        "state": state,
        "time": timestamp,
        "sort_time": datetime.fromisoformat(timestamp).timestamp(),
    }


class HistorySummaryTests(unittest.TestCase):
    """Use the actual garage door times reported in the October 27 trace."""

    def setUp(self):
        self.baseline = sample("off", "2026-10-26T18:00:00+11:00")
        self.items = [
            self.baseline,
            sample("off", "2026-10-26T19:17:26+11:00"),
            sample("off", "2026-10-27T09:28:01+11:00"),
            sample("on", "2026-10-27T10:10:00+11:00"),
            sample("off", "2026-10-27T10:11:59+11:00"),
            sample("off", "2026-10-27T11:29:31+11:00"),
            sample("on", "2026-10-27T11:47:00+11:00"),
            sample("off", "2026-10-27T11:48:11+11:00"),
            sample("on", "2026-10-27T13:16:08+11:00"),
            sample("off", "2026-10-27T13:18:10+11:00"),
            sample("unknown", "2026-10-27T15:00:00+11:00"),
            sample("off", "2026-10-27T15:32:19+11:00"),
            sample("unavailable", "2026-10-27T16:00:00+11:00"),
            sample("off", "2026-10-27T16:32:20+11:00"),
            sample("off", "2026-10-27T16:40:07+11:00"),
            sample("off", "2026-10-27T16:52:39+11:00"),
        ]

    def test_last_closing_is_1318_not_restart_at_1652(self):
        result = summarize_transitions(self.items, "off", 80)
        self.assertEqual(
            result["latest_matching_transition"]["time"],
            "2026-10-27T13:18:10+11:00",
        )
        self.assertEqual(result["matching_transitions"], 3)
        self.assertEqual(
            [event["time"] for event in result["events_newest_first"]],
            [
                "2026-10-27T13:18:10+11:00",
                "2026-10-27T11:48:11+11:00",
                "2026-10-27T10:11:59+11:00",
            ],
        )

    def test_unfiltered_latest_by_state_only_counts_real_changes(self):
        result = summarize_transitions(self.items, None, 80)
        self.assertEqual(result["matching_transitions"], 6)
        self.assertEqual(result["latest_by_raw_state"]["off"]["time"],
                         "2026-10-27T13:18:10+11:00")
        self.assertEqual(result["latest_by_raw_state"]["on"]["time"],
                         "2026-10-27T13:16:08+11:00")
        self.assertEqual(
            [event["state"] for event in result["events_newest_first"]],
            ["off", "on", "off", "on", "off", "on"],
        )

    def test_unsorted_recorder_samples_are_still_chronological(self):
        result = summarize_transitions(list(reversed(self.items)), "off", 80)
        self.assertEqual(
            result["latest_matching_transition"]["time"],
            "2026-10-27T13:18:10+11:00",
        )

    def test_invalid_readings_do_not_count_as_events(self):
        result = summarize_transitions(self.items, None, 80)
        self.assertNotIn("unknown", result["latest_by_raw_state"])
        self.assertNotIn("unavailable", result["latest_by_raw_state"])
        self.assertTrue(all(
            event["state"] in {"on", "off"}
            for event in result["events_newest_first"]
        ))

    def test_same_state_before_and_after_unknown_is_not_a_change(self):
        result = summarize_transitions([
            sample("off", "2026-10-27T11:00:00+11:00"),
            sample("unknown", "2026-10-27T12:00:00+11:00"),
            sample("unavailable", "2026-10-27T12:10:00+11:00"),
            sample("off", "2026-10-27T13:00:00+11:00"),
        ], "off", 80)
        self.assertEqual(result["matching_transitions"], 0)
        self.assertIsNone(result["latest_matching_transition"])

    def test_valid_change_after_unavailable_is_detected(self):
        result = summarize_transitions([
            sample("off", "2026-10-27T11:00:00+11:00"),
            sample("unavailable", "2026-10-27T12:00:00+11:00"),
            sample("on", "2026-10-27T13:00:00+11:00"),
        ], "on", 80)
        self.assertEqual(result["matching_transitions"], 1)

    def test_single_first_sample_is_baseline_not_event(self):
        result = summarize_transitions([
            sample("off", "2026-10-27T13:00:00+11:00"),
        ], "off", 80)
        self.assertIsNone(result["latest_matching_transition"])

    def test_initial_baseline_prior_to_interval_is_not_returned(self):
        cutoff = datetime.fromisoformat(
            "2026-10-27T12:00:00+11:00"
        ).timestamp()
        result = summarize_transitions(
            self.items, "off", 80, start_timestamp=cutoff
        )
        self.assertEqual(result["matching_transitions"], 1)
        self.assertEqual(
            result["latest_matching_transition"]["time"],
            "2026-10-27T13:18:10+11:00",
        )

    def test_all_invalid_samples_have_no_transitions(self):
        result = summarize_transitions([
            sample("unknown", "2026-10-27T12:00:00+11:00"),
            sample("unavailable", "2026-10-27T13:00:00+11:00"),
        ], None, 80)
        self.assertIsNone(result["latest_matching_transition"])
        self.assertEqual(result["latest_by_raw_state"], {})
        self.assertEqual(result["matching_transitions"], 0)

    def test_truncation_returns_newest_only(self):
        result = summarize_transitions(self.items, "off", 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["events_newest_first"]), 1)
        self.assertEqual(
            result["events_newest_first"][0]["time"],
            "2026-10-27T13:18:10+11:00",
        )


if __name__ == "__main__":
    unittest.main()
