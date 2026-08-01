"""D5a family ``C4_honest_outcomes`` — the controller half, Q3 (B).

The reducer half (``web/tests/event-protocol.test.mjs``) is Q4/A's.  This file
covers what the controller must guarantee before a reducer can render it: that a
run which found nothing, ran out of budget, or could not be staged says so, and
that none of those states borrows a finding's evidence or a safety claim.

Case ids are frozen by the D5a manifest (`tests/evals/d5a-plan.json` on
`eval/rekhet-d5a-test-plan`, commit `9bec2a1`) and restated here because the
manifest is not on `main`; the traceability test below refuses to let one go
missing.
"""

from __future__ import annotations

from agent24.agent.models import StopDecision
from agent24.agent.prompts import LoopBudget, LoopState, should_stop
from agent24.agent.report import (
    NOT_A_SAFETY_CERTIFICATE,
    FindingReportStatus,
    build_report,
)

CASE_IDS = (
    "HO-01-unsupported",
    "HO-02-no-failure-observed",
    "HO-03-budget-exhausted",
    "HO-04-source-failure-separate-fallback",
    "HO-05-explanation-offline-keeps-submitted-evidence",
)

IMPLEMENTED_HERE: dict[str, str] = {
    "HO-02-no-failure-observed": "test_ho_02_a_run_that_found_nothing_does_not_claim_safety",
    "HO-03-budget-exhausted": "test_ho_03_an_exhausted_budget_is_not_a_clean_result",
}

COVERED_ELSEWHERE: dict[str, str] = {
    "HO-01-unsupported": (
        "tests/evals/test_surprise_support.py::"
        "test_an_off_domain_mission_against_a_payment_manifest_terminates_unsupported and "
        "::test_the_off_domain_run_stops_before_planning_an_experiment — one typed unsupported "
        "terminal with no experiment_plan, no protected_replay and zero payment evidence; "
        "tests/unit/test_mission_scope.py covers the classification that produces it"
    ),
}

BLOCKED: dict[str, str] = {
    "HO-04-source-failure-separate-fallback": (
        "A4 semantic-event migration and Q4. The required assertions are a distinct fixture "
        "fingerprint and zero submitted-provenance carryover, both of which live in the reducer "
        "state the migrated envelope defines; `fixture_fallback` appears in no Python test today."
    ),
    "HO-05-explanation-offline-keeps-submitted-evidence": (
        "A4 semantic-event migration. Distinguishing explanation degradation from fixture "
        "fallback needs the migrated origin field; today both arrive as offline_demo and the "
        "controller cannot tell a reader which one happened."
    ),
}


# --------------------------------------------------------------------------
# Traceability
# --------------------------------------------------------------------------


def test_the_c4_family_declares_every_controller_case_id() -> None:
    buckets = (set(IMPLEMENTED_HERE), set(COVERED_ELSEWHERE), set(BLOCKED))
    union: set[str] = set()
    for bucket in buckets:
        assert not (union & bucket), "a case id appears in two buckets"
        union |= bucket
    assert union == set(CASE_IDS)

    implemented = set(globals())
    for case_id, test_name in IMPLEMENTED_HERE.items():
        assert test_name in implemented, f"{case_id} names a test that does not exist"


# --------------------------------------------------------------------------
# HO-02 · no failure observed
# --------------------------------------------------------------------------


def test_ho_02_a_run_that_found_nothing_does_not_claim_safety() -> None:
    """HO-02. "We observed no failure" and "this agent is safe" are different
    sentences, and only the first one is earned.

    The status is *derived* from the absence of evidence rather than chosen, so
    there is no argument a caller could pass to upgrade it.
    """

    report = build_report(
        "f-none-observed",
        experiments_run=2,
        families=("commit_then_timeout", "malicious_web_content"),
    )

    assert report.status is FindingReportStatus.NO_FAILURE_OBSERVED
    assert NOT_A_SAFETY_CERTIFICATE in report.bounded_summary
    assert report.claims_a_failure is False
    assert report.claims_a_mitigation is False


def test_ho_02_a_non_finding_state_carries_no_finding_fields() -> None:
    """The family's headline assertion: non-finding states carry no finding
    fields.  Asserted field by field, because "looks empty" is not the same as
    "cannot borrow a measured failure's evidence"."""

    report = build_report("f-none-observed", experiments_run=1, families=("empty_result",))

    assert report.observed is None
    assert report.proposed_patch is None
    assert report.verification is None
    assert report.diagnosis_hypothesis is None
    assert report.first_divergence is None
    assert report.minimized_counterexample is None
    assert report.citations() == []
    assert report.reproduction() == "0/0 same-seed replays"


# --------------------------------------------------------------------------
# HO-03 · budget exhausted
# --------------------------------------------------------------------------


def test_ho_03_an_exhausted_budget_is_not_a_clean_result() -> None:
    """HO-03. Running out of room to look is not the same as having looked."""

    report = build_report(
        "f-budget",
        stop=StopDecision(
            stop=True,
            reason="budget_exhausted",
            detail="실험 3/3회를 모두 사용했다.",
        ),
        experiments_run=3,
        cost_units_used=6,
    )

    assert report.status is FindingReportStatus.BUDGET_EXHAUSTED
    assert report.claims_a_failure is False
    assert report.claims_a_mitigation is False
    assert report.observed is None
    assert report.proposed_patch is None
    assert report.verification is None


def test_ho_03_either_bound_exhausts_the_budget() -> None:
    """Both declared bounds terminate, and neither is silently ignored."""

    budget = LoopBudget(max_experiments=2, max_cost_units=4)

    assert should_stop(LoopState(experiments_run=0, cost_units_used=0), budget).stop is False
    by_experiments = should_stop(LoopState(experiments_run=2, cost_units_used=0), budget)
    by_cost = should_stop(LoopState(experiments_run=1, cost_units_used=4), budget)

    assert by_experiments.reason == "budget_exhausted"
    assert by_cost.reason == "budget_exhausted"
    # The reason has to say the search ended for lack of room, not for lack of
    # findings -- that distinction is the whole point of the state.
    assert "예산" in by_experiments.detail


def test_ho_03_an_unsupported_input_outranks_an_exhausted_budget() -> None:
    """Two true statements, one honest one.

    A run that could not be staged *and* spent its budget is reported as
    unsupported: "we could not test this" and "we ran out of room to test" are
    different claims to a reader, and only the first explains the run.
    """

    stop = should_stop(
        LoopState(experiments_run=9, cost_units_used=9, supported=False),
        LoopBudget(max_experiments=2, max_cost_units=4),
    )

    assert stop.reason == "unsupported_input"
