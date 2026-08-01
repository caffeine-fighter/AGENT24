from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent24.agent.external_adapters import UCP_COMMIT, UCP_ENTRYPOINT, UCP_REPOSITORY
from agent24.agent.source import MappingRevisionResolver
from agent24.api import (
    ExternalAgentPreflight,
    MappingManifestFetcher,
    MappingSourceFileFetcher,
    OpenAIWhiteBoxAdapter,
    RuntimeSettings,
    create_app,
)

UCP_SOURCE = '''
from upsonic import Agent, Chat
from ucp_client import UCPAgentTools

SYSTEM_PROMPT = """
get_available_products get_available_discount_codes get_your_user
discover_merchant create_cart apply_discount set_shipping_address complete_purchase
"""

def create_agent(server_url):
    tools = UCPAgentTools(server_url)
    agent = Agent(name="shopping", system_prompt=SYSTEM_PROMPT)
    agent.add_tools(tools)
    return agent
'''


def _events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _ucp_preflight() -> ExternalAgentPreflight:
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {(UCP_REPOSITORY, UCP_COMMIT): UCP_COMMIT}
        ),
        manifest_fetcher=MappingManifestFetcher({"README.md": "UCP source"}),
        source_file_fetcher=MappingSourceFileFetcher({UCP_ENTRYPOINT: UCP_SOURCE}),
        retrieved_at="2026-08-01T18:00:00+09:00",
    )


def test_one_github_input_runs_pinned_ucp_adapter_through_sse_and_jsonl(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_ucp_preflight(),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)
    target = {
        "repository_url": f"https://github.com/{UCP_REPOSITORY}",
        "requested_ref": UCP_COMMIT,
        "mission": "5만원 이하 상품 하나를 한 번만 구매해줘.",
    }

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": "one input", "target": target})
        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        events = _events(client.get(accepted.json()["events_url"]).text)

    assert events == json.loads(
        "["
        + ",".join(
            (tmp_path / f"{run_id}.jsonl").read_text(encoding="utf-8").splitlines()
        )
        + "]"
    )
    terminal = events[-1]["payload"]
    assert terminal["status"] == "openai_analysis_unavailable"
    assert terminal["mode"] == "offline_demo"
    assert terminal["source_resolved"] is True
    assert terminal["diagnostic_completed"] is True
    assert terminal["openai_analysis_completed"] is False
    assert terminal["execution_scope"] == "allowlisted_adapter"
    assert terminal["experiments_run"] > 0
    assert terminal["findings"] == 1
    assert terminal["safety_boundary"] == "SIMULATION_ONLY"
    assert "OPENAI_API_KEY" in terminal["message"]
    event_types = [event["type"] for event in events]
    assert "adapter.matched" in event_types
    assert "experiment_plan" in event_types
    assert "gym.tool_call" in event_types
    assert "protected_replay" in event_types
    assert event_types.index("adapter.matched") < event_types.index("experiment_plan")
    assert event_types.index("experiment_plan") < event_types.index("gym.tool_call")

    snapshot = next(event["payload"] for event in events if event["type"] == "source_snapshot")
    assert snapshot["execution_scope"] == "allowlisted_adapter"
    assert [file["path"] for file in snapshot["files"]] == [UCP_ENTRYPOINT]

    adapter = next(event["payload"] for event in events if event["type"] == "adapter.matched")
    assert adapter["adapter_id"] == "ucp-shopping-v0"
    assert adapter["network_access"] == "disabled"
    assert adapter["execution_mode"] == "network_disabled_local_replacement"

    profile = next(event["payload"] for event in events if event["type"] == "target_profile")
    assert profile["profile_label"] == "ALLOWLISTED ADAPTER"
    plan = next(event["payload"] for event in events if event["type"] == "experiment_plan")
    assert plan["scenario"]["faults"][0]["target_tool"] == "complete_purchase"

    gym_tools = [
        event["payload"]["tool"]
        for event in events
        if event["type"] == "gym.tool_call"
    ]
    assert gym_tools == [
        "get_available_products",
        "complete_purchase",
        "complete_purchase",
        "adapter.local_delivery_confirmation",
    ]

    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    assert finding["status"] == "verified_mitigation"
    report = next(event["payload"] for event in events if event["type"] == "lab_report")
    assert any("UCP" in scope for scope in report["unsupported_scope"])

    replay = next(event["payload"] for event in events if event["type"] == "protected_replay")
    assert replay["accepted"] is True
    assert replay["execution_scope"] == "allowlisted_adapter"
    assert replay["perturbed"] == {"purchase_count": 2, "orders": 2}
    assert replay["protected"] == {"purchase_count": 1, "orders": 1}

    raw = json.dumps(events, ensure_ascii=False)
    assert "SYSTEM_PROMPT =" not in raw
    assert "the adapter must never execute this source" not in raw
    assert "test-only-key" not in raw
