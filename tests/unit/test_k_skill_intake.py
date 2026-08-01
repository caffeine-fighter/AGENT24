"""Metadata-only K-Skill risk intake contract for issue #61."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from agent24.agent.k_skill_intake import (
    K_SKILL_CATALOG_ENTRY_COUNT,
    K_SKILL_CATALOG_MANIFEST_BLOB_SHA,
    K_SKILL_CATALOG_MANIFEST_PATH,
    K_SKILL_CATALOG_MANIFEST_SIZE,
    K_SKILL_CLAIM_BOUNDARY,
    K_SKILL_PINNED_SHA,
    K_SKILL_PINNED_TREE_SHA,
    K_SKILL_SHORTLIST,
    CatalogSnapshotError,
    KSkillCatalogSnapshot,
    KSkillIntakeResult,
    KSkillRiskProfileAdapter,
    RiskHypothesisFamily,
    RiskIntakeReason,
    RiskIntakeStatus,
    SideEffectClass,
    k_skill_ids_from_manifest,
    load_k_skill_catalog,
    metadata_fixture_for_catalog,
    rebuild_k_skill_catalog_snapshot,
)
from agent24.agent.packs import DomainKind
from agent24.agent.participant_intake import (
    MappingEvidenceMetadataFetcher,
    RepositoryEvidenceMetadata,
)
from agent24.agent.source import SourceDescriptor

UPSTREAM_CATALOG_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "k_skill_plugin_v1.json"
)

EXPECTED_EVIDENCE = {
    "catchtable-sniper": (
        ("manifest", "29faa1b286496a3685d7c6e979da4b72945ec484", 690),
        ("instruction", "38c5dca8e164f6b9395457276a52e51c3788cc6d", 8259),
    ),
    "court-payment-order-assistant": (
        ("manifest", "84a620a029374caabb5b0d5ff15c910ab0f57c5c", 883),
        ("instruction", "781d466e35fc380fd64e5cd38d4c58f1480e744a", 4926),
    ),
    "express-bus-booking": (
        ("manifest", "3bfaf5d82d726155b631500e80cce99d08202c24", 803),
        ("instruction", "c77877e3d8ef3447db3e38aedb962f5adb58c9e4", 8160),
    ),
    "intercity-bus-booking": (
        ("manifest", "36d1aa85a09dc4d2973494a253a25e523b6b3044", 793),
        ("instruction", "fc43d8bb148e53b7b332767679d688c49ea60fad", 7899),
    ),
    "iros-registry-automation": (
        ("manifest", "8bfe8d917bfa43a4a2eed27bf4ec3d552fe72810", 927),
        ("instruction", "9dc6fbd8a295784cf21fdf4b07c48ea0d69574aa", 13592),
    ),
    "k-skill-cleaner": (
        ("manifest", "020570587243d9f49133ee3ddec28318bf13586d", 355),
        ("instruction", "995c34a739ba3af039c60ee82bf3f1f4cfe24aeb", 4703),
    ),
    "kakaotalk-mac": (
        ("manifest", "46ced577cec3dee271460f36d86bdfe784acb329", 520),
        ("instruction", "f29d382be75abed54282a6c2d0c43fa9781e4695", 6512),
    ),
    "keris-academic-search": (
        ("manifest", "d2da2e6d3cd993b70c2a2a61c41226e0e85f9736", 721),
        ("instruction", "82c43c3b40a8eb7644160d28550bbc5a6eb7455c", 5167),
    ),
    "korean-stock-search": (
        ("manifest", "7d3a3de6107217e25ac0ced4f6350d7786bdf873", 559),
        ("instruction", "87889ba57d492e00ca37db8ee59834138440c679", 6441),
    ),
    "ktx-booking": (
        ("manifest", "e83345d60994876cca715e76d3b326b1df7d639e", 1063),
        ("instruction", "d54b290ab5204e6667b69a67aec7602775c26509", 11375),
    ),
    "lovebug-report": (
        ("manifest", "c16e9a8a65528a3f61fd0b38cf8848315ea77a4d", 829),
        ("instruction", "279f5e4d50b5cc1be0930ac4618cbe0680b5d239", 6006),
    ),
    "popbill": (
        ("manifest", "ea517a5052784676c8356708e2a15dc147853a9e", 793),
        ("instruction", "542d244ca98537fb9b21bfd55ab51264d3e49f58", 7198),
    ),
    "srt-booking": (
        ("manifest", "ea294dc9a89677e02314bf51eb80772942be1e26", 945),
        ("instruction", "b84dc4ff4fd000883745180e88e0e0063cd03c9e", 7187),
    ),
}


def pinned_source(sha: str = K_SKILL_PINNED_SHA) -> SourceDescriptor:
    return SourceDescriptor(
        repository="NomaDamas/k-skill",
        repository_url="https://github.com/NomaDamas/k-skill",
        source_url=f"https://github.com/NomaDamas/k-skill/commit/{sha}",
        requested_ref=sha,
        resolved_sha=sha,
        retrieved_at="2026-08-01T13:30:00+09:00",
        resolver="fixture",
    )


def offline_adapter() -> KSkillRiskProfileAdapter:
    catalog = load_k_skill_catalog()
    return KSkillRiskProfileAdapter(
        catalog=catalog,
        evidence_fetcher=MappingEvidenceMetadataFetcher(
            metadata_fixture_for_catalog(catalog)
        ),
    )


def commit_fixture() -> dict[str, object]:
    return {
        "sha": K_SKILL_PINNED_SHA,
        "tree": {"sha": K_SKILL_PINNED_TREE_SHA},
        "committer": {"date": "2026-08-01T04:36:55Z"},
    }


def reviewed_tree_fixture() -> dict[str, object]:
    catalog = load_k_skill_catalog()
    catalog_manifest = catalog.source.catalog_manifest
    return {
        "sha": K_SKILL_PINNED_TREE_SHA,
        "truncated": False,
        "tree": [
            {
                "path": catalog_manifest.path,
                "mode": "100644",
                "type": "blob",
                "sha": catalog_manifest.blob_sha,
                "size": catalog_manifest.size,
            },
            *[
                {
                    "path": evidence.path,
                    "mode": "100644",
                    "type": "blob",
                    "sha": evidence.blob_sha,
                    "size": evidence.size,
                }
                for profile in catalog.profiles
                for evidence in profile.evidence
            ],
        ],
    }


def test_checked_in_catalog_covers_the_p0_shortlist_with_exact_provenance() -> None:
    catalog = load_k_skill_catalog()

    assert catalog.source.repository == "NomaDamas/k-skill"
    assert catalog.source.commit_sha == K_SKILL_PINNED_SHA
    assert catalog.source.tree_sha == K_SKILL_PINNED_TREE_SHA
    assert catalog.source.catalog_manifest.path == K_SKILL_CATALOG_MANIFEST_PATH
    assert catalog.source.catalog_manifest.blob_sha == K_SKILL_CATALOG_MANIFEST_BLOB_SHA
    assert catalog.source.catalog_manifest.size == K_SKILL_CATALOG_MANIFEST_SIZE
    assert catalog.source.catalog_manifest.entry_count == K_SKILL_CATALOG_ENTRY_COUNT
    catalog_members = k_skill_ids_from_manifest(UPSTREAM_CATALOG_FIXTURE.read_bytes())
    assert len(catalog_members) == K_SKILL_CATALOG_ENTRY_COUNT
    assert K_SKILL_SHORTLIST <= frozenset(catalog_members)
    assert tuple(profile.skill_id for profile in catalog.profiles) == tuple(
        sorted(K_SKILL_SHORTLIST)
    )
    assert len(catalog.profiles) == 13
    assert {
        profile.skill_id: tuple(
            (item.kind.value, item.blob_sha, item.size) for item in profile.evidence
        )
        for profile in catalog.profiles
    } == EXPECTED_EVIDENCE
    assert all(profile.description for profile in catalog.profiles)
    assert all(profile.declared_tools for profile in catalog.profiles)
    assert catalog.profile("court-payment-order-assistant").declared_tools == (
        "court_payment_order_helper",
        "browseros_cdp",
        "manual_browser",
    )
    assert all(
        tuple(item.kind for item in profile.evidence) == ("manifest", "instruction")
        for profile in catalog.profiles
    )
    assert all(
        item.path in {f"{profile.skill_id}/skill.json", f"{profile.skill_id}/instruction.md"}
        for profile in catalog.profiles
        for item in profile.evidence
    )
    assert "SKILL.md" not in catalog.canonical_json()
    assert "KSKILL_" not in catalog.canonical_json()


def test_adapter_returns_declared_surface_without_authorizing_execution() -> None:
    result = offline_adapter().assess(pinned_source(), "srt-booking")

    assert result.status == RiskIntakeStatus.PROFILED
    assert result.reason is None
    assert result.profile is not None
    assert result.profile.side_effect == SideEffectClass.IRREVERSIBLE
    assert result.profile.applicable_domains == (DomainKind.TICKET,)
    assert result.source_snapshot.mode == "metadata_only"
    assert result.source_snapshot.execution_scope == "static_metadata_only"
    assert tuple(item.path for item in result.source_snapshot.files) == (
        ".claude-plugin/plugin.json",
        "srt-booking/instruction.md",
        "srt-booking/skill.json",
    )
    assert all(item.content_sha256 is None for item in result.source_snapshot.files)
    assert result.execution_authorized is False
    assert result.max_experiments == 0
    assert result.failure_claims == ()
    assert result.observation_status == "not_executed"


def test_changed_commit_stops_before_any_metadata_lookup() -> None:
    class MetadataMustNotRun:
        def stat(self, source: SourceDescriptor, path: str) -> NoReturn:
            raise AssertionError("a changed commit must not reuse pinned metadata")

    adapter = KSkillRiskProfileAdapter(
        catalog=load_k_skill_catalog(),
        evidence_fetcher=MetadataMustNotRun(),
    )

    result = adapter.assess(pinned_source("1" * 40), "srt-booking")

    assert result.status == RiskIntakeStatus.UNSUPPORTED
    assert result.reason == "catalog_revision_not_registered"
    assert result.profile is None
    assert result.source_snapshot.execution_scope == "none"
    assert result.failure_claims == ()


def test_catalog_drift_returns_typed_unsupported_without_a_finding() -> None:
    catalog = load_k_skill_catalog()
    metadata = metadata_fixture_for_catalog(catalog)
    profile = catalog.profile("srt-booking")
    evidence = catalog.source.catalog_manifest
    metadata[(pinned_source().source_ref, evidence.path)] = RepositoryEvidenceMetadata(
        path=evidence.path,
        blob_sha="0" * 40,
        size=evidence.size,
        object_type="file",
    )
    adapter = KSkillRiskProfileAdapter(
        catalog=catalog,
        evidence_fetcher=MappingEvidenceMetadataFetcher(metadata),
    )

    result = adapter.assess(pinned_source(), profile.skill_id)

    assert result.status == RiskIntakeStatus.UNSUPPORTED
    assert result.reason == "catalog_metadata_drift"
    assert result.profile is None
    assert result.max_experiments == 0
    assert result.failure_claims == ()


def test_snapshot_builder_binds_catalog_bytes_to_the_tree_blob() -> None:
    catalog = load_k_skill_catalog()
    tampered = UPSTREAM_CATALOG_FIXTURE.read_bytes().replace(
        b'"./srt-booking"',
        b'"./zzz-booking"',
    )

    with pytest.raises(CatalogSnapshotError, match="catalog metadata differs"):
        rebuild_k_skill_catalog_snapshot(
            catalog,
            reviewed_tree_fixture(),
            commit_payload=commit_fixture(),
            upstream_catalog_manifest=tampered,
        )


def test_missing_catalog_path_returns_typed_unsupported() -> None:
    catalog = load_k_skill_catalog()
    metadata = metadata_fixture_for_catalog(catalog)
    profile = catalog.profile("popbill")
    metadata.pop((pinned_source().source_ref, profile.evidence[1].path))
    adapter = KSkillRiskProfileAdapter(
        catalog=catalog,
        evidence_fetcher=MappingEvidenceMetadataFetcher(metadata),
    )

    result = adapter.assess(pinned_source(), profile.skill_id)

    assert result.status == RiskIntakeStatus.UNSUPPORTED
    assert result.reason == "catalog_path_missing"
    assert result.source_snapshot.files == ()
    assert result.failure_claims == ()


def test_unknown_skill_never_defaults_to_a_safe_or_executable_profile() -> None:
    result = offline_adapter().assess(pinned_source(), "not-in-the-pinned-catalog")

    assert result.status == RiskIntakeStatus.UNSUPPORTED
    assert result.reason == "unknown_skill"
    assert result.profile is None
    assert result.execution_authorized is False
    assert result.max_experiments == 0
    assert result.source_snapshot.execution_scope == "none"


def test_invalid_skill_id_is_sanitized_before_it_reaches_a_result() -> None:
    result = offline_adapter().assess(pinned_source(), "bad\nskill=secret")

    assert result.reason == RiskIntakeReason.INVALID_SKILL_ID
    assert result.skill_id == "invalid-skill-id"
    assert "secret" not in result.model_dump_json()
    assert result.source_snapshot.execution_scope == "none"


def test_no_side_effect_skill_does_not_gain_a_mutation_fault_hypothesis() -> None:
    result = offline_adapter().assess(pinned_source(), "korean-stock-search")

    assert result.status == RiskIntakeStatus.PROFILED
    assert result.profile is not None
    assert result.profile.side_effect == SideEffectClass.READ_ONLY
    assert result.profile.applicable_domains == (DomainKind.STOCK,)
    assert RiskHypothesisFamily.APPROVAL_SCOPE not in result.profile.fault_hypotheses
    assert RiskHypothesisFamily.PARTIAL_SUCCESS_RETRY not in result.profile.fault_hypotheses
    assert (
        RiskHypothesisFamily.HOLD_CANCEL_RECONCILIATION
        not in result.profile.fault_hypotheses
    )
    assert (
        RiskHypothesisFamily.UNTRUSTED_CONTENT_PRIVILEGED_SINK
        not in result.profile.fault_hypotheses
    )
    assert result.execution_authorized is False


def test_local_archive_sync_is_reversible_and_never_claims_credentials() -> None:
    profile = load_k_skill_catalog().profile("kakaotalk-mac")

    assert profile.side_effect == SideEffectClass.REVERSIBLE
    assert profile.declared_tools == ("katok_cli",)
    assert profile.requirements.credential == "not_required"
    assert "account_session" not in profile.protected_assets


def test_claim_boundaries_cannot_be_weakened_even_with_an_existing_digest() -> None:
    catalog_payload = load_k_skill_catalog().model_dump(mode="json")
    catalog_payload["profiles"][0]["claim_boundary"] = "TARGET IS SAFE AND TESTED"

    with pytest.raises(ValueError, match="claim_boundary"):
        KSkillCatalogSnapshot.model_validate(catalog_payload)

    result_payload = offline_adapter().assess(
        pinned_source(), "srt-booking"
    ).model_dump(mode="json")
    result_payload["claim_boundary"] = "safe"
    with pytest.raises(ValueError, match="claim_boundary"):
        KSkillIntakeResult.model_validate(result_payload)

    assert result_payload["profile"]["claim_boundary"] == K_SKILL_CLAIM_BOUNDARY


def test_result_rejects_a_profile_for_a_different_skill() -> None:
    result_payload = offline_adapter().assess(
        pinned_source(), "srt-booking"
    ).model_dump(mode="json")
    result_payload["skill_id"] = "popbill"

    with pytest.raises(ValueError, match="profile skill_id"):
        KSkillIntakeResult.model_validate(result_payload)


def test_result_rejects_a_snapshot_for_a_different_source() -> None:
    result_payload = offline_adapter().assess(
        pinned_source(), "srt-booking"
    ).model_dump(mode="json")
    result_payload["source_ref"] = f"NomaDamas/k-skill@{'1' * 40}"

    with pytest.raises(ValueError, match="source_ref"):
        KSkillIntakeResult.model_validate(result_payload)


def test_profiled_result_requires_its_static_metadata_snapshot() -> None:
    result_payload = offline_adapter().assess(
        pinned_source(), "srt-booking"
    ).model_dump(mode="json")
    result_payload["source_snapshot"]["files"] = []
    result_payload["source_snapshot"]["total_bytes"] = 0
    result_payload["source_snapshot"]["execution_scope"] = "none"

    with pytest.raises(ValueError, match="profiled result snapshot"):
        KSkillIntakeResult.model_validate(result_payload)


def test_unsupported_result_cannot_carry_profiled_snapshot_evidence() -> None:
    result_payload = offline_adapter().assess(
        pinned_source(), "not-in-the-pinned-catalog"
    ).model_dump(mode="json")
    profiled_payload = offline_adapter().assess(
        pinned_source(), "srt-booking"
    ).model_dump(mode="json")
    result_payload["source_snapshot"] = profiled_payload["source_snapshot"]

    with pytest.raises(ValueError, match="unsupported result snapshot"):
        KSkillIntakeResult.model_validate(result_payload)


def test_result_rejects_a_weakened_snapshot_claim_boundary() -> None:
    result_payload = offline_adapter().assess(
        pinned_source(), "srt-booking"
    ).model_dump(mode="json")
    result_payload["source_snapshot"]["claim_boundary"] = "TARGET IS SAFE"

    with pytest.raises(ValueError, match="source snapshot claim_boundary"):
        KSkillIntakeResult.model_validate(result_payload)


def test_snapshot_builder_is_byte_stable_for_the_same_tree() -> None:
    catalog = load_k_skill_catalog()

    rebuilt = rebuild_k_skill_catalog_snapshot(
        catalog,
        reviewed_tree_fixture(),
        commit_payload=commit_fixture(),
        upstream_catalog_manifest=UPSTREAM_CATALOG_FIXTURE.read_bytes(),
    )

    assert rebuilt.canonical_json() == catalog.canonical_json()


def test_snapshot_builder_rejects_truncated_or_missing_tree_metadata() -> None:
    catalog = load_k_skill_catalog()
    truncated = reviewed_tree_fixture()
    truncated["truncated"] = True

    with pytest.raises(CatalogSnapshotError, match="truncated"):
        rebuild_k_skill_catalog_snapshot(
            catalog,
            truncated,
            commit_payload=commit_fixture(),
            upstream_catalog_manifest=UPSTREAM_CATALOG_FIXTURE.read_bytes(),
        )

    missing = reviewed_tree_fixture()
    missing["tree"] = list(missing["tree"])[1:]
    with pytest.raises(CatalogSnapshotError, match="missing"):
        rebuild_k_skill_catalog_snapshot(
            catalog,
            missing,
            commit_payload=commit_fixture(),
            upstream_catalog_manifest=UPSTREAM_CATALOG_FIXTURE.read_bytes(),
        )


def test_snapshot_builder_rejects_an_unrelated_commit_and_tree() -> None:
    catalog = load_k_skill_catalog()
    commit = commit_fixture()
    commit["tree"] = {"sha": "1" * 40}

    with pytest.raises(CatalogSnapshotError, match="does not identify"):
        rebuild_k_skill_catalog_snapshot(
            catalog,
            reviewed_tree_fixture(),
            commit_payload=commit,
            upstream_catalog_manifest=UPSTREAM_CATALOG_FIXTURE.read_bytes(),
        )


def test_snapshot_builder_never_carries_classifications_to_a_new_commit() -> None:
    catalog = load_k_skill_catalog()
    commit = commit_fixture()
    commit["sha"] = "1" * 40

    with pytest.raises(CatalogSnapshotError, match="new reviewed version"):
        rebuild_k_skill_catalog_snapshot(
            catalog,
            reviewed_tree_fixture(),
            commit_payload=commit,
            upstream_catalog_manifest=UPSTREAM_CATALOG_FIXTURE.read_bytes(),
        )
