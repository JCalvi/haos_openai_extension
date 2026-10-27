"""Regression tests for relative and explicit Recorder time windows."""

from datetime import datetime, timedelta
from pathlib import Path
import importlib.util
import unittest
from zoneinfo import ZoneInfo

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "haos_openai_extension"
    / "window_utils.py"
)
spec = importlib.util.spec_from_file_location("haos_window_utils", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
resolve_history_window = module.resolve_history_window


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.zone = ZoneInfo("Australia/Melbourne")
        self.now = datetime(2026, 10, 27, 17, 10, 45, tzinfo=self.zone)

    def test_yesterday_uses_midnight_boundaries(self):
        start, end, label = resolve_history_window(
            self.now, period="yesterday"
        )
        self.assertEqual(start.isoformat(), "2026-10-26T00:00:00+11:00")
        self.assertEqual(end.isoformat(), "2026-10-27T00:00:00+11:00")
        self.assertEqual(label, "yesterday")

    def test_this_week_starts_monday(self):
        start, end, _ = resolve_history_window(
            self.now, period="this_week"
        )
        self.assertEqual(start.date().isoformat(), "2026-10-26")
        self.assertEqual(start.hour, 0)
        self.assertEqual(end, self.now)

    def test_last_week_from_tuesday_is_within_ten_days(self):
        start, end, _ = resolve_history_window(
            self.now, period="last_week"
        )
        self.assertEqual(start.date().isoformat(), "2026-10-19")
        self.assertEqual(end.date().isoformat(), "2026-10-26")

    def test_last_week_too_far_back_is_error_not_partial(self):
        late_sunday = datetime(
            2026, 11, 1, 19, 0, tzinfo=self.zone
        )
        with self.assertRaisesRegex(ValueError, "more than 240 hours"):
            resolve_history_window(late_sunday, period="last_week")

    def test_last_night_is_six_pm_to_six_am(self):
        start, end, _ = resolve_history_window(
            self.now, period="last_night"
        )
        self.assertEqual(start.isoformat(), "2026-10-26T18:00:00+11:00")
        self.assertEqual(end.isoformat(), "2026-10-27T06:00:00+11:00")

    def test_default_lookback_remains_240_hours(self):
        start, end, label = resolve_history_window(self.now)
        self.assertEqual((end - start).total_seconds(), 240 * 3600)
        self.assertEqual(label, "last_240_hours")

    def test_explicit_naive_timestamps_are_local_melbourne(self):
        start, end, label = resolve_history_window(
            self.now, start_time="2026-10-27T14:00",
            end_time="2026-10-27T17:00"
        )
        self.assertEqual(start.isoformat(), "2026-10-27T14:00:00+11:00")
        self.assertEqual(end.isoformat(), "2026-10-27T17:00:00+11:00")
        self.assertEqual(label, "custom")

    def test_explicit_offset_timezone_is_preserved(self):
        start, end, _ = resolve_history_window(
            self.now, start_time="2026-10-27T03:00:00Z",
            end_time="2026-10-27T05:00:00Z",
        )
        self.assertEqual(start.utcoffset(), timedelta(0))
        self.assertEqual(end.utcoffset(), timedelta(0))

    def test_future_window_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            resolve_history_window(
                self.now, start_time="2026-10-27T19:00",
                end_time="2026-10-27T20:00"
            )

    def test_different_time_options_cannot_be_mixed(self):
        with self.assertRaisesRegex(ValueError, "OR"):
            resolve_history_window(
                self.now, period="today",
                start_time="2026-10-27T12:00",
            )

    def test_rolling_240_hours_across_dst_stays_240_hours(self):
        from datetime import timezone
        after_dst = datetime(2026, 10, 7, 15, 0, tzinfo=self.zone)
        start, end, _ = resolve_history_window(after_dst, hours=240)
        seconds = (
            end.astimezone(timezone.utc)
            - start.astimezone(timezone.utc)
        ).total_seconds()
        self.assertEqual(seconds, 240 * 3600)

    def test_rolling_seven_days_across_dst_stays_168_hours(self):
        from datetime import timezone
        after_dst = datetime(2026, 10, 7, 15, 0, tzinfo=self.zone)
        start, end, _ = resolve_history_window(
            after_dst, period="last_7_days"
        )
        seconds = (
            end.astimezone(timezone.utc)
            - start.astimezone(timezone.utc)
        ).total_seconds()
        self.assertEqual(seconds, 168 * 3600)

    def test_dst_calendar_day_is_not_assumed_24_hours(self):
        # DST starts in Melbourne on 2026-10-04; 02:00 jumps to 03:00.
        dst_today = datetime(2026, 10, 4, 16, 0, tzinfo=self.zone)
        start, end, _ = resolve_history_window(
            dst_today, period="today"
        )
        self.assertEqual(start.utcoffset(), timedelta(hours=10))
        self.assertEqual(end.utcoffset(), timedelta(hours=11))
        from datetime import timezone
        actual_seconds = (
            end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
        ).total_seconds()
        self.assertEqual(actual_seconds, 15 * 3600)


if __name__ == "__main__":
    unittest.main()
