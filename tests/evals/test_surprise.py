from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent24.api import create_app
from agent24.evals import SURPRISE_CASES, evaluate_event_stream, parse_sse


def _sse_events(body: str) -> list[dict]:
    return parse_sse(body)


@pytest.mark.parametrize("case", SURPRISE_CASES, ids=lambda case: case.case_id)
def test_surprise_matrix_uses_one_input_raw_stream_path(monkeypatch, tmp_path: Path, case) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(artifact_root=tmp_path)

    with TestClient(app) as client:
        accepted = client.post("/api/runs", json={"input": case.mission})
        assert accepted.status_code == 202
        metadata = accepted.json()
        response = client.get(metadata["events_url"])

    report = evaluate_event_stream(case, _sse_events(response.text))
    assert report.passed, report.errors
    assert report.run_id == metadata["run_id"]
    assert report.mode == "offline_demo"
    assert report.side_effect_evidence is True
    assert report.scenario_id == case.expected_scenario


def test_gate_rejects_missing_result_and_side_effect_evidence() -> None:
    case = SURPRISE_CASES[0]
    events = [
        {
            "run_id": "broken",
            "seq": 0,
            "timestamp": "2026-08-01T07:00:00+00:00",
            "type": "run_started",
            "payload": {"mode": "live"},
        },
        {
            "run_id": "broken",
            "seq": 1,
            "timestamp": "2026-08-01T07:00:01+00:00",
            "type": "tool_call",
            "payload": {"name": "inspect_synthetic_gym"},
        },
        {
            "run_id": "broken",
            "seq": 2,
            "timestamp": "2026-08-01T07:00:02+00:00",
            "type": "run_completed",
            "payload": {"mode": "live"},
        },
    ]

    report = evaluate_event_stream(case, events)
    assert report.passed is False
    assert "tool_result is missing" in report.errors
    assert "raw tool result does not prove external_side_effects=false" in report.errors


def test_parser_rejects_malformed_sse_json() -> None:
    with pytest.raises(ValueError, match="invalid SSE JSON"):
        parse_sse("data: {not-json}\n\n")


def test_parser_preserves_raw_payload() -> None:
    payload = {"run_id": "r1", "seq": 0, "type": "future", "payload": {"raw": "그대로"}}
    parsed = parse_sse(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    assert parsed == [payload]
