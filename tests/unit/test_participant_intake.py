from __future__ import annotations

import json
import socket

import pytest

from agent24.agent import (
    CREATIVE_STARTER_SHA,
    DEFAULT_PARTICIPANT_SPECS,
    LJH_E2P_SHA,
    MAX_STATIC_PROFILE_FILE_BYTES,
    PAPER_PLAYGROUND_SHA,
    CompatibilityStatus,
    GitHubEvidenceMetadataFetcher,
    MappingEvidenceMetadataFetcher,
    MappingRevisionResolver,
    ParticipantCompatibilityResult,
    ParticipantEvidenceAccessError,
    ParticipantStaticProfiler,
    ProfileOrigin,
    RepositoryEvidenceMetadata,
    SourceAccessError,
    SourceDescriptor,
    StaticEvidenceKind,
    StaticEvidenceSpec,
    StaticTargetRegistry,
    StaticTargetSpec,
    TargetDomainKind,
    git_blob_sha,
    metadata_fixture_for,
)
from agent24.agent.packs import REGISTRY_VERSION, DomainKind
from agent24.api.preflight import (
    ExternalAgentPreflight,
    ExternalPreflightResult,
    ExternalTarget,
    MappingManifestFetcher,
)

FIXED_AT = "2026-08-01T20:30:00+09:00"


def source_for(repository: str, sha: str) -> SourceDescriptor:
    return SourceDescriptor(
        repository=repository,
        repository_url=f"https://github.com/{repository}",
        source_url=f"https://github.com/{repository}/commit/{sha}",
        requested_ref=sha,
        resolved_sha=sha,
        retrieved_at=FIXED_AT,
        resolver="fixture",
    )


def checked_in_profiler(
    metadata: dict[tuple[str, str], RepositoryEvidenceMetadata] | None = None,
) -> ParticipantStaticProfiler:
    return ParticipantStaticProfiler(
        evidence_fetcher=MappingEvidenceMetadataFetcher(
            metadata_fixture_for() if metadata is None else metadata
        )
    )


@pytest.mark.parametrize(
    ("repository", "sha", "expected_name", "expected_status", "expected_domain"),
    [
        (
            "ljh8450/Agent24",
            LJH_E2P_SHA,
            "E2P Agent",
            CompatibilityStatus.COMPATIBLE_CANDIDATE,
            TargetDomainKind.RESEARCH,
        ),
        (
            "midwestchekhov/agent24",
            PAPER_PLAYGROUND_SHA,
            "Paper Playground",
            CompatibilityStatus.COMPATIBLE_CANDIDATE,
            TargetDomainKind.RESEARCH,
        ),
        (
            "youkim1028/agent24-creative-agent",
            CREATIVE_STARTER_SHA,
            "Agent24 Creative Agent Starter",
            CompatibilityStatus.UNSUPPORTED,
            None,
        ),
    ],
)
def test_three_pinned_participant_profiles_are_provenance_bearing_and_claim_bounded(
    repository: str,
    sha: str,
    expected_name: str,
    expected_status: CompatibilityStatus,
    expected_domain: TargetDomainKind | None,
) -> None:
    result = checked_in_profiler().profile(source_for(repository, sha))

    assert result.target_profile.agent_name == expected_name
    assert result.target_profile.status == expected_status
    assert result.target_profile.provenance.origin == ProfileOrigin.LAB_STATIC_PROFILE
    assert result.target_profile.provenance.resolved_sha == sha
    assert result.target_profile.provenance.public_visibility == "public"
    assert result.pack_selection.selected_domain == expected_domain
    assert result.pack_selection.registry_version == REGISTRY_VERSION
    assert result.pack_selection.pack_id == (
        "research-agent-pack.v1" if expected_domain is TargetDomainKind.RESEARCH else None
    )
    assert result.pack_selection.fixture_id is None
    assert result.pack_selection.max_experiments == 0
    assert result.compatibility_report.selected_domain == expected_domain
    assert result.compatibility_report.experiments_run == 0
    assert result.compatibility_report.findings == ()
    assert result.target_profile.failure_claims == ()
    assert "no vulnerability" in result.compatibility_report.claim_boundary
    assert all(
        evidence.resolved_sha == sha
        and evidence.stable_ref.endswith(f"@{evidence.blob_sha}#{evidence.line_selector}")
        for evidence in result.target_profile.provenance.evidence
    )


def test_participant_domain_kind_reuses_the_canonical_router_enum() -> None:
    assert TargetDomainKind is DomainKind


def test_two_research_like_profiles_have_distinct_pinned_evidence() -> None:
    profiles = [
        checked_in_profiler().profile(source_for(spec.repository, spec.resolved_sha))
        for spec in DEFAULT_PARTICIPANT_SPECS[:2]
    ]

    assert [result.pack_selection.selected_domain for result in profiles] == [
        TargetDomainKind.RESEARCH,
        TargetDomainKind.RESEARCH,
    ]
    assert all(len(result.pack_selection.evidence_refs) == 2 for result in profiles)
    assert profiles[0].pack_selection.evidence_refs != profiles[1].pack_selection.evidence_refs
    assert all(result.compatibility_report.findings == () for result in profiles)


def test_static_profile_and_selection_are_byte_stable_for_the_same_source() -> None:
    source = source_for("ljh8450/Agent24", LJH_E2P_SHA)
    first = checked_in_profiler().profile(source)
    second = checked_in_profiler().profile(source)
    third = checked_in_profiler().profile(source)

    assert first.canonical_json() == second.canonical_json() == third.canonical_json()
    assert (
        len(
            {
                first.target_profile.profile_digest,
                second.target_profile.profile_digest,
                third.target_profile.profile_digest,
            }
        )
        == 1
    )
    assert (
        len(
            {
                first.pack_selection.selection_digest,
                second.pack_selection.selection_digest,
                third.pack_selection.selection_digest,
            }
        )
        == 1
    )
    later = checked_in_profiler().profile(
        source.model_copy(update={"retrieved_at": "2026-08-01T21:30:00+09:00"})
    )
    assert (
        later.target_profile.provenance.generated_at != first.target_profile.provenance.generated_at
    )
    assert later.target_profile.profile_digest == first.target_profile.profile_digest
    assert later.pack_selection.selection_digest == first.pack_selection.selection_digest


def test_owner_manifest_takes_priority_over_registered_lab_profile() -> None:
    manifest = json.dumps(
        {
            "name": "owner-e2p",
            "entrypoint": "agent/main.py",
            "mission_family": "research",
            "tools": [
                {"name": "research.search"},
                {"name": "citation.resolve"},
            ],
        },
        separators=(",", ":"),
    )

    class StaticFetcherMustNotRun:
        def stat(self, source: SourceDescriptor, path: str) -> RepositoryEvidenceMetadata:
            del source, path
            raise AssertionError("owner manifest must bypass the lab static profile")

    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver({("ljh8450/Agent24", "main"): LJH_E2P_SHA}),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest}),
        static_profiler=ParticipantStaticProfiler(evidence_fetcher=StaticFetcherMustNotRun()),
        retrieved_at=FIXED_AT,
    )
    result = preflight.run(
        ExternalTarget(
            repository_url="https://github.com/ljh8450/Agent24",
            requested_ref="main",
            mission="근거가 있는 연구 결과를 정리해줘.",
        )
    )

    assert isinstance(result, ExternalPreflightResult)
    assert result.target_profile.profile_label == "OWNER MANIFEST"
    assert result.target_profile.provenance.origin == ProfileOrigin.OWNER_MANIFEST
    assert result.target_profile.provenance.reviewed_at is None
    assert result.target_profile.provenance.evidence[0].blob_sha == git_blob_sha(manifest.encode())
    assert result.compatibility_selection.selected_domain == TargetDomainKind.RESEARCH
    assert result.compatibility_selection.max_experiments == 0
    assert result.pack_selection.selected is not None
    assert result.pack_selection.selected.domain_kind == TargetDomainKind.RESEARCH
    assert result.target_profile.declared_capabilities == (
        "citation.resolve",
        "research.search",
    )


def test_missing_owner_manifest_uses_the_exact_registered_static_profile() -> None:
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("midwestchekhov/agent24", "main"): PAPER_PLAYGROUND_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({}),
        static_profiler=checked_in_profiler(),
        retrieved_at=FIXED_AT,
    )

    result = preflight.run(
        ExternalTarget(
            repository_url="https://github.com/midwestchekhov/agent24",
            requested_ref="main",
            mission="논문 주장의 근거를 확인해줘.",
        )
    )

    assert isinstance(result, ParticipantCompatibilityResult)
    assert result.target_profile.profile_label == "LAB-INFERRED STATIC PROFILE"
    assert result.pack_selection.selected_domain == TargetDomainKind.RESEARCH
    assert result.compatibility_report.experiments_run == 0


def test_branch_drift_never_reuses_an_older_static_profile() -> None:
    drifted = source_for("ljh8450/Agent24", "a" * 40)

    result = checked_in_profiler().profile(drifted)

    assert result.target_profile.status == CompatibilityStatus.UNSUPPORTED
    assert result.target_profile.provenance.evidence == ()
    assert result.target_profile.domain_candidates == ()
    assert result.target_profile.unsupported_fields == ("pinned_profile_not_registered",)
    assert result.compatibility_report.findings == ()


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "static_evidence_missing"),
        ("drift", "static_evidence_drift"),
        ("oversized", "static_evidence_policy_rejected"),
        ("symlink", "static_evidence_policy_rejected"),
        ("submodule", "static_evidence_policy_rejected"),
    ],
)
def test_invalid_static_evidence_terminates_without_reusing_profile(
    mutation: str,
    reason: str,
) -> None:
    source = source_for("ljh8450/Agent24", LJH_E2P_SHA)
    metadata = metadata_fixture_for()
    key = (source.source_ref, "README.md")
    original = metadata[key]
    if mutation == "missing":
        del metadata[key]
    elif mutation == "drift":
        metadata[key] = original.model_copy(update={"blob_sha": "b" * 40})
    elif mutation == "oversized":
        metadata[key] = original.model_copy(update={"size": MAX_STATIC_PROFILE_FILE_BYTES + 1})
    else:
        metadata[key] = original.model_copy(update={"object_type": mutation})

    result = checked_in_profiler(metadata).profile(source)

    assert result.target_profile.status == CompatibilityStatus.UNSUPPORTED
    assert result.target_profile.provenance.evidence == ()
    assert result.target_profile.domain_candidates == ()
    assert result.target_profile.unsupported_fields == (reason,)
    assert result.pack_selection.max_experiments == 0
    assert result.compatibility_report.findings == ()


def test_static_evidence_access_error_is_sanitized_to_unsupported() -> None:
    class FailingFetcher:
        def stat(self, source: SourceDescriptor, path: str) -> RepositoryEvidenceMetadata:
            del source, path
            raise ParticipantEvidenceAccessError("secret transport detail")

    profiler = ParticipantStaticProfiler(evidence_fetcher=FailingFetcher())
    result = profiler.profile(source_for("ljh8450/Agent24", LJH_E2P_SHA))

    rendered = result.model_dump_json()
    assert result.target_profile.unsupported_fields == ("static_evidence_unavailable",)
    assert "secret transport detail" not in rendered


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../README.md",
        "/README.md",
        ".env",
        ".env.example",
        "keys/private.pem",
        "docs/credentials.json",
        "README.exe",
        "docs//architecture.md",
    ],
)
def test_static_evidence_spec_rejects_traversal_secret_and_non_text_paths(
    unsafe_path: str,
) -> None:
    with pytest.raises(ValueError):
        StaticEvidenceSpec(
            evidence_id="unsafe",
            kind=StaticEvidenceKind.ARCHITECTURE,
            path=unsafe_path,
            expected_blob_sha="a" * 40,
            expected_size=1,
            line_selector="L1-L1",
            note="must be rejected",
        )


def test_static_profile_file_count_budget_is_enforced_before_fetch() -> None:
    source = source_for("example/many-files", "c" * 40)
    evidence = tuple(
        StaticEvidenceSpec(
            evidence_id=f"evidence-{index}",
            kind=StaticEvidenceKind.ARCHITECTURE,
            path=f"docs/evidence-{index}.md",
            expected_blob_sha=f"{index + 1:x}" * 40,
            expected_size=1,
            line_selector="L1-L1",
            note="bounded evidence",
        )
        for index in range(5)
    )
    registry = StaticTargetRegistry(
        (
            StaticTargetSpec(
                repository=source.repository,
                resolved_sha=source.resolved_sha,
                agent_name="too-many-files",
                reviewed_at=FIXED_AT,
                evidence=evidence,
            ),
        )
    )

    class FetcherMustNotRun:
        def stat(self, source: SourceDescriptor, path: str) -> RepositoryEvidenceMetadata:
            del source, path
            raise AssertionError("file count budget must be checked before metadata calls")

    result = ParticipantStaticProfiler(
        registry=registry,
        evidence_fetcher=FetcherMustNotRun(),
    ).profile(source)

    assert result.target_profile.unsupported_fields == ("static_evidence_file_budget_exceeded",)


def test_github_metadata_fetcher_reads_only_git_tree_metadata(monkeypatch) -> None:
    source = source_for("ljh8450/Agent24", LJH_E2P_SHA)
    captured: dict[str, object] = {}
    body = json.dumps(
        {
            "sha": LJH_E2P_SHA,
            "truncated": False,
            "tree": [
                {
                    "path": "README.md",
                    "mode": "100644",
                    "type": "blob",
                    "sha": "9aa8bfcdabd8fcc11fbb7c1b35431dd3498223f9",
                    "size": 3394,
                }
            ],
        }
    ).encode()

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit: int) -> bytes:
            captured["read_limit"] = limit
            return body

    def fake_urlopen(request, *, timeout: float):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("agent24.agent.participant_intake.urlopen", fake_urlopen)
    metadata = GitHubEvidenceMetadataFetcher(
        token="test-token",
        timeout_seconds=3.0,
    ).stat(source, "README.md")

    assert metadata == RepositoryEvidenceMetadata(
        path="README.md",
        blob_sha="9aa8bfcdabd8fcc11fbb7c1b35431dd3498223f9",
        size=3394,
        object_type="file",
    )
    assert str(captured["url"]).endswith(f"/git/trees/{LJH_E2P_SHA}")
    assert "/contents/" not in str(captured["url"])
    assert captured["authorization"] == "Bearer test-token"
    assert captured["timeout"] == 3.0
    assert captured["read_limit"] > len(body)


def test_github_metadata_fetcher_traverses_only_named_trees_and_marks_symlinks(
    monkeypatch,
) -> None:
    source = source_for("example/nested", "d" * 40)
    docs_tree_sha = "e" * 40
    requested: list[str] = []
    payloads = {
        source.resolved_sha: {
            "sha": source.resolved_sha,
            "truncated": False,
            "tree": [
                {
                    "path": "docs",
                    "mode": "040000",
                    "type": "tree",
                    "sha": docs_tree_sha,
                }
            ],
        },
        docs_tree_sha: {
            "sha": docs_tree_sha,
            "truncated": False,
            "tree": [
                {
                    "path": "architecture.md",
                    "mode": "120000",
                    "type": "blob",
                    "sha": "f" * 40,
                    "size": 18,
                }
            ],
        },
    }

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit: int) -> bytes:
            return self.body

    def fake_urlopen(request, *, timeout: float):
        assert timeout == 5.0
        tree_sha = request.full_url.rsplit("/", 1)[-1]
        requested.append(tree_sha)
        return Response(json.dumps(payloads[tree_sha]).encode())

    monkeypatch.setattr("agent24.agent.participant_intake.urlopen", fake_urlopen)
    metadata = GitHubEvidenceMetadataFetcher().stat(source, "docs/architecture.md")

    assert requested == [source.resolved_sha, docs_tree_sha]
    assert metadata.object_type == "symlink"
    assert metadata.path == "docs/architecture.md"


def test_empty_or_unresolved_repository_never_produces_a_profile() -> None:
    preflight = ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver({}),
        manifest_fetcher=MappingManifestFetcher({}),
        static_profiler=checked_in_profiler(),
        retrieved_at=FIXED_AT,
    )

    with pytest.raises(SourceAccessError, match="no revision"):
        preflight.run(
            ExternalTarget(
                repository_url="https://github.com/kitavkdl/developAgent",
                requested_ref="main",
                mission="이 저장소를 분석해줘.",
            )
        )


def test_newly_initialized_but_unreviewed_repository_is_unsupported() -> None:
    source = source_for(
        "kitavkdl/developAgent",
        "87649e99abf99525b83da14176e4ba16d6ab2d14",
    )

    result = checked_in_profiler().profile(source)

    assert result.target_profile.status == CompatibilityStatus.UNSUPPORTED
    assert result.target_profile.provenance.evidence == ()
    assert result.target_profile.unsupported_fields == ("pinned_profile_not_registered",)
    assert result.compatibility_report.findings == ()


def test_checked_in_intake_never_needs_network_or_exec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline participant intake attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    result = checked_in_profiler().profile(
        source_for("midwestchekhov/agent24", PAPER_PLAYGROUND_SHA)
    )

    assert result.pack_selection.selected_domain == TargetDomainKind.RESEARCH
    assert result.compatibility_report.experiments_run == 0
