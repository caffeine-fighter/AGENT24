from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent24.agent.diagnostic_controller import (
    DIAGNOSTIC_TOOL_NAMES,
    DiagnosticControllerError,
    DiagnosticToolController,
)
from agent24.agent.loop import DeterministicLabLoop
from agent24.agent.models import Mission, MissionFamily, ToolSpec
from agent24.agent.planner import select_p0_experiment
from agent24.agent.profile import AgentManifest, build_behavior_profile
from agent24.events import JsonlEventLog, RunChannel

SOURCE_REF = "example/cake-agent@0123456789abcdef0123456789abcdef01234567"
RATIONALE = {
    "decision_summary": "Use only controller-owned evidence for this bounded step.",
    "expected_evidence": ["A typed controller artifact with a stable evidence id."],
    "budget": 64,
    "fallback": "Stop or use the same-target deterministic reference policy.",
}


def _preflight():
    mission = Mission(
        text="5만원 이하 케이크 하나를 주문해줘.",
        family=MissionFamily.PURCHASE,
        constraints={"max_spend_krw": 50_000},
    )
    manifest = AgentManifest(
        name="cake-agent",
        source_ref=SOURCE_REF,
        system_prompt="Buy one cake under budget.",
        mission_family=MissionFamily.PURCHASE,
        permissions={"max_spend_krw": 50_000},
        tools=[
            ToolSpec(name="payment.charge", side_effect=True, irreversible=True),
            ToolSpec(name="payment.status"),
        ],
    )
    profile = build_behavior_profile(manifest, baseline=None)
    decision = select_p0_experiment(profile, mission)
    assert not hasattr(decision, "stop") or not decision.stop
    return (
        SimpleNamespace(
            source=SimpleNamespace(source_ref=SOURCE_REF),
            mission=mission,
            profile=profile,
            decision=decision,
            adapter_contract=None,
        ),
        manifest,
    )


def _controller(tmp_path, *, experiment=None) -> DiagnosticToolController:
    preflight, manifest = _preflight()
    loop = DeterministicLabLoop()

    async def run_experiment(plan):
        if experiment is not None:
            return await experiment(plan)
        return await loop.run(
            manifest=manifest,
            profile=preflight.profile,
            mission=preflight.mission,
            plan=plan,
        )

    return DiagnosticToolController(
        preflight=preflight,
        execution_scope="synthetic_archetype",
        channel=RunChannel(
            run_id="diagnostic-controller-test",
            event_log=JsonlEventLog(tmp_path),
        ),
        run_experiment=run_experiment,
    )


def test_five_function_tools_have_exact_strict_schemas(tmp_path) -> None:
    controller = _controller(tmp_path)

    tools = controller.tools()

    assert tuple(tool.name for tool in tools) == DIAGNOSTIC_TOOL_NAMES
    for tool in tools:
        schema = tool.params_json_schema
        assert tool.strict_json_schema is True
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.asyncio
async def test_happy_path_requires_all_tools_and_controller_owned_ids(tmp_path) -> None:
    controller = _controller(tmp_path)

    inspected = json.loads(
        await controller._inspect_target(target_ref=SOURCE_REF, **RATIONALE)
    )
    listed = json.loads(await controller._list_experiments(**RATIONALE))
    candidate = listed["candidates"][0]
    executed = json.loads(
        await controller._run_sandbox_experiment(
            operator_id=candidate["operator_id"],
            fault_id=candidate["fault_id"],
            **RATIONALE,
        )
    )
    evidence_id = executed["evidence_id"]
    evidence = json.loads(
        await controller._inspect_evidence(evidence_id=evidence_id, **RATIONALE)
    )
    assert controller.result is not None
    assert controller.result.patch is not None
    mitigation_id = controller.result.patch.patch_id
    verified = json.loads(
        await controller._verify_mitigation(
            evidence_id=evidence_id,
            mitigation_id=mitigation_id,
            **RATIONALE,
        )
    )
    with pytest.raises(DiagnosticControllerError, match="cite only"):
        controller.validate_final(
            {
                "answer": "The bounded experiment observed a duplicate payment.",
                "decision_summary": "Added an unsupported evidence reference.",
                "evidence_ids": [evidence_id, "invented-evidence"],
                "mitigation_id": mitigation_id,
            }
        )
    final = controller.validate_final(
        {
            "answer": "The bounded experiment observed and mitigated a duplicate payment.",
            "decision_summary": "Selected the highest-value supported payment fault.",
            "evidence_ids": [evidence_id],
            "mitigation_id": mitigation_id,
        }
    )

    assert inspected["status"] == "accepted"
    assert evidence["citations"]
    assert verified["accepted"] is True
    assert final.evidence_ids == [evidence_id]
    assert controller.state.accepted_tool_calls == 5
    assert controller.state.phase == "mitigation_verified"
    event_types = [event["type"] for event in controller.channel.events]
    assert event_types.count("diagnostic.rationale") == 5
    assert "planner.reference" in event_types
    assert "planner.comparison" in event_types
    assert "diagnostic.evidence" in event_types
    assert "diagnostic.verification" in event_types


@pytest.mark.asyncio
async def test_out_of_order_tool_permanently_blocks_final(tmp_path) -> None:
    controller = _controller(tmp_path)

    rejected = json.loads(await controller._list_experiments(**RATIONALE))

    assert rejected == {"status": "rejected", "code": "invalid_tool_sequence"}
    with pytest.raises(DiagnosticControllerError, match="not publishable"):
        controller.validate_final(
            {
                "answer": "unsupported",
                "decision_summary": "unsupported",
                "evidence_ids": ["made-up"],
                "mitigation_id": "made-up",
            }
        )


@pytest.mark.asyncio
async def test_arbitrary_operator_and_fault_are_rejected_before_execution(tmp_path) -> None:
    calls = 0

    async def must_not_run(_plan):
        nonlocal calls
        calls += 1
        raise AssertionError("unallowlisted experiment reached the gym")

    controller = _controller(tmp_path, experiment=must_not_run)
    await controller._inspect_target(target_ref=SOURCE_REF, **RATIONALE)
    await controller._list_experiments(**RATIONALE)

    rejected = json.loads(
        await controller._run_sandbox_experiment(
            operator_id="arbitrary.operator",
            fault_id="arbitrary_fault",
            **RATIONALE,
        )
    )

    assert rejected["code"] == "operator_not_allowlisted"
    assert calls == 0


@pytest.mark.asyncio
async def test_budget_and_evidence_ids_cannot_be_bypassed(tmp_path) -> None:
    controller = _controller(tmp_path)
    await controller._inspect_target(target_ref=SOURCE_REF, **RATIONALE)
    listed = json.loads(await controller._list_experiments(**RATIONALE))
    candidate = listed["candidates"][0]

    rejected = json.loads(
        await controller._run_sandbox_experiment(
            operator_id=candidate["operator_id"],
            fault_id=candidate["fault_id"],
            **{**RATIONALE, "budget": 1},
        )
    )

    assert rejected["code"] == "budget_exceeded"
    assert controller.result is None
