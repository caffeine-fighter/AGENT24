from __future__ import annotations

import base64
import json

from agent24.agent.models import ExperimentPlan, StopDecision
from agent24.agent.source import MappingRevisionResolver, SourceDescriptor
from agent24.api.preflight import (
    ExternalAgentPreflight,
    ExternalTarget,
    GitHubContentsManifestFetcher,
    ManifestUnavailableError,
    MappingManifestFetcher,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
TARGET = ExternalTarget(
    repository_url="https://github.com/example/cake-agent",
    requested_ref="main",
    mission="5만원 이하 케이크 하나를 주문해줘.",
)


def manifest_json(*, supported: bool = True) -> str:
    tools = (
        [
            {"name": "catalog.search"},
            {"name": "payment.charge", "side_effect": True, "irreversible": True},
            {"name": "payment.status"},
        ]
        if supported
        else [{"name": "vendor.transfer", "side_effect": True, "irreversible": True}]
    )
    return json.dumps(
        {
            "name": "cake-agent",
            "entrypoint": "agent/main.py",
            "system_prompt": "Buy one cake under budget.",
            "readme_text": "Claims are not observed behavior.",
            "mission_family": "purchase",
            "permissions": {"max_spend_krw": 50_000},
            "tools": tools,
        }
    )


def preflight_for(manifest: str) -> ExternalAgentPreflight:
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/cake-agent", "main"): SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )


def test_preflight_resolves_profiles_and_selects_one_typed_experiment() -> None:
    result = preflight_for(manifest_json()).run(TARGET)

    assert result.source.resolved_sha == SHA
    assert result.source.source_ref == f"example/cake-agent@{SHA}"
    assert result.manifest.source_ref == result.source.source_ref
    assert result.manifest.manifest_hash
    assert result.mission.text == TARGET.mission
    assert result.profile.agent_name == "cake-agent"
    assert result.profile.baseline_observed is False
    assert result.profile.idempotency_usage.value == "unknown"
    assert isinstance(result.decision, ExperimentPlan)
    assert result.decision.scenario.faults[0].fault == "commit_then_timeout"
    assert result.decision.scenario.faults[0].target_tool == "payment.charge"
    assert "확인된 결함이 아니라" in result.decision.tool_choice_reason


def test_preflight_stops_honestly_when_manifest_tools_are_unsupported() -> None:
    result = preflight_for(manifest_json(supported=False)).run(TARGET)

    assert result.manifest.unsupported_tools == ("unsupported:vendor.transfer",)
    assert isinstance(result.decision, StopDecision)
    assert result.decision.reason == "unsupported_input"
    assert "gym 어휘 밖" in result.decision.detail


def test_mapping_fetcher_uses_allowlist_order_and_is_deterministic() -> None:
    first = preflight_for(manifest_json()).run(TARGET)
    second = preflight_for(manifest_json()).run(TARGET)

    assert first.model_dump_json() == second.model_dump_json()


def test_mapping_fetcher_rejects_absent_allowlisted_manifest() -> None:
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/cake-agent", "main"): SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({"README.md": "not a manifest"}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )

    try:
        preflight.run(TARGET)
    except ManifestUnavailableError as error:
        assert "allowlisted manifest" in str(error)
    else:
        raise AssertionError("missing allowlisted manifest must fail")


def test_github_fetcher_accepts_line_wrapped_base64_and_keeps_token_in_header(
    monkeypatch,
) -> None:
    manifest_bytes = manifest_json().encode("utf-8")
    response_body = json.dumps(
        {
            "encoding": "base64",
            "content": base64.encodebytes(manifest_bytes).decode("ascii"),
        }
    ).encode("utf-8")
    captured: dict[str, str | float] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return response_body

    def fake_urlopen(request, *, timeout: float):
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("agent24.api.preflight.urlopen", fake_urlopen)
    source = SourceDescriptor(
        repository="example/cake-agent",
        repository_url="https://github.com/example/cake-agent",
        source_url="https://github.com/example/cake-agent/tree/main",
        requested_ref="main",
        resolved_sha=SHA,
        retrieved_at="2026-08-01T17:45:00+09:00",
        resolver="fixture",
    )

    manifest_path, fetched = GitHubContentsManifestFetcher(
        token="test-token",
        timeout_seconds=3.0,
    ).fetch(source)

    assert manifest_path == ".agent24/manifest.json"
    assert fetched == manifest_bytes
    assert captured == {"authorization": "Bearer test-token", "timeout": 3.0}
