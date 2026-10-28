"""Mocked Home Assistant integration contract checks (stdlib only).

Import llm.py against lightweight HA shims, so CI can verify actual tool
registration, privacy checks and Recorder call count without installing the
entire Home Assistant stack.
"""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

COMPONENT = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "haos_openai_extension"
)
TZ = ZoneInfo("Australia/Melbourne")
NOW = datetime(2026, 10, 27, 18, 30, tzinfo=TZ)


def make_module(name, **attributes):
    module = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    return module


class OptKey(str):
    def __new__(cls, value, default=None):
        instance = str.__new__(cls, value)
        instance.default = default
        return instance


class Schema:
    def __init__(self, fields):
        self.fields = fields

    def __call__(self, passed):
        result = dict(passed)
        for field in self.fields:
            if isinstance(field, OptKey) and field.default is not None:
                result.setdefault(str(field), field.default)
        return result


class ToolResult:
    def __init__(self, data, error=False):
        self.data = data
        self.error = error


class LLMAssistContext:
    assistant = "main_assistant"


class State:
    def __init__(self, entity_id, name, state, device_class=None):
        self.entity_id = entity_id
        self.name = name
        self.state = state
        self.domain = entity_id.split(".")[0]
        self.attributes = (
            {"device_class": device_class} if device_class else {}
        )


def historical(state, time):
    return types.SimpleNamespace(
        state=state, last_changed=time, last_updated=time
    )


class FakeStateMachine:
    def __init__(self, states):
        self.entries = {state.entity_id: state for state in states}

    def async_all(self):
        return list(self.entries.values())

    def get(self, entity_id):
        return self.entries.get(entity_id)


class FakeRecorder:
    def __init__(self, hass):
        self.hass = hass
        self.calls = 0
        self.ids_requested = []

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)


class FakeHass:
    def __init__(self):
        self.states = FakeStateMachine([
            State("light.stove", "Stove Bench Light", "off"),
            State("light.hall", "Hallway Light", "off"),
            State("light.secret", "Secret Light", "off"),
            State("cover.garage", "Garage Door", "closed"),
        ])
        self.allowed = {"light.stove", "light.hall"}
        self.recorder = FakeRecorder(self)


def get_significant_states(
    hass, start, end, entity_ids, filters, include_start,
    significant_only, minimal, no_attributes
):
    hass.recorder.calls += 1
    hass.recorder.ids_requested.append(list(entity_ids))
    assert include_start and significant_only and no_attributes
    assert not minimal
    dataset = {
        "light.stove": [
            historical("off", NOW.replace(hour=0, minute=0)),
            historical("on", NOW.replace(hour=10, minute=0)),
            historical("off", NOW.replace(hour=11, minute=0)),
        ],
        "light.hall": [
            historical("off", NOW.replace(hour=0, minute=0)),
            historical("on", NOW.replace(hour=10, minute=0)),
            historical("off", NOW.replace(hour=10, minute=5)),
        ],
        "light.secret": [
            historical("off", NOW.replace(hour=0, minute=0)),
            historical("on", NOW.replace(hour=1, minute=0)),
            historical("off", NOW.replace(hour=18, minute=0)),
        ],
    }
    if hasattr(hass.recorder, "custom_dataset"):
        dataset = hass.recorder.custom_dataset
    result = {}
    for entity_id in entity_ids:
        data = dataset.get(entity_id, [])
        in_range = [item for item in data if start <= item.last_updated <= end]
        earlier = [item for item in data if item.last_updated < start]
        if earlier and include_start:
            in_range = [earlier[-1]] + in_range
        result[entity_id] = in_range
    return result


def load_llm_module():
    pkg_name = "_haos_contract_test_pkg"
    package = types.ModuleType(pkg_name)
    package.__path__ = [str(COMPONENT)]
    sys.modules[pkg_name] = package

    packages = [
        "homeassistant", "homeassistant.components",
        "homeassistant.helpers", "homeassistant.util",
    ]
    fake = {key: make_module(key) for key in packages}
    fake.update({
        "probatio": make_module(
            "probatio", Optional=OptKey, Schema=Schema
        ),
        "homeassistant.components.llm": make_module(
            "homeassistant.components.llm",
            LLMTools=type(
                "LLMTools", (),
                {"__init__": lambda self, tools, prompt: (
                    setattr(self, "tools", tools),
                    setattr(self, "prompt", prompt),
                    None,
                )[-1]}
            ),
        ),
        "homeassistant.components.homeassistant": make_module(
            "homeassistant.components.homeassistant",
            async_should_expose=lambda hass, assistant, eid: (
                assistant == "main_assistant" and eid in hass.allowed
            ),
        ),
        "homeassistant.components.recorder": make_module(
            "homeassistant.components.recorder",
            get_instance=lambda hass: hass.recorder,
            history=types.SimpleNamespace(
                get_significant_states=get_significant_states
            ),
        ),
        "homeassistant.core": make_module(
            "homeassistant.core", HomeAssistant=FakeHass, State=State,
            callback=lambda fn: fn,
        ),
        "homeassistant.helpers.llm": make_module(
            "homeassistant.helpers.llm",
            LLM_API_ASSIST="assist",
            LLMContext=LLMAssistContext,
            Tool=type("Tool", (), {}),
            ToolAnnotations=lambda **kwargs: kwargs,
            ToolInput=object,
            ToolResult=ToolResult,
        ),
        "homeassistant.util.dt": make_module(
            "homeassistant.util.dt",
            now=lambda: NOW,
            as_utc=lambda value: value.astimezone(timezone.utc),
            as_local=lambda value: value.astimezone(TZ),
        ),
    })
    fake["homeassistant.util"].dt = fake["homeassistant.util.dt"]
    fake["homeassistant.components"].llm = fake["homeassistant.components.llm"]
    with patch.dict(sys.modules, fake):
        spec = importlib.util.spec_from_file_location(
            pkg_name + ".llm", COMPONENT / "llm.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


llm_module = load_llm_module()


class ToolContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.hass = FakeHass()

    def test_both_tools_are_registered_and_read_only(self):
        instance = llm_module.async_get_tools(
            self.hass, LLMAssistContext(), "assist"
        )
        self.assertEqual(
            {tool.name for tool in instance.tools},
            {
                "haos_openai_extension__get_history",
                "haos_openai_extension__compare_history",
            },
        )
        self.assertTrue(all(tool.annotations["read_only"] for tool in instance.tools))
        self.assertIsNone(
            llm_module.async_get_tools(
                self.hass, LLMAssistContext(), "some_other_api"
            )
        )

    async def test_latest_with_last_7_days_uses_one_complete_query(self):
        """Luna may specify latest and an explicit range together."""
        tool = llm_module.HistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "entity_id": "light.stove",
                "period": "last_7_days",
                "state": "off",
                "query_mode": "latest",
                "detail": "summary",
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(self.hass.recorder.calls, 1)
        self.assertEqual(response.data["period_name"], "last_7_days")
        self.assertEqual(response.data["detail"], "summary")
        self.assertIn("matching_transitions", response.data)
        self.assertNotIn("events_newest_first", response.data)
        self.assertNotIn("searched_hours", response.data)

    async def test_latest_with_yesterday_never_expands_outside_yesterday(self):
        tool = llm_module.HistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "entity_id": "light.stove",
                "period": "yesterday",
                "state": "off",
                "query_mode": "latest",
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(response.data["period_name"], "yesterday")
        self.assertEqual(self.hass.recorder.calls, 1)
        self.assertIsNone(response.data["latest_matching_transition"])

    async def test_comparison_queries_recorder_once_for_exposed_lights(self):
        tool = llm_module.CompareHistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "domain": "light", "period": "today"
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(self.hass.recorder.calls, 1)
        self.assertEqual(
            self.hass.recorder.ids_requested[0],
            ["light.stove", "light.hall"],
        )
        self.assertEqual(response.data["compared_entities"], 2)
        self.assertEqual(response.data["results"][0]["name"], "Stove Bench Light")
        self.assertEqual(response.data["results"][0]["duration_seconds"], 3600)
        self.assertNotIn("light.secret", str(response.data))
        self.assertNotIn("cover.garage", str(response.data))

    async def test_batched_comparison_proves_winner_through_short_outage(self):
        self.hass.recorder.custom_dataset = {
            "light.stove": [
                historical("off", NOW.replace(hour=0, minute=0)),
                historical("on", NOW.replace(hour=10, minute=0)),
                historical("off", NOW.replace(hour=11, minute=0)),
                historical("unavailable", NOW.replace(hour=11, minute=30)),
                historical("off", NOW.replace(hour=11, minute=31)),
            ],
            "light.hall": [
                historical("off", NOW.replace(hour=0, minute=0)),
                historical("on", NOW.replace(hour=10, minute=0)),
                historical("off", NOW.replace(hour=10, minute=5)),
            ],
        }
        response = await llm_module.CompareHistoryTool().async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "domain": "light", "period": "today"
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(self.hass.recorder.calls, 1)
        self.assertEqual(response.data["unavailable_gap_entities"], 1)
        self.assertEqual(response.data["results"][0]["duration_seconds"], 3600)
        self.assertEqual(response.data["results"][0]["unavailable_gap_seconds"], 60)
        self.assertFalse(response.data["ranking_complete"])
        self.assertTrue(response.data["winner_proven"])

    async def test_batched_comparison_refuses_proof_if_loser_gap_is_large(self):
        self.hass.recorder.custom_dataset = {
            "light.stove": [
                historical("off", NOW.replace(hour=0, minute=0)),
                historical("on", NOW.replace(hour=10, minute=0)),
                historical("off", NOW.replace(hour=11, minute=0)),
            ],
            "light.hall": [
                historical("off", NOW.replace(hour=0, minute=0)),
                historical("on", NOW.replace(hour=10, minute=0)),
                historical("unavailable", NOW.replace(hour=10, minute=2)),
                historical("off", NOW.replace(hour=13, minute=0)),
            ],
        }
        response = await llm_module.CompareHistoryTool().async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "domain": "light", "period": "today", "max_results": 1
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(self.hass.recorder.calls, 1)
        self.assertEqual(response.data["results"][0]["name"], "Stove Bench Light")
        self.assertFalse(response.data["winner_proven"])
        self.assertEqual(response.data["coverage_incomplete_entities"], 1)

    async def test_comparison_explicit_hours_is_rolling_window(self):
        tool = llm_module.CompareHistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "domain": "light", "hours": 4
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(response.data["period_name"], "last_4_hours")
        self.assertEqual(self.hass.recorder.calls, 1)

    async def test_comparison_over_limit_fails_before_recorder(self):
        tool = llm_module.CompareHistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "domain": "light", "max_entities": 1
            }), LLMAssistContext()
        )
        self.assertTrue(response.error)
        self.assertEqual(self.hass.recorder.calls, 0)

    async def test_unexposed_single_entity_is_rejected_before_query(self):
        tool = llm_module.HistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "entity_id": "light.secret", "state": "on"
            }), LLMAssistContext()
        )
        self.assertTrue(response.error)
        self.assertEqual(self.hass.recorder.calls, 0)

    async def test_latest_single_event_uses_one_query(self):
        tool = llm_module.HistoryTool()
        response = await tool.async_call(
            self.hass, types.SimpleNamespace(tool_args={
                "entity_id": "light.stove",
                "state": "off",
                "query_mode": "latest",
            }), LLMAssistContext()
        )
        self.assertFalse(response.error, response.data)
        self.assertEqual(self.hass.recorder.calls, 1)
        self.assertEqual(response.data["searched_hours"], 12)
        self.assertEqual(
            response.data["latest_matching_transition"]["time"],
            NOW.replace(hour=11, minute=0).isoformat(),
        )


if __name__ == "__main__":
    unittest.main()
