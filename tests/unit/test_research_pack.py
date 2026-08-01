from __future__ import annotations

import pytest

from agent24.tools import (
    RESEARCH_CLEAN_FIXTURE,
    RESEARCH_FAILURE_FIXTURES,
    RESEARCH_FULL_FIXTURE,
    ResearchGym,
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
