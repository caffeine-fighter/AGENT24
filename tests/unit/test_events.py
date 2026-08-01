from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from agent24.agent import AgentCard, ExperimentPlan, LabReport, Mission, StopDecision
from agent24.agent.profile import BehaviorProfile
from agent24.events import JsonlEventLog, RunChannel


def test_lab_report_model_is_preserved_in_raw_event_payload(tmp_path: Path) -> None:
    report = LabReport(
        agent=AgentCard(name="fixture-agent", system_prompt="test only", tools=[]),
        mission=Mission(text="buy one cake"),
        capabilities=[],
        invariants=[],
        experiments_run=0,
        cost_units_used=0,
        findings=[],
        termination=StopDecision(
            stop=True,
            reason="insufficient_evidence",
            detail="No supported experiment was available.",
        ),
        unsupported_scope=["real payment provider"],
        no_failure_statement="No failure observed; this is not proof of safety.",
    )
    channel = RunChannel(run_id="report-run", event_log=JsonlEventLog(tmp_path))

    event = channel.publish("lab_report", report)

    assert event["type"] == "lab_report"
    assert event["payload"] == report.model_dump(mode="json", exclude_none=False)
    assert channel.events == [event]
    assert "not proof of safety" in event["payload"]["no_failure_statement"]


def test_web_cake_fixtures_match_frozen_python_contracts() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for the web contract gate"
    repository_root = Path(__file__).resolve().parents[2]
    script = (
        "import { createCakeBehaviorProfile, createCakeExperimentPlan, "
        "createCakeLabReport } from './web/src/core.mjs'; "
        "console.log(JSON.stringify({profile:createCakeBehaviorProfile(),"
        "plan:createCakeExperimentPlan(),report:createCakeLabReport()}));"
    )

    completed = subprocess.run(  # noqa: S603 - fixed executable and script
        [node, "--input-type=module", "--eval", script],
        cwd=repository_root,
        check=True,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )

    payload = json.loads(completed.stdout)
    profile = BehaviorProfile.model_validate(payload["profile"])
    plan = ExperimentPlan.model_validate(payload["plan"])
    report = LabReport.model_validate(payload["report"])
    assert profile.agent_name == "cake-buyer"
    assert profile.idempotency_usage.value == "absent"
    assert profile.untrusted_input_handling.value == "unknown"
    assert plan.scenario.faults[0].fault == "commit_then_timeout"
    assert plan.scenario.faults[0].target_tool == "payment.charge"
    assert plan.scenario.max_turns == 8
    assert plan.single_variable is True
    assert report.agent.name == "cake-buyer"
    assert report.findings[0].verified is not None
    assert report.findings[0].verified.accepted is True
    assert report.termination.reason == "coverage_complete"
