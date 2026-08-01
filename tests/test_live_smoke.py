"""Contract tests for the opt-in issue #103 release-smoke helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "live-smoke.py"
    spec = importlib.util.spec_from_file_location("agent24_live_smoke", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sse_parser_keeps_only_json_data_envelopes() -> None:
    smoke = _module()
    assert smoke._parse_sse(
        'event: ignored\n\ndata: {"seq":0,"type":"run_started"}\n\n'
        'data: {"seq":1,"type":"run_completed"}\n\n'
    ) == [
        {"seq": 0, "type": "run_started"},
        {"seq": 1, "type": "run_completed"},
    ]


def test_controller_projection_ignores_model_prose() -> None:
    smoke = _module()
    common = [
        {"type": "source_descriptor", "payload": {
            "source_kind": "local_bundle",
            "source_path": "examples/demo-agent-repo",
            "revision_kind": "bundle_sha256",
            "bundle_sha256": "a" * 64,
            "source_ref": "local://agent24/examples/demo-agent-repo@sha256:" + "a" * 64,
        }},
        {"type": "source_snapshot", "payload": {}},
        {"type": "experiment_plan", "payload": {}},
        {"type": "tool_call", "payload": {"name": "inspect_target", "call_id": "call-a"}},
        {"type": "tool_call", "payload": {"name": "list_experiments", "call_id": "call-b"}},
        {"type": "gym.tool_call", "payload": {"tool": "payment.charge"}},
        {"type": "gym.tool_call", "payload": {"tool": "payment.status"}},
        {"type": "gym.tool_result", "payload": {"status": "ok"}},
        {"type": "gym.tool_result", "payload": {"status": "ok"}},
        {"type": "oracle.report", "payload": {
            "status": "observed",
            "violations": ["platform.exactly_once_payment"],
        }},
        {"type": "protected_replay", "payload": {
            "accepted": True,
            "execution_scope": "synthetic_archetype",
            "protected": {"purchase_count": 1, "orders": 1},
        }},
        {"type": "finding_report", "payload": {"status": "verified_mitigation"}},
        {"type": "lab_report", "payload": {}},
        {"type": "final_output", "payload": {
            "text": "first model wording",
            "analysis_source": "openai",
            "controller_final": {
                "evidence_ids": ["evidence-1"],
                "mitigation_id": "patch-1",
            },
        }},
        {"type": "run_completed", "payload": {
            "status": "completed",
            "mode": "live",
            "source_resolved": True,
            "diagnostic_completed": True,
            "openai_analysis_completed": True,
            "execution_scope": "synthetic_archetype",
        }},
    ]
    changed = [dict(event) for event in common]
    changed[-2] = {
        "type": "final_output",
        "payload": {
            "text": "different provider prose",
            "analysis_source": "openai",
            "controller_final": {
                "evidence_ids": ["evidence-1"],
                "mitigation_id": "patch-1",
            },
        },
    }

    assert smoke._controller_projection(common) == smoke._controller_projection(changed)


def test_controller_projection_accepts_target_sandbox_tool_events() -> None:
    smoke = _module()
    events = [
        {"type": "source_descriptor", "payload": {
            "source_kind": "local_bundle",
            "source_path": "examples/demo-agent-repo",
            "revision_kind": "bundle_sha256",
            "bundle_sha256": "a" * 64,
            "source_ref": "local://agent24/examples/demo-agent-repo@sha256:" + "a" * 64,
        }},
        {"type": "run_completed", "payload": {
            "status": "completed",
            "mode": "live",
            "source_resolved": True,
            "diagnostic_completed": True,
            "openai_analysis_completed": True,
            "execution_scope": "target_sandbox",
        }},
        {"type": "target.tool_call", "payload": {"tool": "payment.charge"}},
        {"type": "target.tool_result", "payload": {"status": "ok"}},
        {"type": "oracle.report", "payload": {"status": "observed", "violations": []}},
        {"type": "protected_replay", "payload": {
            "accepted": True,
            "execution_scope": "target_sandbox",
            "protected": {"purchase_count": 1},
        }},
        {"type": "finding_report", "payload": {"status": "verified_mitigation"}},
        {"type": "final_output", "payload": {
            "analysis_source": "openai",
            "controller_final": {"evidence_ids": ["evidence-1"], "mitigation_id": "patch-1"},
        }},
    ]

    projection = smoke._controller_projection(events)
    assert projection["gym_tool_call_count"] == 0
    assert projection["target_tool_call_count"] == 1
    assert projection["lab_tool_call_count"] == 1
    changed_run_id = [dict(event) for event in events]
    changed_run_id[5] = {
        **changed_run_id[5],
        "payload": {
            **changed_run_id[5]["payload"],
            "protected": {
                **changed_run_id[5]["payload"]["protected"],
                "target_run_id": "different-run-id",
            },
        },
    }
    assert smoke._controller_projection(changed_run_id) == projection
