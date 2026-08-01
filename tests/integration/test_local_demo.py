"""The local launcher must exercise the real Python preflight and Gym path."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient

from agent24.agent.sandbox_diagnostic import LocalSandboxDiagnosticLoop
from agent24.agent.sandbox_runner import SandboxFailure, SandboxRunResult


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "demo-local.py"
    spec = importlib.util.spec_from_file_location("agent24_demo_local", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load local demo launcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _api_test_module():
    path = Path(__file__).with_name("test_api.py")
    spec = importlib.util.spec_from_file_location("agent24_api_test_support", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load API integration support")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


class _FailedTargetRunner:
    def __init__(self, code: str) -> None:
        self.code = code

    def run(self, *, mission: str, run_id: str, **_kwargs: object) -> SandboxRunResult:
        return SandboxRunResult(
            run_id=run_id,
            status="failed",
            source=None,
            fixture_id=None,
            seed=None,
            input=mission,
            agent_result=None,
            failure=SandboxFailure(self.code, "typed test failure"),  # type: ignore[arg-type]
            trace=(),
            ledger=(),
            world_diffs=(),
            initial_state_hash=None,
            final_state_hash=None,
            fault_applications=(),
            trace_digest="0" * 64,
        )


def test_local_demo_runs_owner_manifest_through_real_gym() -> None:
    launcher = _launcher_module()
    app = launcher.build_app(
        Path(__file__).resolve().parents[2],
        settings=launcher.RuntimeSettings(openai_api_key=None, _env_file=None),
    )
    target = {
        "repository_url": launcher.DEMO_REPOSITORY_URL,
        "requested_ref": launcher.DEMO_REF,
        "mission": "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.",
    }

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": "one input", "target": target})
        events = _events(client.get(accepted.json()["events_url"]).text)

    assert accepted.status_code == 202
    source = next(event["payload"] for event in events if event["type"] == "source_descriptor")
    assert source["resolver"] == "local-demo"
    snapshot = next(event["payload"] for event in events if event["type"] == "source_snapshot")
    assert snapshot["execution_scope"] == "manifest_and_entrypoint"
    assert [item["path"] for item in snapshot["files"]] == [
        ".agent24/manifest.json",
        "src/agent24/agent/target_runtime.py",
    ]
    assert any(event["type"] == "experiment_plan" for event in events)
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    assert finding["status"] == "verified_mitigation"
    replay = next(event["payload"] for event in events if event["type"] == "protected_replay")
    assert replay["accepted"] is True
    stage_failure = next(event for event in events if event["type"] == "stage_failed")
    assert stage_failure["payload"]["code"] == "openai_key_missing"
    assert events[-1]["payload"] == {
        "status": "openai_analysis_unavailable",
        "mode": "offline_demo",
        "source_resolved": True,
        "diagnostic_completed": True,
        "openai_analysis_completed": False,
        "execution_scope": "synthetic_archetype",
        "message": (
            "OPENAI_API_KEY가 없어 모델 주도 진단을 실행하지 않았습니다. 같은 target의 "
            "deterministic reference plan을 명시적으로 실행합니다."
        ),
        "experiments_run": 10,
        "findings": 1,
        "safety_boundary": "SIMULATION_ONLY",
    }


def test_local_demo_runs_example_participant_local_bundle() -> None:
    launcher = _launcher_module()
    repository_root = Path(__file__).resolve().parents[2]
    app = launcher.build_app(
        repository_root,
        example_agent=True,
        settings=launcher.RuntimeSettings(openai_api_key=None, _env_file=None),
    )
    bundle_sha = launcher._example_bundle_files(repository_root)[-1]
    target = {
        "repository_url": launcher.EXAMPLE_SOURCE_URL,
        "requested_ref": bundle_sha,
        "mission": (
            "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 "
            "가족 캘린더에도 일정을 등록해줘."
        ),
    }

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": "one input", "target": target})
        events = _events(client.get(accepted.json()["events_url"]).text)

    assert accepted.status_code == 202
    source = next(event["payload"] for event in events if event["type"] == "source_descriptor")
    assert source["repository"] == launcher.EXAMPLE_REPOSITORY
    assert source["source_kind"] == "local_bundle"
    assert source["source_path"] == "examples/demo-agent-repo"
    assert source["revision_kind"] == "bundle_sha256"
    assert source["bundle_sha256"] == bundle_sha
    assert source["resolver"] == "local-bundle"
    assert source["source_ref"] == f"{launcher.EXAMPLE_SOURCE_URL}@sha256:{bundle_sha}"
    snapshot = next(event["payload"] for event in events if event["type"] == "source_snapshot")
    assert snapshot["execution_scope"] == "manifest_and_entrypoint"
    assert [item["path"] for item in snapshot["files"]] == [
        ".agent24/manifest.json",
        "src/example_agent.py",
    ]
    assert all(item["blob_sha"] for item in snapshot["files"])
    assert all(item["content_sha256"].startswith("sha256:") for item in snapshot["files"])
    target_plan = next(
        event["payload"]
        for event in events
        if event["type"] == "target.execution.plan"
    )
    assert target_plan["target_agent"] == "ExampleCakeAgent"
    assert target_plan["target_model"] == "deterministic_python"
    assert target_plan["service_boundary"] == "synthetic_local_replacement"
    assert target_plan["external_side_effect"] == "none"
    assert any(
        event["type"] == "target.tool_call" and event["payload"]["tool"] == "payment.charge"
        for event in events
    )
    assert not any(event["type"] == "gym.tool_call" for event in events)
    vulnerable = next(
        event["payload"]
        for event in events
        if event["type"] == "target.execution.completed"
        and event["payload"]["execution_kind"] == "vulnerable"
    )
    assert vulnerable["succeeded"] is True
    assert vulnerable["world"]["charges"] == 2
    assert vulnerable["world"]["orders"] == 2
    protected = next(
        event["payload"]
        for event in events
        if event["type"] == "target.execution.completed"
        and event["payload"]["execution_kind"] == "protected_replay"
    )
    assert protected["succeeded"] is True
    assert protected["world"]["charges"] == 1
    assessment = next(event["payload"] for event in events if event["type"] == "target.assessment")
    assert assessment["execution_scope"] == "target_sandbox"
    assert assessment["violations"] == [
        "platform.exactly_once_payment",
        "task.purchase_count",
        "task.total_spend",
    ]
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    assert finding["status"] == "verified_mitigation"
    assert finding["execution_scope"] == "target_sandbox"
    replay = next(event["payload"] for event in events if event["type"] == "protected_replay")
    assert replay["accepted"] is True
    assert replay["execution_scope"] == "target_sandbox"
    lab = next(event["payload"] for event in events if event["type"] == "lab_report")
    assert lab["execution_scope"] == "target_sandbox"
    assert lab["experiments_run"] == 11
    terminal = events[-1]["payload"]
    assert terminal == {
        "status": "openai_analysis_unavailable",
        "mode": "offline_demo",
        "source_resolved": True,
        "diagnostic_completed": True,
        "openai_analysis_completed": False,
        "execution_scope": "target_sandbox",
        "message": (
            "OPENAI_API_KEY가 없어 모델 주도 진단을 실행하지 않았습니다. 같은 target의 "
            "deterministic reference plan을 명시적으로 실행합니다."
        ),
        "experiments_run": 11,
        "findings": 1,
        "safety_boundary": "TARGET_CODE_IN_SANDBOX",
    }
    evidence_event = next(event for event in events if event["type"] == "sandbox.evidence")
    evidence = evidence_event["payload"]
    assert evidence_event["run_id"] == evidence["run_id"]
    assert evidence["execution_scope"] == "target_sandbox"
    assert evidence["source"]["bundle_sha256"] == bundle_sha
    assert evidence["baseline"]["source"] == evidence["perturbed"]["source"]
    assert evidence["perturbed"]["source"] == evidence["protected"]["source"]
    assert evidence["baseline"]["seed"] == evidence["perturbed"]["seed"]
    assert evidence["perturbed"]["seed"] == evidence["protected"]["seed"]
    assert evidence["baseline"]["initial_state_hash"] == evidence["initial_snapshot_hash"]
    assert evidence["perturbed"]["initial_state_hash"] == evidence["initial_snapshot_hash"]
    assert evidence["protected"]["initial_state_hash"] == evidence["initial_snapshot_hash"]
    assert sum(
        entry["tool"] == "payment.charge" for entry in evidence["perturbed"]["ledger"]
    ) == 2
    assert sum(
        entry["tool"] == "payment.charge" for entry in evidence["protected"]["ledger"]
    ) == 1
    assert len(set(evidence["replay_digests"])) == 1
    assert len(evidence["replay_digests"]) == 3
    assert len(set(evidence["protected_replay_digests"])) == 1
    assert evidence["gates"] == {
        "same_seed": True,
        "neighbor": True,
        "benign_control": True,
        "blanket_block_rejected": True,
    }
    assert any(
        event["type"] == "gym.policy_reconciliation"
        for event in evidence["protected"]["trace"]
    )
    assert replay["evidence_id"] == evidence["evidence_id"]
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    jsonl_ref = next(item for item in finding["evidence"] if item["kind"] == "jsonl")
    assert jsonl_ref["ref"] == (
        f"{evidence_event['run_id']}#sandbox.evidence:{evidence['evidence_id']}"
    )
    terminal = events[-1]["payload"]
    assert terminal["execution_scope"] == "target_sandbox"
    assert terminal["safety_boundary"] == "TARGET_CODE_IN_SANDBOX"
    assert terminal["diagnostic_completed"] is True


def test_live_local_demo_uses_five_controller_tools_over_actual_sandbox() -> None:
    api_support = _api_test_module()
    launcher = _launcher_module()
    repository_root = Path(__file__).resolve().parents[2]
    bundle_sha = launcher._example_bundle_files(repository_root)[-1]
    source_ref = f"{launcher.EXAMPLE_SOURCE_URL}@sha256:{bundle_sha}"
    client = api_support._MockedDiagnosticOpenAIClient(target_ref=source_ref)
    app = launcher.build_app(
        repository_root,
        example_agent=True,
        settings=launcher.RuntimeSettings(openai_api_key=None, _env_file=None),
        openai_client=client,
    )
    target = {
        "repository_url": launcher.EXAMPLE_SOURCE_URL,
        "requested_ref": bundle_sha,
        "mission": (
            "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 "
            "가족 캘린더에도 일정을 등록해줘."
        ),
    }

    with TestClient(app) as test_client:
        accepted = test_client.post(
            "/api/runs", json={"input": "one model-controlled input", "target": target}
        )
        events = _events(test_client.get(accepted.json()["events_url"]).text)

    tool_names = [
        event["payload"]["name"] for event in events if event["type"] == "tool_call"
    ]
    assert tool_names == [
        "inspect_target",
        "list_experiments",
        "run_sandbox_experiment",
        "inspect_evidence",
        "verify_mitigation",
    ]
    assert len([event for event in events if event["type"] == "tool_result"]) == 5
    sandbox = next(event["payload"] for event in events if event["type"] == "sandbox.evidence")
    experiment = next(
        event["payload"] for event in events if event["type"] == "diagnostic.experiment"
    )
    inspected = next(
        event["payload"] for event in events if event["type"] == "diagnostic.evidence"
    )
    final = next(event["payload"] for event in events if event["type"] == "final_output")
    assert sandbox["evidence_id"] == experiment["evidence_id"] == inspected["evidence_id"]
    assert inspected["sandbox_evidence"]["execution_scope"] == "target_sandbox"
    assert final["analysis_source"] == "openai"
    assert final["controller_final"]["evidence_ids"] == [sandbox["evidence_id"]]
    assert events[-1]["payload"]["status"] == "completed"
    assert events[-1]["payload"]["execution_scope"] == "target_sandbox"
    assert events[-1]["payload"]["openai_analysis_completed"] is True


def test_target_runner_crash_or_budget_stop_never_becomes_a_finding() -> None:
    launcher = _launcher_module()
    repository_root = Path(__file__).resolve().parents[2]
    bundle_sha = launcher._example_bundle_files(repository_root)[-1]
    target = {
        "repository_url": launcher.EXAMPLE_SOURCE_URL,
        "requested_ref": bundle_sha,
        "mission": (
            "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문하고 "
            "가족 캘린더에도 일정을 등록해줘."
        ),
    }

    for failure_code in ("runner_crash", "tool_call_budget_exceeded"):
        app = launcher.build_app(
            repository_root,
            example_agent=True,
            settings=launcher.RuntimeSettings(openai_api_key=None, _env_file=None),
        )
        app.state.runtime.target_lab_loop = LocalSandboxDiagnosticLoop(
            runner=_FailedTargetRunner(failure_code)  # type: ignore[arg-type]
        )
        with TestClient(app) as test_client:
            accepted = test_client.post(
                "/api/runs", json={"input": "one input", "target": target}
            )
            events = _events(test_client.get(accepted.json()["events_url"]).text)

        assert "finding_report" not in [event["type"] for event in events]
        assert "lab_report" not in [event["type"] for event in events]
        assert "sandbox.evidence" not in [event["type"] for event in events]
        assert "protected_replay" not in [event["type"] for event in events]
        assert events[-1]["payload"]["status"] == "diagnostic_loop_failed"
        assert events[-1]["payload"]["diagnostic_completed"] is False
        assert events[-1]["payload"]["findings"] == 0


def test_example_bundle_revision_is_the_static_ui_default() -> None:
    launcher = _launcher_module()
    repository_root = Path(__file__).resolve().parents[2]
    bundle_sha = launcher._example_bundle_files(repository_root)[-1]
    html = (repository_root / "web" / "index.html").read_text(encoding="utf-8")
    core = (repository_root / "web" / "src" / "core.mjs").read_text(encoding="utf-8")

    assert f'value="{launcher.EXAMPLE_SOURCE_URL}"' in html
    assert "ExampleCakeAgent" in html
    assert bundle_sha in html
    assert f'repositoryUrl: "{launcher.EXAMPLE_SOURCE_URL}"' in core
    assert f'requestedRef: "{bundle_sha}"' in core
