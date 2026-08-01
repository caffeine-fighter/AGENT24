from __future__ import annotations

from agent24.agent.manifest import SUPPORTED_TOOL_NAMES
from agent24.tools import (
    RESEARCH_TOOL_MANIFEST,
    STOCK_TOOL_MANIFEST,
    AdhocGym,
    ResearchGym,
    StockGym,
)


def test_research_full_pack_replays_four_failures_and_preserves_raw_boundary() -> None:
    first = ResearchGym.from_fixture(seed=17)
    second = ResearchGym.from_fixture(seed=17)

    raw_page = first.call(
        "pdf.page.read",
        paper_id="paper-synth-001",
        page_number=1,
    )
    report = first.diagnose(first.vulnerable_assessment())
    replay = second.diagnose(second.vulnerable_assessment())

    assert "contains_injection" not in raw_page
    assert len(report.findings) == 4
    assert report.to_json() == replay.to_json()
    assert first.diagnose(first.safe_assessment()).passed is True


def test_stock_full_pack_replays_six_failures_and_preserves_raw_boundary() -> None:
    first = StockGym.from_fixture(seed=17)
    second = StockGym.from_fixture(seed=17)

    raw_note = first.call("analyst_note.read", record_id="note-orh-injected")
    report = first.diagnose(first.vulnerable_assessment())
    replay = second.diagnose(second.vulnerable_assessment())

    assert "contains_injection" not in raw_note
    assert len(report.findings) == 6
    assert report.to_json() == replay.to_json()
    assert first.diagnose(first.safe_assessment()).passed is True


def test_adhoc_selector_builds_bounded_probes_from_an_agent_tool_surface() -> None:
    gym = AdhocGym()
    selected = gym.select(
        ["web.search", "payment.charge", "calendar.create", "email.send"],
        limit=6,
    )

    assert len(selected["selections"]) == 6
    for selection in selected["selections"]:
        scenario_id = selection["scenario_id"]
        assert gym.inspect(scenario_id)["tool_call_budget"] <= 8
        assert gym.probe(scenario_id) != gym.controller_ground_truth(scenario_id)[
            "benign_result"
        ]


def test_external_manifest_vocabulary_includes_both_domain_tool_packs() -> None:
    research_names = {item["name"] for item in RESEARCH_TOOL_MANIFEST}
    stock_names = {item["name"] for item in STOCK_TOOL_MANIFEST}

    assert research_names <= SUPPORTED_TOOL_NAMES
    assert stock_names <= SUPPORTED_TOOL_NAMES
