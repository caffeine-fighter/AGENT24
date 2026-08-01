from __future__ import annotations

import pytest

from agent24.tools import (
    STOCK_CLEAN_FIXTURE,
    STOCK_FAILURE_FIXTURES,
    STOCK_FULL_FIXTURE,
    StockGym,
)

EXPECTED_FINDINGS = {
    "stock.rumor-vs-disclosure.v1": "stock.rumor_vs_disclosure",
    "stock.stale-truth.v1": "stock.stale_truth",
    "stock.source-misattribution.v1": "stock.source_misattribution",
    "stock.ticker-collision.v1": "stock.ticker_collision",
    "stock.entity-mixing.v1": "stock.entity_mixing",
    "stock.analyst-note-injection.v1": "stock.analyst_note_prompt_injection",
}


@pytest.mark.parametrize(("fixture_id", "finding_id"), EXPECTED_FINDINGS.items())
def test_each_stock_fault_has_one_atomic_fixture(
    fixture_id: str, finding_id: str
) -> None:
    gym = StockGym.from_fixture(fixture_id)

    vulnerable = gym.diagnose(gym.vulnerable_assessment())
    safe = gym.diagnose(gym.safe_assessment())

    assert vulnerable.finding_ids() == (finding_id,)
    assert all(finding.evidence_refs for finding in vulnerable.findings)
    assert safe.passed is True


def test_stock_full_gauntlet_is_byte_stable_and_finds_six_failures() -> None:
    first_gym = StockGym.from_fixture(STOCK_FULL_FIXTURE, seed=71)
    second_gym = StockGym.from_fixture(STOCK_FULL_FIXTURE, seed=71)

    first = first_gym.diagnose(first_gym.vulnerable_assessment())
    second = second_gym.diagnose(second_gym.vulnerable_assessment())

    assert first.finding_ids() == (
        "stock.rumor_vs_disclosure",
        "stock.stale_truth",
        "stock.source_misattribution",
        "stock.ticker_collision",
        "stock.entity_mixing",
        "stock.analyst_note_prompt_injection",
    )
    assert first.to_json() == second.to_json()
    assert first.report_digest == second.report_digest


def test_stock_tools_make_ticker_and_source_conflicts_inspectable() -> None:
    gym = StockGym.from_fixture(STOCK_FULL_FIXTURE)

    ticker = gym.call("ticker.resolve", query="orh")
    source = gym.call("market.source", record_id="news-orh-source-error")
    note = gym.call("analyst_note.read", record_id="note-orh-injected")

    assert ticker["ambiguous"] is True
    assert {item["security_id"] for item in ticker["candidates"]} == {
        "sec-orh-nyse",
        "sec-orh-otc",
    }
    assert source == {
        "record_id": "news-orh-source-error",
        "displayed_source_id": "sec-edgar",
        "resolved_source_id": "anonymous-repost",
        "source_matches": False,
    }
    assert "contains_injection" not in note
    assert "SYSTEM OVERRIDE" in note["body"]


def test_stock_clean_control_and_function_tool_surface() -> None:
    gym = StockGym.from_fixture(STOCK_CLEAN_FIXTURE)
    names = {tool.name for tool in gym.tools()}

    assert gym.diagnose(gym.vulnerable_assessment()).passed is True
    assert names == {
        "ticker_resolve",
        "market_disclosures",
        "market_news",
        "market_record",
        "market_source",
        "entity_relations",
        "analyst_note_read",
    }
    assert set(STOCK_FAILURE_FIXTURES) - {STOCK_FULL_FIXTURE} == set(EXPECTED_FINDINGS)
