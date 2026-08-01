from __future__ import annotations

import base64
import json
from hashlib import sha256
from pathlib import Path

import pytest

from agent24.agent.manifest import MalformedManifestError
from agent24.agent.models import ExperimentPlan, StopDecision
from agent24.agent.participant_intake import (
    CompatibilityStatus,
    ParticipantCompatibilityResult,
    git_blob_sha,
)
from agent24.agent.source import MappingRevisionResolver, SourceDescriptor
from agent24.api.preflight import (
    MAX_ENTRYPOINT_BYTES,
    ExternalAgentPreflight,
    ExternalTarget,
    GitHubContentsManifestFetcher,
    GitHubContentsSourceFetcher,
    MappingManifestFetcher,
    MappingSourceFileFetcher,
    SourceFileFetchError,
)

SHA = "0123456789abcdef0123456789abcdef01234567"
BUNDLE_SHA = "0123456789abcdef" * 4
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
    assert result.compatibility_selection.pack_id == "life-v0-sandbox.v1"
    assert result.compatibility_selection.max_experiments == 0
    assert result.compatibility_selection.fixture_id is None
    assert result.pack_selection.pack_id == "life-v0-sandbox.v1"
    assert result.pack_selection.executed_by_controller is True
    assert isinstance(result.decision, ExperimentPlan)
    assert result.decision.scenario.faults[0].fault == "commit_then_timeout"
    assert result.decision.scenario.faults[0].target_tool == "payment.charge"
    assert "확인된 결함이 아니라" in result.decision.tool_choice_reason


def test_preflight_stops_when_prompt_contract_exceeds_manifest_ceiling() -> None:
    target = TARGET.model_copy(
        update={"mission": "엄마 생일 케이크 하나를 5만원 이하로 두 번 주문해."}
    )

    result = preflight_for(manifest_json()).run(target)

    assert isinstance(result.decision, StopDecision)
    assert result.decision.reason == "unsupported_input"
    assert "requested_total_budget=100000" in result.decision.detail
    contract = result.mission.constraints["prompt_contract"]
    assert contract["order_count"] == 2
    assert contract["status"] == "permission_conflict"


def test_preflight_stops_honestly_when_manifest_tools_are_unsupported() -> None:
    result = preflight_for(manifest_json(supported=False)).run(TARGET)

    assert result.manifest.unsupported_tools == ("unsupported:vendor.transfer",)
    assert isinstance(result.decision, StopDecision)
    assert result.decision.reason == "unsupported_input"
    # Since #57 the domain-pack router answers first, so the stop names the
    # routing failure rather than the Life-v0 operator table.  A tool the
    # manifest loader rejected is not handed to the Adhoc fallback either.
    assert "지원 어휘 밖" in result.decision.detail
    assert result.pack_selection.selected is None
    assert result.pack_selection.candidates == ()


def test_mapping_fetcher_uses_allowlist_order_and_is_deterministic() -> None:
    first = preflight_for(manifest_json()).run(TARGET)
    second = preflight_for(manifest_json()).run(TARGET)

    assert first.model_dump_json() == second.model_dump_json()


def test_preflight_records_bounded_entrypoint_without_executing_it() -> None:
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/cake-agent", "main"): SHA}
        ),
        manifest_fetcher=MappingManifestFetcher(
            {".agent24/manifest.json": manifest_json()}
        ),
        source_file_fetcher=MappingSourceFileFetcher(
            {"agent/main.py": "raise RuntimeError('must not execute')"}
        ),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )

    result = preflight.run(TARGET)

    assert result.source_snapshot.execution_scope == "manifest_and_entrypoint"
    assert [file.path for file in result.source_snapshot.files] == [
        ".agent24/manifest.json",
        "agent/main.py",
    ]
    assert result.source_snapshot.files[1].retrieval_mode == "bounded_download"
    assert result.source_snapshot.files[1].content_sha256 is not None


def test_local_demo_bundle_preflight_preserves_sha_path_and_content_hash_evidence() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    manifest_bytes = (
        repository_root / "examples/demo-agent-repo/.agent24/manifest.json"
    ).read_bytes()
    entrypoint_bytes = (
        repository_root / "examples/demo-agent-repo/src/example_agent.py"
    ).read_bytes()
    target = ExternalTarget(
        repository_url="local://agent24/examples/demo-agent-repo",
        requested_ref=BUNDLE_SHA,
        mission="Order one birthday cake under budget.",
    )
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("local/demo-agent-repo", BUNDLE_SHA): BUNDLE_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest_bytes}),
        source_file_fetcher=MappingSourceFileFetcher({"src/example_agent.py": entrypoint_bytes}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )

    result = preflight.run(target)

    assert result.source.source_kind == "local_bundle"
    assert result.source.source_path == "examples/demo-agent-repo"
    assert result.source.resolved_sha == result.source.bundle_sha256
    assert result.source.revision_kind == "bundle_sha256"
    assert result.manifest.runtime_contract["id"] == "agent24.sandboxgym.life.v1"
    assert result.manifest.python_version == ">=3.11,<3.14"
    files = {item.path: item for item in result.source_snapshot.files}
    assert files[".agent24/manifest.json"].blob_sha == git_blob_sha(manifest_bytes)
    assert files["src/example_agent.py"].blob_sha == git_blob_sha(entrypoint_bytes)
    assert files[".agent24/manifest.json"].content_sha256 == (
        f"sha256:{sha256(manifest_bytes).hexdigest()}"
    )
    assert files["src/example_agent.py"].content_sha256 == (
        f"sha256:{sha256(entrypoint_bytes).hexdigest()}"
    )


def test_local_demo_bundle_schema_mismatch_fails_closed() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = json.loads(
        (repository_root / "examples/demo-agent-repo/.agent24/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    raw["tools"][0]["input_schema"]["required"] = ["query"]
    target = ExternalTarget(
        repository_url="local://agent24/examples/demo-agent-repo",
        requested_ref=BUNDLE_SHA,
        mission="Order one birthday cake under budget.",
    )
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("local/demo-agent-repo", BUNDLE_SHA): BUNDLE_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher(
            {".agent24/manifest.json": json.dumps(raw)}
        ),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )

    with pytest.raises(MalformedManifestError, match="schema mismatch"):
        preflight.run(target)


@pytest.mark.parametrize(
    ("section", "field", "value", "message"),
    [
        ("runtime_contract", "id", "unreviewed.runtime.v1", "id is not supported"),
        ("permissions", "network_access", "enabled", "permissions do not match"),
        ("tools", 1, {"side_effect": False}, "safety metadata mismatch"),
    ],
)
def test_local_demo_bundle_runtime_and_safety_drift_fail_closed(
    section: str,
    field: str | int,
    value: object,
    message: str,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    raw = json.loads(
        (repository_root / "examples/demo-agent-repo/.agent24/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    if section == "tools":
        raw[section][field].update(value)
    else:
        raw[section][field] = value
    target = ExternalTarget(
        repository_url="local://agent24/examples/demo-agent-repo",
        requested_ref=BUNDLE_SHA,
        mission="Order one birthday cake under budget.",
    )
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("local/demo-agent-repo", BUNDLE_SHA): BUNDLE_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher(
            {".agent24/manifest.json": json.dumps(raw)}
        ),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )

    with pytest.raises(MalformedManifestError, match=message):
        preflight.run(target)


def test_absent_manifest_returns_honest_unsupported_compatibility() -> None:
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/cake-agent", "main"): SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({"README.md": "not a manifest"}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )

    result = preflight.run(TARGET)

    assert isinstance(result, ParticipantCompatibilityResult)
    assert result.target_profile.status == CompatibilityStatus.UNSUPPORTED
    assert result.target_profile.provenance.evidence == ()
    assert result.pack_selection.max_experiments == 0
    assert result.compatibility_report.findings == ()


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


def test_github_source_fetcher_reads_pinned_entrypoint_and_enforces_bound(
    monkeypatch,
) -> None:
    entrypoint_bytes = b"raise RuntimeError('never execute this source')\n"
    response_body = json.dumps(
        {
            "encoding": "base64",
            "content": base64.encodebytes(entrypoint_bytes).decode("ascii"),
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
        captured["url"] = request.full_url
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

    fetched = GitHubContentsSourceFetcher(
        token="test-token",
        timeout_seconds=4.0,
    ).fetch(source, "agent/main.py")

    assert fetched == entrypoint_bytes
    assert captured == {
        "url": f"https://api.github.com/repos/example/cake-agent/contents/agent/main.py?ref={SHA}",
        "authorization": "Bearer test-token",
        "timeout": 4.0,
    }

    oversized = b"x" * (MAX_ENTRYPOINT_BYTES + 1)
    oversized_body = json.dumps(
        {
            "encoding": "base64",
            "content": base64.b64encode(oversized).decode("ascii"),
        }
    ).encode("utf-8")
    response_body = oversized_body
    with pytest.raises(SourceFileFetchError, match="bounded intake limit"):
        GitHubContentsSourceFetcher(token="test-token").fetch(source, "agent/main.py")
