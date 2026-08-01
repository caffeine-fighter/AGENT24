from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from openai.types.responses import (
    Response,
    ResponseCompletedEvent,
    ResponseFunctionToolCall,
    ResponseOutputItemDoneEvent,
    ResponseOutputMessage,
    ResponseOutputText,
)

from agent24.agent import (
    PAPER_PLAYGROUND_SHA,
    MappingEvidenceMetadataFetcher,
    ParticipantStaticProfiler,
    metadata_fixture_for,
)
from agent24.agent.source import MappingRevisionResolver
from agent24.api import (
    ExternalAgentPreflight,
    MappingManifestFetcher,
    OpenAIWhiteBoxAdapter,
    RuntimeSettings,
    create_app,
)

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"


def _external_preflight(*, supported: bool = True) -> ExternalAgentPreflight:
    tools = (
        [
            {"name": "payment.charge", "side_effect": True, "irreversible": True},
            {"name": "payment.status"},
        ]
        if supported
        else [{"name": "vendor.transfer", "side_effect": True, "irreversible": True}]
    )
    manifest = json.dumps(
        {
            "name": "cake-agent",
            "entrypoint": "agent/main.py",
            "system_prompt": "Buy one cake under budget.",
            "mission_family": "purchase",
            "permissions": {"max_spend_krw": 50_000},
            "tools": tools,
        }
    )
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/cake-agent", "main"): PINNED_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )


def _participant_preflight() -> ExternalAgentPreflight:
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("midwestchekhov/agent24", "main"): PAPER_PLAYGROUND_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({}),
        static_profiler=ParticipantStaticProfiler(
            evidence_fetcher=MappingEvidenceMetadataFetcher(metadata_fixture_for())
        ),
        retrieved_at="2026-08-01T20:30:00+09:00",
    )


def _target_payload(*, repository_url: str = "https://github.com/example/cake-agent"):
    return {
        "repository_url": repository_url,
        "requested_ref": "main",
        "mission": "5만원 이하 케이크 하나를 주문해줘.",
    }


def _response(*, response_id: str, output: list[Any]) -> Response:
    # ``model_construct`` lets the fixture stay focused on the fields consumed
    # by the official Agents SDK runner, without inventing provider metadata.
    return Response.model_construct(
        id=response_id,
        created_at=0.0,
        model="gpt-4.1-mini",
        object="response",
        output=output,
        status="completed",
        usage=None,
    )


class _MockedOpenAIClient:
    """Small Responses API client double used through ``OpenAIProvider``."""

    last: _MockedOpenAIClient | None = None

    def __init__(self, **kwargs: Any) -> None:
        self.init_kwargs = kwargs
        self.requests: list[dict[str, Any]] = []
        self.responses = self
        type(self).last = self

    async def create(self, **kwargs: Any) -> AsyncIterator[Any]:
        self.requests.append(kwargs)
        input_items = kwargs.get("input", [])
        has_tool_output = any(
            (item.get("type") if isinstance(item, dict) else getattr(item, "type", None))
            == "function_call_output"
            for item in input_items
        ) if isinstance(input_items, list) else False

        if not has_tool_output:
            function_call = ResponseFunctionToolCall(
                id="fc_mock_1",
                call_id="call_mock_1",
                name="inspect_synthetic_gym",
                arguments='{"query":"loop"}',
                type="function_call",
                status="completed",
            )
            response = _response(response_id="resp_mock_1", output=[function_call])
            events = [
                ResponseOutputItemDoneEvent(
                    item=function_call,
                    output_index=0,
                    sequence_number=1,
                    type="response.output_item.done",
                ),
                ResponseCompletedEvent(
                    response=response,
                    sequence_number=2,
                    type="response.completed",
                ),
            ]
        else:
            output_text = ResponseOutputText(
                annotations=[], text="Mock final diagnosis", type="output_text"
            )
            message = ResponseOutputMessage(
                id="msg_mock_1",
                content=[output_text],
                role="assistant",
                status="completed",
                type="message",
            )
            response = _response(response_id="resp_mock_2", output=[message])
            events = [
                ResponseOutputItemDoneEvent(
                    item=message,
                    output_index=0,
                    sequence_number=1,
                    type="response.output_item.done",
                ),
                ResponseCompletedEvent(
                    response=response,
                    sequence_number=2,
                    type="response.completed",
                ),
            ]

        async def stream() -> AsyncIterator[Any]:
            for event in events:
                yield event

        return stream()


def _sse_data(body: str) -> list[dict[str, Any]]:
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


def test_live_run_uses_mocked_openai_client_and_preserves_raw_tool_items(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    # OpenAIProvider is still the production adapter; only its AsyncOpenAI
    # constructor is replaced, so the test never makes a network request.
    import agents.models.openai_provider as provider_module

    monkeypatch.setattr(provider_module, "AsyncOpenAI", _MockedOpenAIClient)
    app = create_app(artifact_root=tmp_path)

    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "live"
        accepted = client.post("/api/runs", json={"input": "inspect the loop"})
        assert accepted.status_code == 202
        metadata = accepted.json()
        run_id = metadata["run_id"]

        sse_response = client.get(metadata["events_url"])

    sse_events = _sse_data(sse_response.text)
    jsonl_events = _jsonl_events(tmp_path, run_id)
    assert "event:" not in sse_response.text
    assert sse_events == jsonl_events
    assert [event["seq"] for event in sse_events] == list(range(len(sse_events)))
    assert {event["run_id"] for event in sse_events} == {run_id}
    assert [event["type"] for event in sse_events] == [
        "run_started",
        "tool_call",
        "tool_result",
        "final_output",
        "run_completed",
    ]
    assert sse_events[1]["payload"] == {
        "arguments": '{"query":"loop"}',
        "call_id": "call_mock_1",
        "name": "inspect_synthetic_gym",
        "type": "function_call",
        "id": "fc_mock_1",
        "caller": None,
        "namespace": None,
        "status": "completed",
    }
    assert sse_events[2]["payload"]["type"] == "function_call_output"
    assert sse_events[3]["payload"] == {"text": "Mock final diagnosis"}
    assert _MockedOpenAIClient.last is not None
    assert _MockedOpenAIClient.last.init_kwargs["api_key"] == "test-only-key"
    assert "test-only-key" not in sse_response.text
    assert "test-only-key" not in (tmp_path / f"{run_id}.jsonl").read_text(encoding="utf-8")


def test_missing_key_is_explicit_offline_demo(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": "unsafe delete"})
        assert accepted.json()["mode"] == "offline_demo"
        events = _sse_data(client.get(accepted.json()["events_url"]).text)

    assert any(event["type"] == "offline_demo" for event in events)
    assert events[-1]["type"] == "run_completed"
    assert events[-1]["payload"] == {"status": "offline_demo", "mode": "offline_demo"}


class _TimeoutAdapter(OpenAIWhiteBoxAdapter):
    async def _run_live(self, query: str, channel) -> str:
        raise TimeoutError


def test_timeout_emits_failure_then_offline_fallback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    app = create_app(runtime=_TimeoutAdapter(timeout_seconds=1), artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": "loop"})
        events = _sse_data(client.get(accepted.json()["events_url"]).text)

    failure = next(event for event in events if event["type"] == "run_failed")
    assert failure["payload"] == {"status": "offline_demo", "code": "timeout"}
    assert any(event["type"] == "offline_demo" for event in events)
    assert events[-1]["type"] == "run_completed"


def test_single_origin_serves_demo_assets_before_static_catch_all(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    repository_root = Path(__file__).resolve().parents[2]
    app = create_app(artifact_root=tmp_path, web_root=repository_root / "web")

    with TestClient(app) as client:
        root = client.get("/")
        script = client.get("/src/app.mjs")
        health = client.get("/health")
        accepted = client.post("/api/runs", json={"input": "single-origin smoke test"})
        events = client.get(accepted.json()["events_url"])
        traversal = client.get("/%2e%2e/pyproject.toml")

    assert root.status_code == 200
    assert "NIGHTMARE" in root.text
    assert script.status_code == 200
    assert "startLiveRun" in script.text
    assert health.status_code == 200
    assert accepted.status_code == 202
    assert events.status_code == 200
    assert "tool_call" in events.text
    assert traversal.status_code == 404


def test_missing_web_directory_keeps_api_available(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(artifact_root=tmp_path, web_root=tmp_path / "missing-web")

    with TestClient(app) as client:
        root = client.get("/")
        health = client.get("/health")

    assert root.status_code == 200
    assert root.json()["web_status"] == "missing"
    assert health.json()["status"] == "ok"


def test_structured_target_publishes_pinned_preflight_to_sse_and_jsonl(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_external_preflight(),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={"input": "one input", "target": _target_payload()},
        )
        assert accepted.status_code == 202
        metadata = accepted.json()
        events = _sse_data(client.get(metadata["events_url"]).text)

    assert events == _jsonl_events(tmp_path, metadata["run_id"])
    assert [event["seq"] for event in events] == list(range(len(events)))
    event_types = [event["type"] for event in events]
    assert event_types == [
        "run_started",
        "phase.changed",
        "source_descriptor",
        "source_snapshot",
        "target_profile",
        "pack_selection",
        "behavior_profile",
        "pack.selected",
        "experiment_plan",
        "phase.changed",
        "gym.baseline.completed",
        "gym.tool_call",
        "gym.tool_result",
        "gym.tool_call",
        "gym.tool_result",
        "gym.tool_call",
        "gym.tool_result",
        "gym.tool_call",
        "gym.tool_result",
        "oracle.report",
        "damage.updated",
        "failure.detected",
        "phase.changed",
        "autopsy.ready",
        "phase.changed",
        "vaccine.proposed",
        "phase.changed",
        "verification.updated",
        "replay.completed",
        "protected_replay",
        "finding_report",
        "lab_report",
        "offline_demo",
        "tool_call",
        "tool_result",
        "final_output",
        "run_completed",
    ]
    source = next(event["payload"] for event in events if event["type"] == "source_descriptor")
    snapshot = next(event["payload"] for event in events if event["type"] == "source_snapshot")
    target_profile = next(
        event["payload"] for event in events if event["type"] == "target_profile"
    )
    compatibility_selection = next(
        event["payload"] for event in events if event["type"] == "pack_selection"
    )
    routed_selection = next(
        event["payload"] for event in events if event["type"] == "pack.selected"
    )
    profile = next(event["payload"] for event in events if event["type"] == "behavior_profile")
    plan = next(event["payload"] for event in events if event["type"] == "experiment_plan")
    assert source["resolved_sha"] == PINNED_SHA
    assert source["requested_ref"] == "main"
    assert snapshot["mode"] == "bounded_download"
    assert snapshot["execution_scope"] == "manifest_only"
    assert snapshot["files"][0]["path"] == ".agent24/manifest.json"
    assert snapshot["files"][0]["content_sha256"].startswith("sha256:")
    assert target_profile["profile_label"] == "OWNER MANIFEST"
    assert target_profile["provenance"]["origin"] == "owner_manifest"
    assert compatibility_selection["pack_id"] == "life-v0-sandbox.v1"
    assert compatibility_selection["max_experiments"] == 0
    assert compatibility_selection["fixture_id"] is None
    assert routed_selection["selected"]["pack_id"] == "life-v0-sandbox.v1"
    assert profile["source_ref"] == f"example/cake-agent@{PINNED_SHA}"
    assert profile["baseline_observed"] is False
    assert plan["scenario"]["faults"][0]["fault"] == "commit_then_timeout"
    assert plan["scenario"]["faults"][0]["target_tool"] == "payment.charge"
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    lab_report = next(event["payload"] for event in events if event["type"] == "lab_report")
    sandbox_replay = next(
        event["payload"] for event in events if event["type"] == "protected_replay"
    )
    assert finding["status"] == "verified_mitigation"
    assert finding["reproduction_count"] == finding["reproduction_total"] == 3
    assert finding["evidence"]
    assert "외부 저장소 코드는 실행하지 않았으며" in finding["residual_risk"][0]
    assert lab_report["findings"][0]["verified"]["accepted"] is True
    assert lab_report["termination"]["reason"] == "coverage_complete"
    assert sandbox_replay["accepted"] is True
    assert sandbox_replay["perturbed"]["charge_count"] == 2
    assert sandbox_replay["protected"]["charge_count"] == 1
    assert "Buy one cake under budget." not in json.dumps(events, ensure_ascii=False)
    assert events[-1]["payload"]["mode"] == "offline_demo"


def test_manifestless_participant_terminates_with_ordered_compatibility_report(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_participant_preflight(),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)
    target = {
        "repository_url": "https://github.com/midwestchekhov/agent24",
        "requested_ref": "main",
        "mission": "논문 주장의 근거를 검토해줘.",
    }

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={"input": "one input", "target": target},
        )
        metadata = accepted.json()
        events = _sse_data(client.get(metadata["events_url"]).text)

    assert events == _jsonl_events(tmp_path, metadata["run_id"])
    assert [event["type"] for event in events] == [
        "run_started",
        "phase.changed",
        "source_descriptor",
        "source_snapshot",
        "target_profile",
        "pack_selection",
        "compatibility_report",
        "run_completed",
    ]
    assert {event["run_id"] for event in events} == {metadata["run_id"]}
    assert [event["seq"] for event in events] == list(range(len(events)))
    snapshot = events[3]["payload"]
    profile = events[4]["payload"]
    selection = events[5]["payload"]
    report = events[6]["payload"]
    assert snapshot["mode"] == "metadata_only"
    assert snapshot["execution_scope"] == "static_metadata_only"
    assert snapshot["files"]
    assert profile["profile_label"] == "LAB-INFERRED STATIC PROFILE"
    assert profile["provenance"]["resolved_sha"] == PAPER_PLAYGROUND_SHA
    assert selection["status"] == "compatible_candidate"
    assert selection["selected_domain"] == "research"
    assert selection["pack_id"] == "research-agent-pack.v1"
    assert selection["registry_version"] == "domain-pack-registry.v1"
    assert selection["fixture_id"] is None
    assert selection["max_experiments"] == 0
    assert report["experiments_run"] == 0
    assert report["findings"] == []
    assert "no vulnerability" in report["claim_boundary"]
    assert not any(
        event["type"]
        in {
            "experiment_plan",
            "gym.tool_call",
            "tool_call",
            "failure.detected",
            "offline_demo",
        }
        for event in events
    )
    assert events[-1]["payload"]["status"] == "compatible_candidate"
    assert events[-1]["payload"]["mode"] == "compatibility_only"


def test_live_target_passes_bounded_synthetic_evidence_to_openai(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    import agents.models.openai_provider as provider_module

    monkeypatch.setattr(provider_module, "AsyncOpenAI", _MockedOpenAIClient)
    runtime = OpenAIWhiteBoxAdapter(preflight=_external_preflight())
    app = create_app(runtime=runtime, artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={"input": "one input", "target": _target_payload()},
        )
        events = _sse_data(client.get(accepted.json()["events_url"]).text)

    assert events[-1]["payload"] == {"status": "completed", "mode": "live"}
    assert _MockedOpenAIClient.last is not None
    first_request = _MockedOpenAIClient.last.requests[0]
    model_input = first_request["input"][0]["content"]
    assert "DIAGNOSTIC CONTEXT" in model_input
    assert '"execution_scope":"synthetic_archetype"' in model_input
    assert f"example/cake-agent@{PINNED_SHA}" in model_input
    assert '"finding_status":"verified_mitigation"' in model_input
    assert "test-only-key" not in model_input


def test_source_preflight_failure_stops_without_target_claims(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_external_preflight(),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={
                "input": "one input",
                "target": _target_payload(repository_url="https://gitlab.com/example/agent"),
            },
        )
        events = _sse_data(client.get(accepted.json()["events_url"]).text)

    event_types = [event["type"] for event in events]
    assert event_types == ["run_started", "phase.changed", "run_failed", "run_completed"]
    assert "source_descriptor" not in event_types
    assert "behavior_profile" not in event_types
    assert "experiment_plan" not in event_types
    assert "offline_demo" not in event_types
    assert "tool_call" not in event_types
    assert "tool_result" not in event_types
    source_failure_message = (
        "외부 Agent source preflight를 완료하지 못해 제출한 Agent 분석과 "
        "synthetic target 실험을 실행하지 않았습니다."
    )
    failure = next(event for event in events if event["type"] == "run_failed")
    assert failure["payload"] == {
        "status": "offline_demo",
        "code": "source_preflight_failed",
        "message": source_failure_message,
    }
    assert events[-1]["payload"] == {
        "status": "source_preflight_failed",
        "mode": "offline_demo",
        "experiments_run": 0,
        "findings": 0,
        "message": source_failure_message,
        "safety_boundary": "SIMULATION_ONLY",
    }


class _FailingLabLoop:
    async def run(self, **_kwargs):
        raise RuntimeError("provider-secret-must-not-leak")


def test_diagnostic_failure_is_sanitized_and_falls_back(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_external_preflight(),
        lab_loop=_FailingLabLoop(),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={"input": "one input", "target": _target_payload()},
        )
        events = _sse_data(client.get(accepted.json()["events_url"]).text)

    failure = next(event for event in events if event["type"] == "run_failed")
    assert failure["payload"]["status"] == "offline_demo"
    assert failure["payload"]["code"] == "diagnostic_loop_failed"
    # A pack failure is reported as that pack failing (#57), never re-routed to
    # another pack whose success would then read as this run's result.
    assert failure["payload"]["pack_id"] == "life-v0-sandbox.v1"
    assert "life-v0-sandbox.v1" in failure["payload"]["message"]
    assert "research-agent-pack.v1" not in failure["payload"]["message"]
    # Only the exception class may surface, never its message.
    assert "RuntimeError" in failure["payload"]["message"]
    rendered = json.dumps(events, ensure_ascii=False)
    assert "provider-secret-must-not-leak" not in rendered
    assert events[-1]["payload"] == {"status": "offline_demo", "mode": "offline_demo"}


def test_unsupported_manifest_stops_without_inventing_an_experiment(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_external_preflight(supported=False),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={"input": "one input", "target": _target_payload()},
        )
        events = _sse_data(client.get(accepted.json()["events_url"]).text)

    assert [event["type"] for event in events] == [
        "run_started",
        "phase.changed",
        "source_descriptor",
        "source_snapshot",
        "target_profile",
        "pack_selection",
        "behavior_profile",
        # Published before the stop branch: on the unsupported path this is the
        # only record of which pack was considered and why nothing ran.
        "pack.selected",
        "finding_report",
        "lab_report",
        "run_completed",
    ]
    finding = next(event["payload"] for event in events if event["type"] == "finding_report")
    lab_report = next(event["payload"] for event in events if event["type"] == "lab_report")
    assert finding["status"] == "unsupported"
    assert finding["unsupported_scope"] == ["unsupported:vendor.transfer"]
    assert lab_report["findings"] == []
    assert lab_report["termination"]["reason"] == "unsupported_input"
    assert events[-1]["payload"]["status"] == "unsupported"
    assert "지원 어휘 밖" in events[-1]["payload"]["message"]
    # The routing decision is on the record even though nothing ran.
    pack = next(event["payload"] for event in events if event["type"] == "pack.selected")
    assert pack["selected"] is None
    assert pack["candidates"] == []
    assert pack["selection_digest"]
    assert "Buy one cake under budget." not in json.dumps(events, ensure_ascii=False)


def test_structured_target_validation_rejects_incomplete_submission(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(artifact_root=tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/runs",
            json={
                "input": "one input",
                "target": {
                    "repository_url": "https://github.com/example/cake-agent",
                    "requested_ref": "main",
                },
            },
        )

    assert response.status_code == 422


def test_github_token_is_only_reported_as_configuration_state(
    monkeypatch, tmp_path: Path
) -> None:
    secret = "github-test-token-must-not-leak"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    app = create_app(artifact_root=tmp_path)

    with TestClient(app) as client:
        health = client.get("/health")
        accepted = client.post(
            "/api/runs",
            json={
                "input": "one input",
                "target": _target_payload(repository_url="https://gitlab.com/example/agent"),
            },
        )
        events = client.get(accepted.json()["events_url"])

    assert health.json()["github_configured"] is True
    assert secret not in health.text
    assert secret not in events.text
    assert secret not in (tmp_path / f"{accepted.json()['run_id']}.jsonl").read_text(
        encoding="utf-8"
    )
