"""The local launcher must exercise the real Python preflight and Gym path."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from fastapi.testclient import TestClient


def _launcher_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "demo-local.py"
    spec = importlib.util.spec_from_file_location("agent24_demo_local", path)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load local demo launcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_local_demo_runs_owner_manifest_through_real_gym() -> None:
    launcher = _launcher_module()
    app = launcher.build_app(Path(__file__).resolve().parents[2])
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
        "src/agent24/api/runtime.py",
    ]
    assert any(event["type"] == "experiment_plan" for event in events)
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    assert finding["status"] == "verified_mitigation"
    replay = next(event["payload"] for event in events if event["type"] == "protected_replay")
    assert replay["accepted"] is True
    assert events[-1]["payload"] == {"status": "offline_demo", "mode": "offline_demo"}


def test_local_demo_runs_example_participant_repo_fixture() -> None:
    launcher = _launcher_module()
    repository_root = Path(__file__).resolve().parents[2]
    app = launcher.build_app(repository_root, example_agent=True)
    target = {
        "repository_url": launcher.EXAMPLE_REPOSITORY_URL,
        "requested_ref": launcher.EXAMPLE_REF,
        "mission": "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.",
    }

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": "one input", "target": target})
        events = _events(client.get(accepted.json()["events_url"]).text)

    assert accepted.status_code == 202
    source = next(event["payload"] for event in events if event["type"] == "source_descriptor")
    assert source["repository"] == launcher.EXAMPLE_REPOSITORY
    assert source["resolver"] == "local-example-fixture"
    snapshot = next(event["payload"] for event in events if event["type"] == "source_snapshot")
    assert snapshot["execution_scope"] == "manifest_and_entrypoint"
    assert [item["path"] for item in snapshot["files"]] == [
        ".agent24/manifest.json",
        "src/example_agent.py",
    ]
    assert any(
        event["type"] == "gym.tool_call" and event["payload"]["tool"] == "payment.charge"
        for event in events
    )
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    assert finding["status"] == "verified_mitigation"
    replay = next(event["payload"] for event in events if event["type"] == "protected_replay")
    assert replay["accepted"] is True
