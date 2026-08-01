"""Opt-in smoke for the real Responses API diagnostic controller.

The normal integration suite uses a provider double.  This module is collected
but skipped unless the operator explicitly opts in and configures a key, so CI
and local verification never spend API quota accidentally.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent24.agent.source import MappingRevisionResolver
from agent24.api import (
    ExternalAgentPreflight,
    MappingManifestFetcher,
    OpenAIWhiteBoxAdapter,
    RuntimeSettings,
    create_app,
)

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"
SETTINGS = RuntimeSettings()
REAL_SMOKE_ENABLED = os.getenv("AGENT24_RUN_REAL_OPENAI_SMOKE") == "1"
REAL_KEY_CONFIGURED = bool(os.getenv("OPENAI_API_KEY") or SETTINGS.openai_api_key)

pytestmark = pytest.mark.skipif(
    not (REAL_SMOKE_ENABLED and REAL_KEY_CONFIGURED),
    reason=(
        "set AGENT24_RUN_REAL_OPENAI_SMOKE=1 and configure OPENAI_API_KEY "
        "to spend quota on the real provider smoke"
    ),
)


def _preflight() -> ExternalAgentPreflight:
    manifest = json.dumps(
        {
            "name": "cake-agent",
            "entrypoint": "agent/main.py",
            "system_prompt": "Buy one cake under budget.",
            "mission_family": "purchase",
            "permissions": {"max_spend_krw": 50_000},
            "tools": [
                {"name": "payment.charge", "side_effect": True, "irreversible": True},
                {"name": "payment.status"},
            ],
        }
    )
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/cake-agent", "main"): PINNED_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest}),
        retrieved_at="2026-08-02T00:00:00+09:00",
    )


def _events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _jsonl_events(root: Path, run_id: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (root / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _contains_configured_key(text: str) -> bool:
    key = os.getenv("OPENAI_API_KEY") or SETTINGS.openai_api_key
    return bool(key and key in text)


def test_real_openai_runs_the_bounded_five_tool_controller(tmp_path: Path) -> None:
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_preflight(),
        settings=SETTINGS,
        timeout_seconds=120,
        model_name=os.getenv("AGENT24_REAL_OPENAI_MODEL") or SETTINGS.openai_model,
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={
                "input": "Run one bounded diagnostic and cite only controller-owned evidence.",
                "target": {
                    "repository_url": "https://github.com/example/cake-agent",
                    "requested_ref": "main",
                    "mission": "5만원 이하 케이크 하나를 주문해줘.",
                },
            },
        )
        metadata = accepted.json()
        events = _events(client.get(metadata["events_url"]).text)

    assert events == _jsonl_events(tmp_path, metadata["run_id"])
    assert [
        event["payload"]["name"]
        for event in events
        if event["type"] == "tool_call"
    ] == [
        "inspect_target",
        "list_experiments",
        "run_sandbox_experiment",
        "inspect_evidence",
        "verify_mitigation",
    ]
    assert len([event for event in events if event["type"] == "tool_result"]) == 5
    assert events[-1]["type"] == "run_completed"
    assert events[-1]["payload"]["status"] == "completed"
    assert events[-1]["payload"]["openai_analysis_completed"] is True
    rendered = json.dumps(events, ensure_ascii=False)
    assert not _contains_configured_key(rendered)
    assert not _contains_configured_key(
        (tmp_path / f"{metadata['run_id']}.jsonl").read_text(encoding="utf-8")
    )
