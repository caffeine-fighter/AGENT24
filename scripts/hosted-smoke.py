"""Verify the deployed NIGHTMARE LAB contract without exposing credentials.

The smoke test checks the public HTTP surface only: configuration metadata,
one run creation, the complete SSE trace, source pinning, the OpenAI planning
evidence, and the synthetic-only terminal boundary.  With ``--expect-adapter``
it additionally checks the exact UCP adapter contract and ``complete_purchase``
Gym trace.  It never prints request headers or environment-variable values.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from uuid import uuid4

DEFAULT_MISSION = "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘."
DEFAULT_REPOSITORY = "https://github.com/caffeine-fighter/AGENT24"
EXPECTED_PHASES = ("CLONE", "CRASH", "AUTOPSY", "VACCINE", "REPLAY")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        required=True,
        help=(
            "Dynamic NIGHTMARE LAB server URL. "
            "GitHub Pages is a static demo and is not valid here."
        ),
    )
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--mission", default=DEFAULT_MISSION)
    parser.add_argument(
        "--expect-mode",
        choices=("openai_hosted", "offline_demo", "compatibility_only"),
        default="openai_hosted",
        help="Expected mode for the created run (default: openai_hosted).",
    )
    parser.add_argument(
        "--expect-terminal",
        choices=(
            "verified",
            "compatibility_only",
            "source_unresolved",
            "source_preflight_failed",
            "openai_analysis_unavailable",
            "openai_analysis_failed",
        ),
        default="verified",
        help="Expected terminal status for the created run (default: verified).",
    )
    parser.add_argument(
        "--expect-source",
        choices=("pinned", "unresolved"),
        default="pinned",
        help="Expected submitted-source outcome (default: pinned).",
    )
    parser.add_argument(
        "--expect-adapter",
        action="store_true",
        help="Require the exact pinned UCP adapter path instead of the owner-manifest path.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=45.0)
    parser.add_argument(
        "--allow-ephemeral-context",
        action="store_true",
        help="Allow a local server without a stable RUN_CONTEXT_SECRET.",
    )
    return parser.parse_args()


def request_headers(*, accepts: str) -> dict[str, str]:
    headers = {"Accept": accepts, "User-Agent": "agent24-hosted-smoke/1.0"}
    bypass_token = os.getenv("SITES_BYPASS_TOKEN", "").strip()
    if bypass_token:
        headers["OAI-Sites-Authorization"] = f"Bearer {bypass_token}"
    return headers


def fetch(
    url: str,
    *,
    accepts: str,
    timeout_seconds: float,
    payload: dict[str, Any] | None = None,
    return_http_error: bool = False,
) -> tuple[int, str, str]:
    headers = request_headers(accepts=accepts)
    body = None
    method = "GET"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return response.status, response.headers.get_content_type(), response.read().decode()
    except HTTPError as error:
        if return_http_error:
            return error.code, error.headers.get_content_type(), error.read().decode()
        raise RuntimeError(f"{method} request returned HTTP {error.code}") from None
    except URLError as error:
        raise RuntimeError(f"{method} request failed: {error.reason}") from None


def parse_json(text: str, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"{label} did not return JSON: {error.msg}") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must return a JSON object")
    return payload


def parse_sse(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    normalized = text.replace("\r\n", "\n")
    for block in normalized.split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
        )
        if not data:
            continue
        payload = parse_json(data, label="SSE data")
        events.append(payload)
    if not events:
        raise RuntimeError("SSE stream did not contain any data events")
    return events


def unique_in_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def event_type(event: dict[str, Any]) -> str:
    """Read the canonical wire type."""

    value = event.get("type")
    return value if isinstance(value, str) else ""


def event_payload(event: dict[str, Any]) -> Any:
    """Read the canonical payload field."""

    return event.get("payload")


def event_raw(event: dict[str, Any]) -> Any:
    """Read a tool event's unedited raw item from the canonical payload."""

    return event_payload(event)


def build_run_payload(*, repository: str, requested_ref: str, mission: str) -> dict[str, Any]:
    return {
        "target": {
            "mission": mission,
            "repository_url": repository,
            "requested_ref": requested_ref,
        }
    }


def negative_event_urls(events_url: str, *, run_id: str) -> list[tuple[str, str, int]]:
    parsed = urlsplit(events_url)
    require(not parsed.scheme and not parsed.netloc, "events_url must be same-origin relative")
    require(parsed.path == f"/api/runs/{run_id}/events", "events_url path is not bound to run_id")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    require(
        [name for name, _ in pairs] == ["run_context"],
        "events_url exposed mutable evidence fields",
    )
    token = pairs[0][1]
    parts = token.split(".")
    require(len(parts) == 3 and parts[0] == "v1", "run context is not an encrypted v1 token")

    version, iv, ciphertext = parts
    require(bool(ciphertext), "run context ciphertext is empty")
    forged_ciphertext = ("B" if ciphertext[0] == "A" else "A") + ciphertext[1:]
    tampered_token = f"{version}.{iv}.{forged_ciphertext}"
    tampered_query = urlencode({"run_context": tampered_token})
    injected_query = urlencode({"run_context": token, "resolved_sha": "b" * 40})
    unknown_path = f"/api/runs/{uuid4()}/events"
    return [
        ("token_tamper", urlunsplit(("", "", parsed.path, tampered_query, "")), 401),
        ("evidence_query_injection", urlunsplit(("", "", parsed.path, injected_query, "")), 400),
        ("cross_run_reuse", urlunsplit(("", "", unknown_path, parsed.query, "")), 401),
    ]


def verify_rejected_stream(
    status: int,
    content_type: str,
    body: str,
    *,
    label: str,
    expected_status: int,
) -> None:
    require(
        status == expected_status,
        f"{label} returned status {status}, expected {expected_status}",
    )
    require(content_type != "text/event-stream", f"{label} unexpectedly opened an SSE stream")
    require(
        "run_completed" not in body and '"status":"verified"' not in body,
        f"{label} emitted a verified outcome",
    )


def legacy_input(repository: str, requested_ref: str, mission: str) -> str:
    return "\n".join(
        [
            (
                "NIGHTMARE LAB에서 다음 GitHub 저장소의 에이전트를 "
                "가상 환경에서 안전하게 시험해 주세요."
            ),
            f"저장소: {repository}",
            f"브랜치 또는 커밋: {requested_ref}",
            f"맡길 일: {mission}",
            (
                "실제 외부 서비스를 호출하거나 상태를 바꾸지 말고, "
                "관찰한 사실·추정 원인·제안한 해결책·재검증 결과를 구분해 주세요."
            ),
        ]
    )


def verify_trace(
    events: list[dict[str, Any]],
    *,
    run_id: str,
    expected_mode: str,
    expected_source: str,
    expected_terminal: str = "verified",
    expected_adapter: bool = False,
) -> dict[str, Any]:
    minimum_events = 39 if expected_adapter else 34 if expected_terminal == "verified" else 8
    require(
        len(events) >= minimum_events,
        f"expected the complete hosted trace, got {len(events)} events",
    )
    require(
        [event.get("seq") for event in events] == list(range(len(events))),
        "SSE sequence is not contiguous",
    )
    require(all(event.get("run_id") == run_id for event in events), "SSE run_id changed")
    require(
        all(
            "payload" in event and "data" not in event and "raw" not in event
            for event in events
        ),
        "SSE event envelope is not canonical",
    )
    require(event_type(events[0]) == "run_started", "first event is not run_started")
    terminals = [event for event in events if event_type(event) == "run_completed"]
    require(len(terminals) == 1, "stream must contain exactly one run_completed terminal")
    require(events[-1] is terminals[0], "run_completed terminal is not the final event")

    phases = unique_in_order(
        str(event.get("phase")) for event in events if isinstance(event.get("phase"), str)
    )
    expected_phases = (
        list(EXPECTED_PHASES)
        if expected_terminal
        in {"verified", "openai_analysis_unavailable", "openai_analysis_failed"}
        else ["CLONE"]
    )
    require(phases == expected_phases, f"unexpected phase order: {phases}")

    start_data = event_payload(events[0])
    terminal_data = event_payload(events[-1])
    require(isinstance(start_data, dict), "run_started payload is missing")
    require(isinstance(terminal_data, dict), "run_completed payload is missing")
    require(start_data.get("safety_boundary") == "SIMULATION_ONLY", "start boundary missing")
    require(
        terminal_data.get("safety_boundary") == "SIMULATION_ONLY",
        "terminal boundary missing",
    )
    require(
        terminal_data.get("status") == expected_terminal,
        f"run did not finish {expected_terminal}",
    )
    for field in (
        "source_resolved",
        "diagnostic_completed",
        "openai_analysis_completed",
        "execution_scope",
    ):
        require(field in terminal_data, f"terminal stage truth field {field} is missing")

    descriptor = next((event for event in events if event_type(event) == "source_descriptor"), None)
    require(isinstance(descriptor, dict), "source_descriptor event is missing")
    descriptor_data = event_payload(descriptor) if descriptor else None
    require(isinstance(descriptor_data, dict), "source_descriptor data is missing")
    resolved_sha = (
        descriptor_data.get("resolved_sha") if isinstance(descriptor_data, dict) else None
    )
    if expected_source == "pinned":
        require(
            isinstance(resolved_sha, str) and FULL_SHA.fullmatch(resolved_sha),
            "source ref was not pinned",
        )
        snapshot = next((event for event in events if event_type(event) == "source_snapshot"), None)
        require(isinstance(snapshot, dict), "source_snapshot event is missing")
        snapshot_data = event_payload(snapshot) if snapshot else None
        normal_experiment_terminal = expected_terminal in {
            "verified",
            "openai_analysis_unavailable",
            "openai_analysis_failed",
        }
        expected_snapshot_mode = (
            "bounded_download" if normal_experiment_terminal else "metadata_only"
        )
        expected_execution_scope = (
            "allowlisted_adapter"
            if expected_adapter
            else "manifest_and_entrypoint"
            if normal_experiment_terminal
            else "static_metadata_only"
        )
        require(
            isinstance(snapshot_data, dict)
            and snapshot_data.get("mode") == expected_snapshot_mode
            and snapshot_data.get("execution_scope") == expected_execution_scope,
            "pinned source snapshot did not match the expected intake mode",
        )
        profile = next((event for event in events if event_type(event) == "target_profile"), None)
        require(
            isinstance(profile, dict)
            and isinstance(event_payload(profile), dict)
            and event_payload(profile).get("profile_label")
            == (
                "ALLOWLISTED ADAPTER"
                if expected_adapter
                else "OWNER MANIFEST"
                if normal_experiment_terminal
                else "LAB-INFERRED STATIC PROFILE"
            ),
            "pinned target profile is missing or has the wrong provenance",
        )
        if normal_experiment_terminal:
            if expected_adapter:
                adapter = next(
                    (event for event in events if event_type(event) == "adapter.matched"),
                    None,
                )
                require(isinstance(adapter, dict), "allowlisted adapter match event is missing")
                adapter_data = event_payload(adapter) if adapter else None
                require(
                    isinstance(adapter_data, dict)
                    and adapter_data.get("adapter_id") == "ucp-shopping-v0"
                    and adapter_data.get("execution_mode") == "network_disabled_local_replacement"
                    and adapter_data.get("network_access") == "disabled",
                    "UCP adapter contract is missing or unsafe",
                )
                require(
                    any(
                        event_type(event) == "gym.tool_call"
                        and isinstance(event_raw(event), dict)
                        and event_raw(event).get("name") == "complete_purchase"
                        for event in events
                    ),
                    "UCP Gym trace did not execute complete_purchase",
                )
                require(
                    any(event_type(event) == "lab_report" for event in events),
                    "UCP adapter path did not produce a lab report",
                )
            files = snapshot_data.get("files", []) if isinstance(snapshot_data, dict) else []
            require(
                isinstance(snapshot_data, dict)
                and any(
                    file.get("path")
                    == (
                        "upsonic_shopping_agent.py"
                        if expected_adapter
                        else "agent/main.py"
                    )
                    for file in files
                    if isinstance(file, dict)
                ),
                "bounded owner entrypoint evidence is missing",
            )
            canonical_pack = next(
                (event for event in events if event_type(event) == "pack.selected"), None
            )
            require(
                isinstance(canonical_pack, dict)
                and isinstance(event_payload(canonical_pack), dict)
                and isinstance(event_payload(canonical_pack).get("selected"), dict)
                and event_payload(canonical_pack)["selected"].get("domain_kind") == "life",
                "canonical Life pack selection is missing",
            )
            require(
                any(event_type(event) == "experiment_plan" for event in events),
                "owner manifest path did not produce an experiment plan",
            )
        else:
            require(
                any(event_type(event) == "compatibility_report" for event in events),
                "compatibility report is missing",
            )
    else:
        require(resolved_sha is None, "source unexpectedly resolved")
        snapshot = next((event for event in events if event_type(event) == "source_snapshot"), None)
        require(isinstance(snapshot, dict), "fallback source_snapshot event is missing")
        require(
            isinstance(event_payload(snapshot), dict)
            and event_payload(snapshot).get("execution_scope") == "none",
            "unresolved source was not bounded as an empty intake",
        )
        if expected_terminal in {"source_unresolved", "source_preflight_failed"}:
            failure = next((event for event in events if event_type(event) == "stage_failed"), None)
            require(isinstance(failure, dict), "source failure event is missing")
            failure_data = event_payload(failure) if failure else None
            require(
                isinstance(failure_data, dict)
                and failure_data.get("stage") == "source"
                and failure_data.get("code") == expected_terminal,
                "source failure code did not match the expected terminal",
            )
            require(
                not any(event_type(event) == "experiment_plan" for event in events),
                "source failure unexpectedly planned an experiment",
            )
            return {
                "event_count": len(events),
                "phases": phases,
                "source_ref": resolved_sha,
                "source_pinned": False,
                "openai_response_observed": False,
                "terminal_status": terminal_data.get("status"),
            }

    openai_result = next(
        (
            event
            for event in events
            if event_type(event) == "tool_result"
            and isinstance(event_raw(event), dict)
            and event_raw(event).get("name") == "openai.responses.plan_experiment"
        ),
        None,
    )
    if expected_terminal == "compatibility_only":
        require(openai_result is None, "compatibility-only run unexpectedly planned an experiment")
        return {
            "event_count": len(events),
            "phases": phases,
            "source_ref": resolved_sha,
            "source_pinned": expected_source == "pinned",
            "openai_response_observed": False,
            "terminal_status": terminal_data.get("status"),
        }
    if expected_mode == "offline_demo" or expected_terminal in {
        "openai_analysis_unavailable",
        "openai_analysis_failed",
    }:
        require(openai_result is None, "offline run emitted an OpenAI planner tool event")
        failure = next(
            (event for event in events if event_type(event) == "stage_failed"),
            None,
        )
        require(isinstance(failure, dict), "OpenAI stage failure event is missing")
        failure_data = event_payload(failure) if failure else None
        require(
            isinstance(failure_data, dict)
            and failure_data.get("stage") == "openai_analysis"
            and failure_data.get("code") in {
                "openai_key_missing",
                "openai_provider_non_2xx",
                "openai_response_parse_failed",
                "openai_timeout",
                "openai_provider_failed",
            },
            "offline OpenAI stage failure did not carry a bounded reason code",
        )
        return {
            "event_count": len(events),
            "phases": phases,
            "source_ref": resolved_sha,
            "source_pinned": expected_source == "pinned",
            "openai_response_observed": False,
            "terminal_status": terminal_data.get("status"),
        }
    require(isinstance(openai_result, dict), "OpenAI tool_result event is missing")
    raw = event_raw(openai_result) if openai_result else None
    output = raw.get("output") if isinstance(raw, dict) else None
    require(isinstance(output, dict), "OpenAI result output is missing")
    response_id = output.get("response_id") if isinstance(output, dict) else None
    fallback = output.get("fallback") if isinstance(output, dict) else None
    if expected_mode == "openai_hosted":
        require(fallback is False, "OpenAI result unexpectedly used fallback")
        require(
            isinstance(response_id, str) and response_id.startswith("resp_"),
            "OpenAI response_id evidence is missing",
        )
    else:
        require(fallback is True, "offline run did not declare fallback")
        require(response_id is None, "offline run unexpectedly has an OpenAI response_id")

    return {
        "event_count": len(events),
        "phases": phases,
        "source_ref": resolved_sha,
        "source_pinned": expected_source == "pinned",
        "openai_response_observed": (
            isinstance(response_id, str) and response_id.startswith("resp_")
        ),
        "terminal_status": terminal_data.get("status"),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    if args.timeout_seconds <= 0:
        raise SystemExit("--timeout-seconds must be positive")

    base_url = args.base_url.rstrip("/") + "/"
    health_url = urljoin(base_url, "health")
    status, content_type, body = fetch(
        health_url,
        accepts="application/json",
        timeout_seconds=args.timeout_seconds,
    )
    require(status == 200, f"health returned status {status}")
    require(content_type == "application/json", f"health returned {content_type}")
    health = parse_json(body, label="health")
    require(health.get("status") == "ok", "health status is not ok")
    require(health.get("safety_boundary") == "SIMULATION_ONLY", "health boundary missing")
    require("openai_api_key" not in health, "health exposed a credential field")
    require(
        args.allow_ephemeral_context or health.get("run_context_secret_configured") is True,
        "hosted RUN_CONTEXT_SECRET is not configured",
    )

    run_url = urljoin(base_url, "api/runs")
    status, content_type, body = fetch(
        run_url,
        accepts="application/json",
        timeout_seconds=args.timeout_seconds,
        payload={
            "input": legacy_input(args.repository, args.ref, args.mission),
            **build_run_payload(
                repository=args.repository,
                requested_ref=args.ref,
                mission=args.mission,
            ),
        },
    )
    require(status == 202, f"run creation returned status {status}")
    require(content_type == "application/json", f"run creation returned {content_type}")
    run = parse_json(body, label="run creation")
    require(
        run.get("mode") == args.expect_mode,
        f"expected {args.expect_mode}, got {run.get('mode')}",
    )
    run_id = run.get("run_id")
    events_url = run.get("events_url")
    require(isinstance(run_id, str) and run_id, "run_id is missing")
    require(isinstance(events_url, str) and events_url, "events_url is missing")

    negative_checks = negative_event_urls(events_url, run_id=run_id)
    for label, relative_url, expected_status in negative_checks:
        negative_status, negative_type, negative_body = fetch(
            urljoin(base_url, relative_url),
            accepts="text/event-stream",
            timeout_seconds=args.timeout_seconds,
            return_http_error=True,
        )
        verify_rejected_stream(
            negative_status,
            negative_type,
            negative_body,
            label=label,
            expected_status=expected_status,
        )

    status, content_type, body = fetch(
        urljoin(base_url, events_url),
        accepts="text/event-stream",
        timeout_seconds=args.timeout_seconds,
    )
    require(status == 200, f"events returned status {status}")
    require(content_type == "text/event-stream", f"events returned {content_type}")
    evidence = verify_trace(
        parse_sse(body),
        run_id=run_id,
        expected_mode=args.expect_mode,
        expected_source=args.expect_source,
        expected_terminal=args.expect_terminal,
        expected_adapter=args.expect_adapter,
    )

    print(
        json.dumps(
            {
                "passed": True,
                "base_url": args.base_url.rstrip("/"),
                "health_mode": health.get("mode"),
                "run_context_negative_checks": len(negative_checks),
                "run_context_secret_configured": health.get("run_context_secret_configured"),
                "run_mode": run.get("mode"),
                "run_id": run_id,
                **evidence,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(
            json.dumps({"passed": False, "error": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
