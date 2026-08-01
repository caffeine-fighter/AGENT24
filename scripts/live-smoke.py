"""Run the opt-in three-run real-provider release smoke for issue #103.

This command is intentionally separate from the canonical CI gate.  It spends
OpenAI quota, so it requires AGENT24_RUN_REAL_OPENAI_SMOKE=1 and a key that
is available only through the normal server settings boundary.  The checked-in
local participant bundle is used as the deterministic target; raw SSE/JSONL
evidence stays in a temporary directory and is never printed or copied to an
artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent24.agent.sandbox_runner import (
    LOCAL_BUNDLE_MISSION,
    LOCAL_BUNDLE_SHA256,
    LOCAL_BUNDLE_SOURCE_PATH,
    LOCAL_BUNDLE_URI,
    LocalSandboxRunner,
)
from agent24.tools import PAYMENT_FIXTURE, protected_replay


class SmokeFailure(RuntimeError):
    """A bounded, non-sensitive release-smoke failure."""


def _load_launcher() -> Any:
    path = Path(__file__).resolve().with_name("demo-local.py")
    spec = importlib.util.spec_from_file_location("agent24_demo_local", path)
    if spec is None or spec.loader is None:
        raise SmokeFailure("launcher_unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_sse(body: str) -> list[dict[str, Any]]:
    """Parse only the JSON data envelopes, preserving their exact values."""

    try:
        return [
            json.loads(line.removeprefix("data: "))
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
    except (json.JSONDecodeError, TypeError) as error:
        raise SmokeFailure("sse_parse_failed") from error


def _payloads(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    values = [
        event.get("payload")
        for event in events
        if event.get("type") == event_type
    ]
    if not all(isinstance(value, dict) for value in values):
        raise SmokeFailure(f"{event_type}_payload_invalid")
    return values  # type: ignore[return-value]


def _one_payload(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    values = _payloads(events, event_type)
    if len(values) != 1:
        raise SmokeFailure(f"{event_type}_count_invalid")
    return values[0]


def _assert_secret_absent(text: str, secret: str) -> None:
    if secret and secret in text:
        raise SmokeFailure("secret_leak")


def _aut_sandbox_projection(repository_root: Path, run_number: int) -> dict[str, Any]:
    """Run the reviewed participant once and retain only safe semantic facts."""

    result = LocalSandboxRunner(repository_root).run(
        mission=LOCAL_BUNDLE_MISSION,
        run_id=f"live-smoke-aut-{run_number}",
        seed=42,
    )
    if not result.succeeded or result.source is None or result.failure is not None:
        raise SmokeFailure("sandbox_failed")
    if result.source.get("source_kind") != "local_bundle":
        raise SmokeFailure("sandbox_source_kind_invalid")
    if result.source.get("source_path") != LOCAL_BUNDLE_SOURCE_PATH:
        raise SmokeFailure("sandbox_source_path_invalid")
    if result.source.get("bundle_sha256") != LOCAL_BUNDLE_SHA256:
        raise SmokeFailure("sandbox_bundle_revision_invalid")
    if result.trace[-1].get("type") != "run_completed":
        raise SmokeFailure("sandbox_terminal_missing")
    if Counter(event.get("type") for event in result.trace).get("gym.tool_call", 0) < 2:
        raise SmokeFailure("sandbox_trace_too_short")
    if [entry.get("tool") for entry in result.ledger] != [
        "payment.charge",
        "payment.charge",
        "calendar.create",
    ]:
        raise SmokeFailure("sandbox_ledger_unexpected")
    if not result.fault_applications:
        raise SmokeFailure("sandbox_fault_missing")

    return {
        "trace_digest": result.trace_digest,
        "trace_event_counts": dict(
            sorted(Counter(event.get("type") for event in result.trace).items())
        ),
        "ledger_tools": [entry.get("tool") for entry in result.ledger],
        "charge_count": sum(entry.get("tool") == "payment.charge" for entry in result.ledger),
        "calendar_event_count": sum(
            entry.get("tool") == "calendar.create" for entry in result.ledger
        ),
        "fault_type": result.fault_applications[0].get("type"),
    }


def _controller_projection(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract stable evidence without comparing model prose or SDK IDs."""

    source = _one_payload(events, "source_descriptor")
    terminal = _one_payload(events, "run_completed")
    final = _one_payload(events, "final_output")
    controller_final = final.get("controller_final")
    if not isinstance(controller_final, dict) or not controller_final.get("evidence_ids"):
        raise SmokeFailure("controller_evidence_refs_missing")
    if final.get("analysis_source") != "openai":
        raise SmokeFailure("live_analysis_not_observed")

    oracle = _one_payload(events, "oracle.report")
    replay = _one_payload(events, "protected_replay")
    finding = _one_payload(events, "finding_report")
    gym_tool_call_count = len(_payloads(events, "gym.tool_call"))
    gym_tool_result_count = len(_payloads(events, "gym.tool_result"))
    target_tool_call_count = len(_payloads(events, "target.tool_call"))
    target_tool_result_count = len(_payloads(events, "target.tool_result"))
    protected = replay.get("protected")
    protected_projection = (
        {
            key: protected.get(key)
            for key in (
                "trace_digest",
                "payment_count",
                "order_count",
                "total_spend_krw",
                "agent_result",
            )
        }
        if isinstance(protected, dict)
        else None
    )
    return {
        "source": {
            "source_kind": source.get("source_kind"),
            "source_path": source.get("source_path"),
            "revision_kind": source.get("revision_kind"),
            "bundle_sha256": source.get("bundle_sha256"),
            "source_ref": source.get("source_ref"),
        },
        "controller_tool_names": [
            payload.get("name")
            for payload in _payloads(events, "tool_call")
        ],
        "gym_tool_call_count": gym_tool_call_count,
        "gym_tool_result_count": gym_tool_result_count,
        "target_tool_call_count": target_tool_call_count,
        "target_tool_result_count": target_tool_result_count,
        "lab_tool_call_count": gym_tool_call_count + target_tool_call_count,
        "lab_tool_result_count": gym_tool_result_count + target_tool_result_count,
        "oracle": {
            "status": oracle.get("status"),
            "violations": oracle.get("violations"),
        },
        "replay": {
            "accepted": replay.get("accepted"),
            "execution_scope": replay.get("execution_scope"),
            "protected": protected_projection,
        },
        "finding_status": finding.get("status"),
        "terminal": {
            key: terminal.get(key)
            for key in (
                "status",
                "mode",
                "source_resolved",
                "diagnostic_completed",
                "openai_analysis_completed",
                "execution_scope",
            )
        },
        "final": {
            "analysis_source": final.get("analysis_source"),
            "evidence_ids": controller_final.get("evidence_ids"),
            "mitigation_id": controller_final.get("mitigation_id"),
        },
    }


def _run_one(
    client: TestClient,
    repository_root: Path,
    artifact_root: Path,
    run_number: int,
    secret: str,
) -> dict[str, Any]:
    health = client.get("/health")
    if health.status_code != 200:
        raise SmokeFailure("health_request_failed")
    health_body = health.text
    _assert_secret_absent(health_body, secret)
    health_data = health.json()
    if health_data.get("mode") != "live" or health_data.get("openai_configured") is not True:
        raise SmokeFailure("live_health_contract_failed")

    launcher = _load_launcher()
    bundle_sha = launcher._example_bundle_files(repository_root)[-1]
    if bundle_sha != LOCAL_BUNDLE_SHA256:
        raise SmokeFailure("bundle_revision_drift")
    target = {
        "repository_url": LOCAL_BUNDLE_URI,
        "requested_ref": bundle_sha,
        "mission": LOCAL_BUNDLE_MISSION,
    }
    accepted = client.post("/api/runs", json={"input": "one input", "target": target})
    if accepted.status_code != 202:
        raise SmokeFailure("run_not_accepted")
    metadata = accepted.json()
    run_id = metadata.get("run_id")
    events_url = metadata.get("events_url")
    if not isinstance(run_id, str) or not isinstance(events_url, str):
        raise SmokeFailure("run_metadata_invalid")
    response = client.get(events_url)
    if response.status_code != 200:
        raise SmokeFailure("sse_request_failed")
    _assert_secret_absent(response.text, secret)
    events = _parse_sse(response.text)
    jsonl_path = artifact_root / f"{run_id}.jsonl"
    if not jsonl_path.is_file():
        raise SmokeFailure("jsonl_evidence_missing")
    jsonl_text = jsonl_path.read_text(encoding="utf-8")
    _assert_secret_absent(jsonl_text, secret)
    if events != _parse_jsonl(jsonl_text):
        raise SmokeFailure("sse_jsonl_mismatch")
    if [event.get("seq") for event in events] != list(range(len(events))):
        raise SmokeFailure("event_sequence_invalid")
    if {event.get("run_id") for event in events} != {run_id}:
        raise SmokeFailure("event_run_id_invalid")
    if not events or events[-1].get("type") != "run_completed":
        raise SmokeFailure("terminal_event_missing")
    if any(event.get("type") == "stage_failed" for event in events):
        raise SmokeFailure("live_success_used_fallback")
    required_types = {
        "source_descriptor",
        "source_snapshot",
        "experiment_plan",
        "oracle.report",
        "protected_replay",
        "finding_report",
        "lab_report",
        "final_output",
    }
    observed_types = {event.get("type") for event in events}
    if not required_types <= observed_types:
        raise SmokeFailure("controller_evidence_incomplete")
    controller_names = [
        payload.get("name") for payload in _payloads(events, "tool_call")
    ]
    if controller_names != [
        "inspect_target",
        "list_experiments",
        "run_sandbox_experiment",
        "inspect_evidence",
        "verify_mitigation",
    ]:
        raise SmokeFailure("controller_tool_sequence_invalid")
    if len(_payloads(events, "tool_result")) != len(controller_names):
        raise SmokeFailure("controller_tool_result_count_invalid")
    terminal = _one_payload(events, "run_completed")
    if not (
        terminal.get("status") == "completed"
        and terminal.get("mode") == "live"
        and terminal.get("source_resolved") is True
        and terminal.get("diagnostic_completed") is True
        and terminal.get("openai_analysis_completed") is True
    ):
        raise SmokeFailure("live_terminal_contract_failed")

    controller = _controller_projection(events)
    source = controller["source"]
    if source != {
        "source_kind": "local_bundle",
        "source_path": LOCAL_BUNDLE_SOURCE_PATH,
        "revision_kind": "bundle_sha256",
        "bundle_sha256": LOCAL_BUNDLE_SHA256,
        "source_ref": f"{LOCAL_BUNDLE_URI}@sha256:{LOCAL_BUNDLE_SHA256}",
    }:
        raise SmokeFailure("controller_source_contract_failed")
    if controller["lab_tool_call_count"] < 2 or controller["lab_tool_result_count"] < 2:
        raise SmokeFailure("controller_lab_trace_too_short")
    if controller["replay"].get("accepted") is not True:
        raise SmokeFailure("controller_replay_rejected")

    aut = _aut_sandbox_projection(repository_root, run_number)
    replay = protected_replay(PAYMENT_FIXTURE, seed=42)
    if not replay.accepted:
        raise SmokeFailure("protected_replay_rejected")
    replay_projection = {
        "accepted": replay.accepted,
        "perturbed_charge_count": replay.perturbed.charge_count,
        "protected_charge_count": replay.protected.charge_count,
        "protected_mission_succeeds": replay.protected_mission_succeeds,
        "benign_control_succeeds": replay.benign_control_succeeds,
        "blanket_block_rejected": replay.blanket_block_rejected,
    }
    return {
        "controller": controller,
        "aut": aut,
        "protected_replay": replay_projection,
    }


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    except (json.JSONDecodeError, TypeError) as error:
        raise SmokeFailure("jsonl_parse_failed") from error


def _digest_projection(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="consecutive runs; must be at least 3",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="per-run controller timeout passed to the local app",
    )
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if args.runs < 3:
        print("LIVE_SMOKE FAIL: runs_must_be_at_least_three")
        return 2
    if args.timeout_seconds <= 0:
        print("LIVE_SMOKE FAIL: timeout_must_be_positive")
        return 2
    if os.getenv("AGENT24_RUN_REAL_OPENAI_SMOKE") != "1":
        print("LIVE_SMOKE FAIL: opt_in_required")
        return 2

    try:
        launcher = _load_launcher()
        settings = launcher.RuntimeSettings()
        secret = (os.getenv("OPENAI_API_KEY") or settings.openai_api_key or "").strip()
        if not secret:
            print("LIVE_SMOKE FAIL: openai_key_missing")
            return 2
        settings.run_timeout_seconds = args.timeout_seconds
        repository_root = Path(__file__).resolve().parents[1]
        projections: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="agent24-live-smoke-") as temporary_root:
            artifact_root = Path(temporary_root)
            app = launcher.build_app(
                repository_root,
                example_agent=True,
                settings=settings,
                artifact_root=artifact_root,
            )
            with TestClient(app) as client:
                for run_number in range(1, args.runs + 1):
                    projections.append(
                        _run_one(
                            client,
                            repository_root,
                            artifact_root,
                            run_number,
                            secret,
                        )
                    )
        stable = len({json.dumps(item, sort_keys=True) for item in projections}) == 1
        if not stable:
            print("LIVE_SMOKE FAIL: semantic_evidence_changed_between_runs")
            return 1
        first = projections[0]
        print(
            json.dumps(
                {
                    "runs": args.runs,
                    "stable": True,
                    "semantic_digest": _digest_projection(first),
                    "provider_observed": True,
                    "source_kind": first["controller"]["source"]["source_kind"],
                    "bundle_revision": f"sha256:{LOCAL_BUNDLE_SHA256}",
                    "controller_tool_steps": len(
                        first["controller"]["controller_tool_names"]
                    ),
                    "aut_sandbox_trace": True,
                    "aut_trace_digest": first["aut"]["trace_digest"],
                    "oracle_observed": True,
                    "protected_replay_accepted": first["protected_replay"]["accepted"],
                    "final_output_observed": True,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except SmokeFailure as error:
        print(f"LIVE_SMOKE FAIL: {error}")
        return 1
    except Exception:
        # Provider and filesystem details must never become CI output.
        print("LIVE_SMOKE FAIL: live_provider_or_runtime_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
