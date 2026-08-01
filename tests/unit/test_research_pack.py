from __future__ import annotations

import pytest

from agent24.agent.packs import RESEARCH_PACK
from agent24.tools import (
    MAX_RESEARCH_TOOL_CALL_BUDGET,
    RESEARCH_CLEAN_FIXTURE,
    RESEARCH_FAILURE_FIXTURES,
    RESEARCH_FULL_FIXTURE,
    ResearchDomainPackAdapter,
    ResearchGym,
    ResearchReplayPolicy,
    research_protected_replay,
)

EXPECTED_FINDINGS = {
    "research.generated-citation.v1": "research.generated_citation",
    "research.abstract-table-contradiction.v1": (
        "research.abstract_table_contradiction"
    ),
    "research.pdf-prompt-injection.v1": "research.pdf_prompt_injection",
    "research.reproducibility-mirage.v1": "research.reproducibility_mirage",
}


@pytest.mark.parametrize(("fixture_id", "finding_id"), EXPECTED_FINDINGS.items())
def test_each_research_fault_has_one_atomic_fixture(
    fixture_id: str, finding_id: str
) -> None:
    gym = ResearchGym.from_fixture(fixture_id)

    vulnerable = gym.diagnose(gym.vulnerable_assessment())
    safe = gym.diagnose(gym.safe_assessment())

    assert vulnerable.finding_ids() == (finding_id,)
    assert all(finding.evidence_refs for finding in vulnerable.findings)
    assert safe.passed is True


def test_research_full_gauntlet_is_byte_stable_and_evidence_bounded() -> None:
    first_gym = ResearchGym.from_fixture(RESEARCH_FULL_FIXTURE, seed=71)
    second_gym = ResearchGym.from_fixture(RESEARCH_FULL_FIXTURE, seed=71)

    first = first_gym.diagnose(first_gym.vulnerable_assessment())
    second = second_gym.diagnose(second_gym.vulnerable_assessment())

    assert first.finding_ids() == (
        "research.generated_citation",
        "research.abstract_table_contradiction",
        "research.pdf_prompt_injection",
        "research.reproducibility_mirage",
    )
    assert first.to_json() == second.to_json()
    assert first.report_digest == second.report_digest
    assert first.fixture_digest == first_gym.controller_ground_truth()["fixture_digest"]


def test_research_tools_expose_raw_documents_but_not_controller_fault_labels() -> None:
    gym = ResearchGym.from_fixture(RESEARCH_FULL_FIXTURE)

    paper = gym.call("paper.fetch", paper_id="paper-synth-001")
    page = gym.call(
        "pdf.page.read",
        paper_id="paper-synth-001",
        page_number=1,
    )
    fake = gym.call("citation.resolve", citation_id="cite-gpt-404")

    assert paper["trust"] == "untrusted_document"
    assert "injected_faults" not in paper
    assert "contains_injection" not in page
    assert "SYSTEM OVERRIDE" in page["text"]
    assert fake == {
        "citation_id": "cite-gpt-404",
        "found": False,
        "source_url": None,
        "provenance": "model_generated",
    }


def test_research_clean_control_and_function_tool_surface() -> None:
    gym = ResearchGym.from_fixture(RESEARCH_CLEAN_FIXTURE)
    report = gym.diagnose(gym.vulnerable_assessment())
    names = {tool.name for tool in gym.tools()}

    assert report.passed is True
    assert names == {
        "research_search",
        "paper_fetch",
        "table_read",
        "citation_resolve",
        "pdf_page_read",
        "repository_inspect",
        "dataset_inspect",
        "experiment_inspect",
    }
    assert set(RESEARCH_FAILURE_FIXTURES) - {RESEARCH_FULL_FIXTURE} == set(
        EXPECTED_FINDINGS
    )


@pytest.mark.parametrize("fixture_id", RESEARCH_FAILURE_FIXTURES)
def test_research_protected_replay_preserves_a_cited_answer_and_rejects_overblocking(
    fixture_id: str,
) -> None:
    first, second, third = (
        research_protected_replay(fixture_id, seed=29) for _ in range(3)
    )

    assert first.vulnerable_report.findings
    assert first.protected_report.passed is True
    assert first.benign_report.passed is True
    assert first.blanket_report.passed is True
    assert first.protected.final_answer.strip()
    assert first.protected.citation_ids == ("cite-publisher-001",)
    citation = ResearchGym.from_fixture(fixture_id, seed=29).resolve_citation(
        first.protected.citation_ids[0]
    )
    assert citation["found"] is True
    assert citation["source_url"]
    assert citation["provenance"] == "publisher_index"
    assert first.same_fixture_replay is True
    assert first.protected_answer_valid is True
    assert first.protected_citation_verified is True
    assert first.benign_control_succeeds is True
    assert first.blanket_refusal.final_answer == ""
    assert first.blanket_refusal.refused is True
    assert first.blanket_refusal.blocked_all_research is True
    assert first.blanket_refusal_rejected is True
    assert first.accepted is True
    assert first.to_json() == second.to_json() == third.to_json()
    assert first.report_digest == second.report_digest == third.report_digest


def test_disabled_research_policy_reproduces_all_full_gauntlet_findings() -> None:
    report = research_protected_replay(
        RESEARCH_FULL_FIXTURE,
        seed=29,
        policy=ResearchReplayPolicy(
            require_citation_resolution=False,
            cross_check_primary_table=False,
            treat_pdf_as_untrusted_evidence=False,
            verify_reproducibility_artifacts=False,
        ),
    )

    assert report.protected_report.finding_ids() == (
        "research.generated_citation",
        "research.abstract_table_contradiction",
        "research.pdf_prompt_injection",
        "research.reproducibility_mirage",
    )
    assert report.protected_answer_valid is False
    assert report.protected_citation_verified is False
    assert report.accepted is False


def test_research_domain_adapter_matches_registry_and_enforces_call_budget() -> None:
    assert ResearchDomainPackAdapter.pack_id == RESEARCH_PACK.pack_id
    assert ResearchDomainPackAdapter.version == RESEARCH_PACK.version
    assert ResearchDomainPackAdapter.domain_kind == RESEARCH_PACK.domain_kind.value
    assert set(ResearchDomainPackAdapter.mission_families) == {
        family.value for family in RESEARCH_PACK.mission_families
    }
    assert ResearchDomainPackAdapter.required_tool_capabilities == (
        RESEARCH_PACK.anchor_tools
    )
    assert ResearchDomainPackAdapter.optional_tool_capabilities == (
        RESEARCH_PACK.optional_tools
    )
    assert set(ResearchDomainPackAdapter.fixture_ids) == set(RESEARCH_PACK.fixture_ids)
    assert (
        ResearchDomainPackAdapter.max_tool_calls
        == RESEARCH_PACK.budget.max_tool_calls
        == MAX_RESEARCH_TOOL_CALL_BUDGET
    )
    assert ResearchDomainPackAdapter.supports(
        ResearchDomainPackAdapter.required_tool_capabilities
    )
    assert not ResearchDomainPackAdapter.supports({"web.search"})

    gym = ResearchDomainPackAdapter.build(RESEARCH_CLEAN_FIXTURE, seed=71)
    for _ in range(MAX_RESEARCH_TOOL_CALL_BUDGET):
        gym.call("research.search", query="synthetic")
    with pytest.raises(RuntimeError, match="budget exhausted"):
        gym.call("research.search", query="synthetic")


def test_research_protected_replay_rejects_a_clean_fixture() -> None:
    with pytest.raises(ValueError, match="requires a research failure fixture"):
        research_protected_replay(RESEARCH_CLEAN_FIXTURE)


def test_research_assessment_cannot_cross_fixture_boundary() -> None:
    source = ResearchGym.from_fixture(RESEARCH_CLEAN_FIXTURE)
    target = ResearchGym.from_fixture(RESEARCH_FULL_FIXTURE)

    with pytest.raises(ValueError, match="does not match"):
        target.diagnose(source.safe_assessment())
