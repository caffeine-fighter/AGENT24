from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def load_smoke_module() -> ModuleType:
    path = Path(__file__).parents[1] / "scripts" / "hosted-smoke.py"
    spec = importlib.util.spec_from_file_location("agent24_hosted_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SMOKE = load_smoke_module()
RUN_ID = "run-1"
PINNED_SHA = "a" * 40


def trace(*, hosted: bool) -> list[dict[str, object]]:
    phases = ["CLONE"] * 12 + ["CRASH"] * 7 + ["AUTOPSY"] * 3
    phases += ["VACCINE"] * 3 + ["REPLAY"] * 9
    events: list[dict[str, object]] = [
        {
            "run_id": RUN_ID,
            "seq": index,
            "type": "placeholder",
            "phase": phase,
            "data": {},
            "raw": {},
        }
        for index, phase in enumerate(phases, start=1)
    ]
    events[0].update(
        type="run.started",
        data={"safety_boundary": "SIMULATION_ONLY"},
    )
    events[4].update(
        type="source_descriptor",
        data={"resolved_sha": PINNED_SHA},
    )
    events[10].update(
        type="tool_result",
        raw={
            "name": "openai.responses.plan_experiment",
            "output": {
                "fallback": not hosted,
                "response_id": "resp_live_smoke" if hosted else None,
            },
        },
    )
    events[-1].update(
        type="run.completed",
        data={"status": "verified", "safety_boundary": "SIMULATION_ONLY"},
    )
    return events


@pytest.mark.parametrize(
    ("mode", "hosted", "response_observed"),
    [("openai_hosted", True, True), ("offline_demo", False, False)],
)
def test_verify_trace_accepts_both_explicit_modes(
    mode: str, hosted: bool, response_observed: bool
) -> None:
    result = SMOKE.verify_trace(trace(hosted=hosted), run_id=RUN_ID, expected_mode=mode)

    assert result == {
        "event_count": 34,
        "phases": ["CLONE", "CRASH", "AUTOPSY", "VACCINE", "REPLAY"],
        "source_ref": PINNED_SHA,
        "openai_response_observed": response_observed,
        "terminal_status": "verified",
    }


def test_verify_trace_rejects_non_contiguous_sequence() -> None:
    events = trace(hosted=True)
    events[20]["seq"] = 99

    with pytest.raises(RuntimeError, match="sequence is not contiguous"):
        SMOKE.verify_trace(events, run_id=RUN_ID, expected_mode="openai_hosted")


def test_parse_sse_accepts_crlf_frames() -> None:
    events = trace(hosted=False)[:2]
    body = "\r\n\r\n".join(f"data: {json.dumps(event)}" for event in events) + "\r\n\r\n"

    assert SMOKE.parse_sse(body) == events


def test_private_site_header_is_process_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SITES_BYPASS_TOKEN", "private-token")

    assert SMOKE.request_headers(accepts="application/json") == {
        "Accept": "application/json",
        "User-Agent": "agent24-hosted-smoke/1.0",
        "OAI-Sites-Authorization": "Bearer private-token",
    }
