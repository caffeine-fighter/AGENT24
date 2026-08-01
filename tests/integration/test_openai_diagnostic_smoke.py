"""Opt-in three-run smoke for the real Responses API diagnostic controller.

The normal suite uses a provider double. This module spends quota only after an
explicit opt-in and writes Raw Stream artifacts only below pytest's temporary
directory. The target is the exact checked-in local Agent bundle.
"""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agent24.agent.sandbox_runner import LOCAL_BUNDLE_MISSION
from agent24.api import RuntimeSettings

SETTINGS = RuntimeSettings()
RUNS = 3
TOOL_NAMES = [
    "inspect_target",
    "list_experiments",
    "run_sandbox_experiment",
    "inspect_evidence",
    "verify_mitigation",
]
TARGET_TRACE_TYPES = {
    "target.execution.plan",
    "target.execution.started",
    "target.tool_call",
    "target.tool_result",
    "target.execution.completed",
}

pytestmark = pytest.mark.skipif(
    not (
        os.getenv("AGENT24_RUN_REAL_OPENAI_SMOKE") == "1"
        and bool(os.getenv("OPENAI_API_KEY") or SETTINGS.openai_api_key)
    ),
    reason=(
        "set AGENT24_RUN_REAL_OPENAI_SMOKE=1 and configure OPENAI_API_KEY "
        "to spend quota on the real provider smoke"
    ),
)


def _load_launcher() -> Any:
    path = Path(__file__).resolve().parents[2] / "scripts" / "demo-local.py"
    spec = importlib.util.spec_from_file_location("agent24_real_demo_local", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load local demo launcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sse_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _jsonl_events(root: Path, run_id: str) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (root / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _configured_key() -> str:
    return os.getenv("OPENAI_API_KEY") or SETTINGS.openai_api_key or ""


def _assert_secret_free(value: Any) -> None:
    key = _configured_key()
    assert key
    assert key not in json.dumps(value, ensure_ascii=False)


def _run_once(
    client: TestClient,
    *,
    launcher: Any,
    bundle_sha: str,
    artifact_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response = client.post(
        "/api/runs",
        json={
            "input": "Diagnose this exact checked-in Agent with the bounded controller.",
            "target": {
                "repository_url": launcher.EXAMPLE_SOURCE_URL,
                "requested_ref": bundle_sha,
                "mission": LOCAL_BUNDLE_MISSION,
            },
        },
    )
    assert response.status_code == 202
    metadata = response.json()
    events = _sse_events(client.get(metadata["events_url"]).text)
    persisted = _jsonl_events(artifact_root, metadata["run_id"])
    assert events == persisted
    _assert_secret_free(events)
    return metadata, events


def _assert_live_contract(
    metadata: dict[str, Any], events: list[dict[str, Any]], bundle_sha: str
) -> tuple[Any, ...]:
    assert events and {event["run_id"] for event in events} == {metadata["run_id"]}
    tools = [event["payload"]["name"] for event in events if event["type"] == "tool_call"]
    assert tools == TOOL_NAMES
    assert {"run_sandbox_experiment", "inspect_evidence"} <= set(tools)
    assert len([event for event in events if event["type"] == "tool_result"]) == len(TOOL_NAMES)

    source = next(event["payload"] for event in events if event["type"] == "source_descriptor")
    snapshot = next(event["payload"] for event in events if event["type"] == "source_snapshot")
    expected_ref = f"local://agent24/examples/demo-agent-repo@sha256:{bundle_sha}"
    assert source["source_kind"] == "local_bundle"
    assert source["revision_kind"] == "bundle_sha256"
    assert source["source_ref"] == expected_ref
    assert source["bundle_sha256"] == bundle_sha
    assert snapshot["source_ref"] == expected_ref
    assert snapshot["execution_scope"] == "manifest_and_entrypoint"
    assert [item["path"] for item in snapshot["files"]] == [
        ".agent24/manifest.json",
        "src/example_agent.py",
    ]

    target_types = {event["type"] for event in events if event["type"].startswith("target.")}
    assert TARGET_TRACE_TYPES <= target_types
    target_plan = next(
        event["payload"] for event in events if event["type"] == "target.execution.plan"
    )
    assert target_plan["execution_scope"] == "target_sandbox"
    assert target_plan["target_model"] == "deterministic_python"
    assert target_plan["service_boundary"] == "synthetic_local_replacement"
    assert target_plan["external_side_effect"] == "none"
    assert any(
        event["payload"]["tool"] == "payment.charge"
        for event in events
        if event["type"] == "target.tool_call"
    )

    completed = [
        event["payload"] for event in events if event["type"] == "target.execution.completed"
    ]
    vulnerable = next(item for item in completed if item["execution_kind"] == "vulnerable")
    protected = next(item for item in completed if item["execution_kind"] == "protected_replay")
    assert vulnerable["succeeded"] is True and vulnerable["world"]["charges"] == 2
    assert protected["succeeded"] is True and protected["world"]["charges"] == 1

    evidence = next(event["payload"] for event in events if event["type"] == "sandbox.evidence")
    assert evidence["execution_scope"] == "target_sandbox"
    assert evidence["source"]["source_ref"] == expected_ref
    assert evidence["source"]["bundle_sha256"] == bundle_sha
    assert sum(item["tool"] == "payment.charge" for item in evidence["perturbed"]["ledger"]) == 2
    assert sum(item["tool"] == "payment.charge" for item in evidence["protected"]["ledger"]) == 1
    assert len(evidence["replay_digests"]) == 3
    assert len(set(evidence["replay_digests"])) == 1
    assert len(evidence["protected_replay_digests"]) == 3
    assert len(set(evidence["protected_replay_digests"])) == 1
    assert all(evidence["gates"].values())

    oracle = next(event["payload"] for event in events if event["type"] == "oracle.report")
    replay = next(event["payload"] for event in events if event["type"] == "protected_replay")
    final = next(event["payload"] for event in events if event["type"] == "final_output")
    assert oracle["execution_scope"] == "target_sandbox"
    assert oracle["world_source"] == "actual_target_sandbox"
    assert replay["accepted"] is True
    assert replay["evidence_id"] == evidence["evidence_id"]
    assert final["analysis_source"] == "openai"
    assert final["controller_final"]["evidence_ids"] == [evidence["evidence_id"]]

    terminal = events[-1]
    assert terminal["type"] == "run_completed"
    assert terminal["payload"] == {
        "status": "completed",
        "mode": "live",
        "source_resolved": True,
        "diagnostic_completed": True,
        "openai_analysis_completed": True,
        "execution_scope": "target_sandbox",
        "experiments_run": 11,
        "findings": 1,
        "safety_boundary": "TARGET_CODE_IN_SANDBOX",
    }

    files = tuple(
        (item["path"], item["blob_sha"], item["content_sha256"]) for item in snapshot["files"]
    )
    return (
        expected_ref,
        files,
        evidence["evidence_id"],
        evidence["initial_snapshot_hash"],
        evidence["perturbed"]["trace_digest"],
        evidence["protected"]["trace_digest"],
        tuple(evidence["replay_digests"]),
        tuple(evidence["protected_replay_digests"]),
    )


def test_real_openai_runs_the_bounded_local_bundle_controller_three_times() -> None:
    launcher = _load_launcher()
    repository_root = Path(__file__).resolve().parents[2]
    bundle_sha = launcher._example_bundle_files(repository_root)[-1]
    stable: list[tuple[Any, ...]] = []
    run_ids: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agent24-live-smoke-") as temp_dir:
        artifact_root = Path(temp_dir)
        app = launcher.build_app(
            repository_root,
            example_agent=True,
            settings=SETTINGS,
            artifact_root=artifact_root,
            timeout_seconds=120,
            model_name=os.getenv("AGENT24_REAL_OPENAI_MODEL") or SETTINGS.openai_model,
        )
        with TestClient(app) as client:
            health_response = client.get("/health")
            assert health_response.status_code == 200
            health = health_response.json()
            assert health["status"] == "ok"
            assert health["mode"] == "live"
            assert health["openai_configured"] is True
            _assert_secret_free(health)

            for _ in range(RUNS):
                metadata, events = _run_once(
                    client,
                    launcher=launcher,
                    bundle_sha=bundle_sha,
                    artifact_root=artifact_root,
                )
                run_ids.append(metadata["run_id"])
                stable.append(_assert_live_contract(metadata, events, bundle_sha))

    assert len(set(run_ids)) == RUNS
    assert len(set(stable)) == 1
    assert not artifact_root.exists()
