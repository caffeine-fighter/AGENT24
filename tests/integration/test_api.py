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

from agent24.api import OpenAIWhiteBoxAdapter, create_app


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
