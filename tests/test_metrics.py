"""Regression tests for exact durations and state/time confidence."""

from datetime import datetime
from pathlib import Path
import importlib.util
import unittest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "haos_openai_extension"
    / "metrics.py"
)
spec = importlib.util.spec_from_file_location("haos_metrics", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
calculate_history_metrics = module.calculate_history_metrics
format_duration = module.format_duration


def ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def sample(state: str, time: str) -> dict:
    return {"state": state, "time": time, "sort_time": ts(time)}


class MetricsTests(unittest.TestCase):
    def test_elapsed_closed_uses_real_1318_event_not_reboot(self):
        end_time = "2026-10-27T17:10:45.347304+11:00"
        opening = "2026-10-27T13:16:08.227334+11:00"
        closing = "2026-10-27T13:18:10.992800+11:00"
        samples = [
            sample("off", "2026-10-26T18:00:00+11:00"),
            sample("on", opening),
            sample("off", closing),
            sample("unknown", "2026-10-27T15:00:00+11:00"),
            sample("off", "2026-10-27T15:32:19+11:00"),
            sample("unavailable", "2026-10-27T16:00:00+11:00"),
            sample("off", "2026-10-27T16:40:07+11:00"),
            sample("off", "2026-10-27T16:52:39+11:00"),
        ]
        data = calculate_history_metrics(
            samples, start_timestamp=ts("2026-10-26T17:10:45+11:00"),
            end_timestamp=ts(end_time), current_state="off",
            is_live_window=True
        )
        self.assertEqual(data["current_state_duration_seconds"], 13954)
        self.assertEqual(
            data["current_state_duration_human"], "3 hours 52 minutes"
        )
        self.assertEqual(data["current_state_duration_since"], closing)
        self.assertEqual(
            data["latest_completed_interval_by_raw_state"]["on"]["duration_seconds"],
            123,
        )
        self.assertTrue(data["has_unavailable_gaps"])
        self.assertFalse(data["duration_coverage_complete"])

    def test_three_openings_yesterday_total_eleven_minutes(self):
        begin = "2026-10-26T00:00:00+11:00"
        end = "2026-10-27T00:00:00+11:00"
        samples = [
            sample("off", "2026-10-25T18:00:00+11:00"),
            sample("on", "2026-10-26T08:00:00+11:00"),
            sample("off", "2026-10-26T08:03:00+11:00"),
            sample("on", "2026-10-26T12:15:00+11:00"),
            sample("off", "2026-10-26T12:18:00+11:00"),
            sample("on", "2026-10-26T16:30:00+11:00"),
            sample("off", "2026-10-26T16:35:00+11:00"),
        ]
        data = calculate_history_metrics(
            samples, start_timestamp=ts(begin),
            end_timestamp=ts(end), current_state="off",
            is_live_window=False
        )
        self.assertEqual(
            data["total_observed_duration_by_raw_state"]["on"]["seconds"],
            660,
        )
        self.assertEqual(
            data["total_observed_duration_by_raw_state"]["on"]["human"],
            "11 minutes",
        )
        self.assertTrue(data["duration_coverage_complete"])
        self.assertNotIn("current_state_duration_seconds", data)

    def test_unknown_gap_not_counted_toward_observed_duration(self):
        samples = [
            sample("off", "2026-10-27T10:00:00+11:00"),
            sample("on", "2026-10-27T10:10:00+11:00"),
            sample("unavailable", "2026-10-27T10:12:00+11:00"),
            sample("on", "2026-10-27T10:14:00+11:00"),
            sample("off", "2026-10-27T10:20:00+11:00"),
        ]
        data = calculate_history_metrics(
            samples,
            start_timestamp=ts("2026-10-27T10:00:00+11:00"),
            end_timestamp=ts("2026-10-27T10:30:00+11:00"),
            current_state="off", is_live_window=True,
        )
        self.assertEqual(
            data["total_observed_duration_by_raw_state"]["on"]["seconds"],
            8 * 60,
        )
        self.assertTrue(data["has_unavailable_gaps"])
        self.assertFalse(data["duration_coverage_complete"])

    def test_invalid_current_state_does_not_have_elapsed_time(self):
        data = calculate_history_metrics(
            [
                sample("off", "2026-10-27T10:00:00+11:00"),
                sample("on", "2026-10-27T10:10:00+11:00"),
            ],
            start_timestamp=ts("2026-10-27T10:00:00+11:00"),
            end_timestamp=ts("2026-10-27T10:20:00+11:00"),
            current_state="unavailable", is_live_window=True,
        )
        self.assertNotIn("current_state_duration_seconds", data)

    def test_no_change_in_window_does_not_invent_current_start(self):
        data = calculate_history_metrics(
            [sample("off", "2026-10-27T10:00:00+11:00")],
            start_timestamp=ts("2026-10-27T10:00:00+11:00"),
            end_timestamp=ts("2026-10-27T10:20:00+11:00"),
            current_state="off", is_live_window=True
        )
        self.assertIsNone(data["current_state_duration_seconds"])

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0 seconds")
        self.assertEqual(format_duration(11 * 60), "11 minutes")
        self.assertEqual(format_duration(123), "2 minutes")
        self.assertEqual(
            format_duration(3 * 3600 + 52 * 60 + 34), "3 hours 52 minutes"
        )


if __name__ == "__main__":
    unittest.main()
