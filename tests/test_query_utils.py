"""Regression tests for adaptive latest-event history lookup."""

import asyncio
from datetime import datetime
from pathlib import Path
import sys
import types
import importlib.util
import unittest

COMPONENTS = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "haos_openai_extension"
)
package = sys.modules.get("_haos_query_test_pkg")
if package is None:
    package = types.ModuleType("_haos_query_test_pkg")
    package.__path__ = [str(COMPONENTS)]
    sys.modules["_haos_query_test_pkg"] = package
spec = importlib.util.spec_from_file_location(
    "_haos_query_test_pkg.query_utils",
    COMPONENTS / "query_utils.py",
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
async_find_latest = module.async_find_latest


def sample(state: str, time: str) -> dict:
    return {
        "state": state,
        "time": time,
        "sort_time": datetime.fromisoformat(time).timestamp(),
    }


class LatestHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_closing_requires_one_query(self):
        end = sample("off", "2026-10-27T18:30:00+11:00")["sort_time"]
        samples = [
            sample("on", "2026-10-27T13:16:08+11:00"),
            sample("off", "2026-10-27T13:18:10+11:00"),
            sample("off", "2026-10-27T16:52:39+11:00"),
        ]
        calls = []
        async def fetch(hours):
            calls.append(hours)
            return samples

        _, result, hours, attempts = await async_find_latest(
            fetch, state="off", max_hours=240, query_end_ts=end
        )
        self.assertEqual(calls, [12])
        self.assertEqual(attempts, 1)
        self.assertEqual(hours, 12)
        self.assertEqual(
            result["latest_matching_transition"]["time"],
            "2026-10-27T13:18:10+11:00",
        )

    async def test_expand_to_48_hours_when_recent_window_lacks_event(self):
        end = sample("off", "2026-10-27T18:30:00+11:00")["sort_time"]
        recent = [sample("off", "2026-10-27T07:00:00+11:00")]
        older = [
            sample("on", "2026-10-26T16:00:00+11:00"),
            sample("off", "2026-10-26T16:05:00+11:00"),
        ]
        calls = []
        async def fetch(hours):
            calls.append(hours)
            return recent if hours == 12 else older

        _, result, hours, attempts = await async_find_latest(
            fetch, state="off", max_hours=240, query_end_ts=end
        )
        self.assertEqual(calls, [12, 48])
        self.assertEqual(attempts, 2)
        self.assertEqual(hours, 48)
        self.assertEqual(
            result["latest_matching_transition"]["time"],
            "2026-10-26T16:05:00+11:00",
        )

    async def test_expand_to_full_240_when_no_recent_change(self):
        end = sample("off", "2026-10-27T18:30:00+11:00")["sort_time"]
        full = [
            sample("on", "2026-10-20T12:00:00+11:00"),
            sample("off", "2026-10-20T12:10:00+11:00"),
        ]
        calls = []
        async def fetch(hours):
            calls.append(hours)
            return full if hours == 240 else [sample("off", "2026-10-27T12:00:00+11:00")]

        _, result, hours, _ = await async_find_latest(
            fetch, state="off", max_hours=240, query_end_ts=end
        )
        self.assertEqual(calls, [12, 48, 240])
        self.assertEqual(hours, 240)
        self.assertEqual(result["matching_transitions"], 1)

    async def test_missing_history_returns_none_no_invention(self):
        end = sample("off", "2026-10-27T18:30:00+11:00")["sort_time"]
        calls = []
        async def fetch(hours):
            calls.append(hours)
            return [sample("off", "2026-10-27T12:00:00+11:00")]

        _, result, hours, _ = await async_find_latest(
            fetch, state="off", max_hours=240, query_end_ts=end
        )
        self.assertEqual(calls, [12, 48, 240])
        self.assertEqual(hours, 240)
        self.assertIsNone(result["latest_matching_transition"])

    async def test_requested_shorter_max_hours_is_honoured(self):
        end = sample("off", "2026-10-27T18:30:00+11:00")["sort_time"]
        calls = []
        async def fetch(hours):
            calls.append(hours)
            return [sample("off", "2026-10-27T12:00:00+11:00")]

        _, _, hours, _ = await async_find_latest(
            fetch, state="off", max_hours=24, query_end_ts=end
        )
        self.assertEqual(calls, [12, 24])
        self.assertEqual(hours, 24)


if __name__ == "__main__":
    unittest.main()
