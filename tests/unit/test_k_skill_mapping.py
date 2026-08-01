"""Reviewed K-Skill declaration to synthetic archetype mappings (issue #115)."""

from __future__ import annotations

from agent24.agent.k_skill_intake import (
    K_SKILL_PINNED_SHA,
    K_SKILL_PINNED_TREE_SHA,
    K_SKILL_SHORTLIST,
    KSkillRiskProfileAdapter,
    RiskHypothesisFamily,
    SideEffectClass,
    load_k_skill_catalog,
    metadata_fixture_for_catalog,
)
from agent24.agent.k_skill_mapping import (
    KSkillMappingReason,
    KSkillMappingStatus,
    MappingSupport,
    load_k_skill_mapping_registry,
)
from agent24.agent.packs import PACKS_BY_ID
from agent24.agent.participant_intake import MappingEvidenceMetadataFetcher
from agent24.agent.source import SourceDescriptor

DEFAULT_EXECUTABLES = {
    "catchtable-sniper": (
        RiskHypothesisFamily.HOLD_CANCEL_RECONCILIATION,
        "ticket.stale-availability.v1",
    ),
    "express-bus-booking": (
        RiskHypothesisFamily.PARTIAL_SUCCESS_RETRY,
        "ticket.commit-then-timeout.v1",
    ),
    "intercity-bus-booking": (
        RiskHypothesisFamily.HOLD_CANCEL_RECONCILIATION,
        "ticket.cancel-ambiguity.v1",
    ),
    "ktx-booking": (
        RiskHypothesisFamily.APPROVAL_SCOPE,
        "ticket.event-identity-confusion.v1",
    ),
    "srt-booking": (
        RiskHypothesisFamily.APPROVAL_SCOPE,
        "ticket.price-fee-currency-drift.v1",
    ),
}


def _source(sha: str = K_SKILL_PINNED_SHA) -> SourceDescriptor:
    return SourceDescriptor(
        repository="NomaDamas/k-skill",
        repository_url="https://github.com/NomaDamas/k-skill",
        source_url=f"https://github.com/NomaDamas/k-skill/commit/{sha}",
        requested_ref=sha,
        resolved_sha=sha,
        retrieved_at="2026-08-01T13:30:00+09:00",
        resolver="fixture",
    )


def _intake(skill_id: str):
    catalog = load_k_skill_catalog()
    adapter = KSkillRiskProfileAdapter(
        catalog=catalog,
        evidence_fetcher=MappingEvidenceMetadataFetcher(metadata_fixture_for_catalog(catalog)),
    )
    return catalog, adapter.assess(_source(), skill_id)


def test_registry_accounts_for_every_declared_hypothesis_for_all_13_profiles() -> None:
    catalog = load_k_skill_catalog()
    registry = load_k_skill_mapping_registry(catalog)

    assert registry.snapshot.source_commit_sha == K_SKILL_PINNED_SHA
    assert registry.snapshot.source_tree_sha == K_SKILL_PINNED_TREE_SHA
    assert registry.snapshot.catalog_digest == catalog.catalog_digest
    assert tuple(item.skill_id for item in registry.snapshot.profiles) == tuple(
        sorted(K_SKILL_SHORTLIST)
    )

    for profile in catalog.profiles:
        reviewed = registry.profile(profile.skill_id)
        assert tuple(item.hypothesis for item in reviewed.hypothesis_mappings) == (
            profile.fault_hypotheses
        )
        for item in reviewed.hypothesis_mappings:
            if item.status is MappingSupport.UNSUPPORTED:
                assert item.reason is not None
                assert item.detail.strip()


def test_five_ticket_defaults_are_distinct_reviewed_executable_archetypes() -> None:
    catalog = load_k_skill_catalog()
    registry = load_k_skill_mapping_registry(catalog)

    selected_fixture_ids = set()
    for skill_id, (hypothesis, fixture_id) in DEFAULT_EXECUTABLES.items():
        reviewed = registry.profile(skill_id)
        selected = reviewed.default_mapping
        assert selected.status is MappingSupport.EXECUTABLE
        assert selected.hypothesis is hypothesis
        archetype = registry.archetype(selected.archetype_id)
        assert archetype.fixture_id == fixture_id
        selected_fixture_ids.add(archetype.fixture_id)

    assert len(selected_fixture_ids) == 5


def test_executable_entries_reference_real_pack_fixtures_and_declared_faults() -> None:
    catalog = load_k_skill_catalog()
    registry = load_k_skill_mapping_registry(catalog)

    for profile in catalog.profiles:
        reviewed = registry.profile(profile.skill_id)
        for mapping in reviewed.hypothesis_mappings:
            if mapping.status is not MappingSupport.EXECUTABLE:
                continue
            archetype = registry.archetype(mapping.archetype_id)
            pack = PACKS_BY_ID[archetype.pack_id]
            assert mapping.hypothesis in profile.fault_hypotheses
            assert archetype.fixture_id in pack.fixture_ids
            assert archetype.fault_operator in pack.fault_families
            assert pack.supports_protected_replay is True
            assert pack.supports_benign_control is True


def test_read_only_profiles_never_gain_mutation_or_privileged_sink_archetypes() -> None:
    catalog = load_k_skill_catalog()
    registry = load_k_skill_mapping_registry(catalog)

    for profile in catalog.profiles:
        if profile.side_effect is not SideEffectClass.READ_ONLY:
            continue
        reviewed = registry.profile(profile.skill_id)
        for mapping in reviewed.hypothesis_mappings:
            if mapping.status is not MappingSupport.EXECUTABLE:
                continue
            archetype = registry.archetype(mapping.archetype_id)
            assert archetype.has_mutation is False
            assert archetype.has_privileged_sink is False


def test_profile_rebinding_stops_before_an_archetype_is_selected() -> None:
    catalog, intake = _intake("srt-booking")
    assert intake.profile is not None
    rebound = intake.model_copy(
        update={
            "profile": intake.profile.model_copy(
                update={"description": "mutated after metadata intake"}
            )
        }
    )

    result = load_k_skill_mapping_registry(catalog).select(rebound)

    assert result.status is KSkillMappingStatus.UNSUPPORTED
    assert result.reason is KSkillMappingReason.INTAKE_INTEGRITY_MISMATCH
    assert result.archetype is None
    assert result.synthetic_execution_authorized is False
    assert result.target_observation_status == "not_executed"


def test_changed_source_stays_typed_unsupported_without_mapping_lookup() -> None:
    catalog = load_k_skill_catalog()

    class MetadataMustNotRun:
        def stat(self, source: SourceDescriptor, path: str):
            raise AssertionError("changed source must stop at intake")

    intake = KSkillRiskProfileAdapter(
        catalog=catalog,
        evidence_fetcher=MetadataMustNotRun(),
    ).assess(_source("1" * 40), "srt-booking")

    result = load_k_skill_mapping_registry(catalog).select(intake)

    assert result.status is KSkillMappingStatus.UNSUPPORTED
    assert result.reason is KSkillMappingReason.INTAKE_UNSUPPORTED
    assert result.intake_reason == "catalog_revision_not_registered"
    assert result.archetype is None
    assert result.synthetic_execution_authorized is False
