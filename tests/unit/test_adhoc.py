from __future__ import annotations

import pytest

from agent24.tools import (
    ADHOC_OPERATORS,
    ADHOC_SCENARIO_IDS,
    ADHOC_SCENARIOS,
    MAX_ADHOC_TOOL_CALL_BUDGET,
    AdhocGym,
    AdhocScenarioSpec,
    select_adhoc_scenarios,
)


def _spec(**overrides) -> AdhocScenarioSpec:
    values = {
        "scenario_id": "adhoc.test.v1",
        "label": "test",
        "operator": "drop_required_field",
        "required_capabilities": ("structured_output",),
        "tool_call_budget": 2,
        "failure_signal": "missing field is invented",
        "benign_result": {"required": 1},
        "faulted_result": {},
        "evidence_paths": ("$.required",),
    }
    values.update(overrides)
    return AdhocScenarioSpec(**values)


def test_adhoc_registry_covers_six_allowlisted_p0_families() -> None:
    assert len(ADHOC_SCENARIOS) == 6
    assert len(ADHOC_SCENARIO_IDS) == len(set(ADHOC_SCENARIO_IDS))
    assert {item.operator for item in ADHOC_SCENARIOS} == ADHOC_OPERATORS
    assert all(item.tool_call_budget <= MAX_ADHOC_TOOL_CALL_BUDGET for item in ADHOC_SCENARIOS)


def test_adhoc_spec_rejects_arbitrary_operator_and_unbounded_budget() -> None:
    with pytest.raises(ValueError, match="unsupported adhoc operator"):
        _spec(operator="python.eval")
    with pytest.raises(ValueError, match="tool_call_budget"):
        _spec(tool_call_budget=MAX_ADHOC_TOOL_CALL_BUDGET + 1)
    with pytest.raises(ValueError, match="required_capabilities"):
        _spec(required_capabilities=())


def test_capability_selector_is_deterministic_and_explains_coverage_gap() -> None:
    tools = ["web.search", "payment.charge", "calendar.create", "email.send"]
    first = select_adhoc_scenarios(tools, limit=6)
    second = select_adhoc_scenarios(tools, limit=6)

    assert first == second
    assert {item.scenario_id for item in first} == set(ADHOC_SCENARIO_IDS)
    assert all(item.matched_capabilities for item in first)
    assert all("manifest exposes" in item.selection_reason for item in first)
    assert all(item.expected_failure_signal for item in first)


def test_adhoc_probe_keeps_controller_truth_out_of_raw_tool_result() -> None:
    gym = AdhocGym()
    scenario_id = "adhoc.partial-side-effect.v1"

    inspected = gym.inspect(scenario_id)
    raw = gym.probe(scenario_id)
    truth = gym.controller_ground_truth(scenario_id)

    assert "benign_result" not in inspected
    assert raw == {
        "status": "success",
        "committed_steps": ["write"],
        "pending_steps": ["notify"],
    }
    assert truth["benign_result"]["status"] == "committed"
    assert truth["fixture_digest"]
    assert truth["evidence_paths"]


def test_adhoc_function_tool_surface_and_limits() -> None:
    gym = AdhocGym()
    names = {tool.name for tool in gym.tools()}

    assert names == {
        "adhoc_scenarios_select",
        "adhoc_fixture_inspect",
        "adhoc_fixture_probe",
    }
    with pytest.raises(ValueError, match="limit"):
        gym.select(["web.search"], limit=0)
