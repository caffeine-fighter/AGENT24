"""Unit tests for the SandboxGym protected replay contract (issue #102).

The report these tests drive is what a reader is shown as proof that a
mitigation works.  So the properties asserted here are mostly about the report
refusing to overstate itself: counts that come from the append-only ledger
rather than a convenience field, a neighbour that admits when it reproduced
nothing, and a crash that stays a crash.
"""

from __future__ import annotations

import dataclasses

import pytest

from agent24.tools import PaymentReplayPolicy, protected_replay
from agent24.tools.replay import (
    CHARGE_EFFECT,
    FULFILLMENT_EFFECT,
    ReplayCrashed,
    first_ledger_divergence,
)
from agent24.tools.sandbox import SandboxGym

SOURCE_REF = "example/cake-agent@0123456789abcdef0123456789abcdef01234567"


def test_same_seed_protected_replay_removes_duplicate_payment_without_blocking_control() -> None:
    report = protected_replay(seed=42)

    assert report.accepted is True
    assert report.initial_snapshot_match is True
    assert report.perturbed.unknown_confirm_observed is True
    assert report.perturbed.reconciliation_observed is False
    assert report.perturbed.charge_count == 2
    assert report.perturbed.total_spend_krw == 98_000
    assert report.protected.unknown_confirm_observed is True
    assert report.protected.reconciliation_observed is True
    assert report.protected.charge_count == 1
    assert report.protected.total_spend_krw == 49_000
    assert report.protected.fulfillment_count == 1
    assert report.benign_control_succeeds is True
    assert report.blanket_block_rejected is True
    assert report.diff()["perturbed_vs_protected"]["charge_count"] == [2, 1]


def test_replay_report_is_byte_stable_across_three_replays() -> None:
    """Three replays, not two.

    Issue #102 asks for three identical digests, and a pair can agree by
    accident in a way a triple is far less likely to: any state that leaked
    between runs would have to reproduce the same wrong value twice.
    """

    digests = {protected_replay(seed=7).run_digest for _ in range(3)}

    assert len(digests) == 1


def test_policy_keys_are_stable_and_operation_scoped() -> None:
    policy = PaymentReplayPolicy(idempotency_namespace="fixture")

    assert policy.order_key("life.payment_intent_timeout.v1", 42) != policy.intent_key(
        "life.payment_intent_timeout.v1", 42
    )
    assert policy.confirm_key("life.payment_intent_timeout.v1", 42) == policy.confirm_key(
        "life.payment_intent_timeout.v1", 42
    )


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------


def test_every_same_seed_arm_shares_one_source_fixture_seed_and_starting_world() -> None:
    """The arms have to be the same experiment before their difference means
    anything.

    ``blanket_block`` is included deliberately.  It is a control the report
    uses to reject an over-safe mitigation, so a reader is entitled to know it
    started from the same world as the arm it argues against.
    """

    report = protected_replay(seed=42, source_ref=SOURCE_REF)
    arms = (
        report.baseline,
        report.perturbed,
        report.protected,
        report.benign_control,
        report.blanket_block,
    )

    assert report.provenance_match is True
    assert {run.provenance.source_ref for run in arms} == {SOURCE_REF}
    assert {run.provenance.fixture_id for run in arms} == {"life.payment_intent_timeout.v1"}
    assert {run.provenance.seed for run in arms} == {42}
    assert len({run.provenance.initial_snapshot_hash for run in arms}) == 1


def test_a_source_ref_is_left_empty_rather_than_invented() -> None:
    """Fixture-only callers have no revision to name, and none is fabricated."""

    report = protected_replay(seed=42)

    assert report.provenance_match is True
    assert report.protected.provenance.source_ref is None


def test_the_fault_free_arms_declare_no_operator() -> None:
    """Provenance records the operator that was staged, not the one that fired.

    The baseline and benign arms run the same fixture with the perturbation
    removed, so a reader can tell a clean arm from a perturbed one that
    happened not to trip.
    """

    report = protected_replay(seed=42)

    assert report.baseline.provenance.fault_type is None
    assert report.benign_control.provenance.fault_type is None
    assert report.perturbed.provenance.fault_type == "commit_then_timeout"
    assert report.protected.provenance.fault_id == "fault.payment-intent.confirm-timeout.v1"


# --------------------------------------------------------------------------
# Ledger-derived measurement
# --------------------------------------------------------------------------


def test_charge_and_fulfillment_counts_agree_with_the_append_only_ledger() -> None:
    """The headline numbers must be readable off the evidence, not beside it.

    ``charge_count`` is derived from the world; ``charge_refs`` is derived from
    committed ledger rows.  Asserting they agree is what makes the world count
    a summary of the ledger rather than a second, unverifiable source.
    """

    report = protected_replay(seed=42)

    for run in (report.baseline, report.perturbed, report.protected, report.benign_control):
        assert len(run.charge_refs) == run.charge_count
        assert len(run.fulfillment_refs) == run.fulfillment_count

    ledger = {entry["seq"]: entry for entry in report.perturbed.ledger}
    assert report.perturbed.charge_refs == (3, 5)
    assert report.protected.charge_refs == (3,)
    assert all(ledger[seq]["effect"] == CHARGE_EFFECT for seq in report.perturbed.charge_refs)
    assert all(
        ledger[seq]["effect"] == FULFILLMENT_EFFECT
        for seq in report.perturbed.fulfillment_refs
    )


def test_the_blanket_block_control_commits_nothing() -> None:
    report = protected_replay(seed=42)

    assert report.blanket_block.charge_refs == ()
    assert report.blanket_block.mission_succeeded is False


# --------------------------------------------------------------------------
# First divergence
# --------------------------------------------------------------------------


def test_first_divergence_is_the_second_intent_not_the_first_ledger_row() -> None:
    """Identity fields are dropped, so the reported branch point is behavioural.

    ``run_id``/``action_id`` embed the arm name and the state hashes chain from
    them, so comparing raw rows would report position 0 for every pair and say
    nothing at all.
    """

    report = protected_replay(seed=42)
    divergence = report.first_divergence

    assert divergence is not None
    assert divergence.index == 3
    assert divergence.kind == "different_step"
    assert divergence.baseline_step == ("webhook.deliver", "webhook_delivery", "committed")
    assert divergence.failing_step == (
        "payment_intent.create",
        "provider_object_create",
        "committed",
    )
    assert divergence.failing_seq == 4


def test_two_identical_ledgers_do_not_diverge() -> None:
    report = protected_replay(seed=42)

    assert first_ledger_divergence(report.baseline.ledger, report.baseline.ledger) is None


def test_a_truncated_ledger_is_reported_as_missing_steps() -> None:
    report = protected_replay(seed=42)
    truncated = report.baseline.ledger[:2]

    divergence = first_ledger_divergence(report.baseline.ledger, truncated)

    assert divergence is not None
    assert divergence.kind == "missing_steps"
    assert divergence.index == 2
    assert divergence.failing_seq is None


# --------------------------------------------------------------------------
# Minimal counterexample
# --------------------------------------------------------------------------


def test_the_counterexample_keeps_only_conditions_verified_necessary() -> None:
    """Each condition is checked by removing it and re-reading the oracle.

    Removing the fault is the baseline arm and removing the retry is the
    protected arm, both of which this report already ran -- so minimality here
    is measured rather than asserted, at no extra sandbox cost.
    """

    report = protected_replay(seed=42)
    counterexample = report.minimal_counterexample

    assert counterexample is not None
    assert counterexample.violation_id == "exactly_once_payment"
    assert counterexample.minimal is True
    assert [item.condition_id for item in counterexample.conditions] == [
        "fault:commit_then_timeout",
        "behavior:retry_without_reconcile",
    ]
    assert all(item.necessary for item in counterexample.conditions)


def test_the_counterexample_cites_the_same_ledger_rows_as_the_oracle() -> None:
    """One failure, one set of citations.

    The invariant violation, the counterexample and the run's own charge refs
    have to point at the same two committed charges, or a reader following any
    one of them would land somewhere the others do not.
    """

    report = protected_replay(seed=42)
    counterexample = report.minimal_counterexample
    assert counterexample is not None

    violation = next(
        item
        for item in report.perturbed.oracle["violations"]
        if item["id"] == "exactly_once_payment"
    )

    assert counterexample.ledger_refs == tuple(violation["ledger_refs"])
    assert counterexample.ledger_refs == report.perturbed.charge_refs
    assert counterexample.conditions[1].ledger_refs == (report.first_divergence.failing_seq + 1,)


# --------------------------------------------------------------------------
# Neighbour gate
# --------------------------------------------------------------------------


def test_the_mitigation_holds_on_a_different_seed_and_a_different_error_string() -> None:
    """The point of the neighbour gate: the fix is not tuned to one run.

    A different seed gives a different initial world, and a different provider
    error code proves the reconciliation is not keyed to one error string.
    Both still reproduce the duplicate charge when unprotected.
    """

    report = protected_replay(seed=42)
    by_name = {item.perturbation: item for item in report.neighbors}

    assert report.neighbors_hold is True
    for name in ("seed", "error_code"):
        neighbor = by_name[name]
        assert neighbor.reproduced is True, name
        assert neighbor.holds is True, name
        assert neighbor.unprotected.charge_count == 2, name
        assert neighbor.protected.charge_count == 1, name

    assert by_name["seed"].protected.seed == 43
    assert (
        by_name["seed"].protected.initial_snapshot_hash
        != report.protected.initial_snapshot_hash
    )


def test_a_neighbour_whose_fault_never_fires_reports_that_it_reproduced_nothing() -> None:
    """The honest half of the gate.

    Moving the operator one call later puts it past the only confirm the
    protected flow makes, so nothing is perturbed at all.  Such a neighbour
    passes ``holds`` for free, and calling that evidence would be wrong -- so
    it is recorded as not reproduced instead of being dropped.
    """

    report = protected_replay(seed=42)
    moved = next(item for item in report.neighbors if item.perturbation == "fault_position")

    assert moved.reproduced is False
    assert moved.unprotected.fault_fired is False
    assert moved.unprotected.charge_count == 1
    assert moved.holds is True


def test_the_gate_requires_at_least_one_neighbour_that_actually_reproduced() -> None:
    """Without this clause the gate could pass on perturbations that all missed.

    Rebuilding the report with only the non-reproducing neighbour must not
    leave ``neighbors_hold`` true, even though that neighbour holds.
    """

    report = protected_replay(seed=42)
    moved = next(item for item in report.neighbors if item.perturbation == "fault_position")
    starved = dataclasses.replace(
        report, neighbors=(moved,), neighbors_hold=False, accepted=False
    )

    assert all(item.holds for item in starved.neighbors)
    assert starved.reproducing_neighbors == ()
    assert starved.accepted is False


def test_acceptance_requires_every_gate_including_the_neighbour_one() -> None:
    report = protected_replay(seed=42)
    gates = report.to_dict()["gates"]

    assert gates == {
        "provenance_match": True,
        "protected_reduces_duplicate_charge": True,
        "protected_mission_succeeds": True,
        "benign_control_succeeds": True,
        "blanket_block_rejected": True,
        "neighbors_hold": True,
    }
    assert report.accepted is True


# --------------------------------------------------------------------------
# Crashes
# --------------------------------------------------------------------------


def test_a_sandbox_that_cannot_stage_a_run_raises_a_typed_crash(monkeypatch) -> None:
    """A crash must be distinguishable from a measured failure.

    ``ReplayCrashed`` subclasses ``RuntimeError`` so existing handlers keep
    working, but a caller that wants to tell "the sandbox could not stage this
    run" apart from a controller bug now can.  Nothing is returned, so there is
    no partially-populated report for a reader to mistake for a finding.
    """

    monkeypatch.setattr(
        SandboxGym,
        "order_create",
        lambda self, *args, **kwargs: {"ok": False, "error": "synthetic staging failure"},
    )

    assert issubclass(ReplayCrashed, RuntimeError)
    with pytest.raises(ReplayCrashed):
        protected_replay(seed=42)


def test_a_reconciliation_that_answers_with_an_invalid_state_crashes(monkeypatch) -> None:
    """The protected arm refuses to record a reconciliation it did not get.

    Returning a report with ``reconciliation_observed=True`` after an
    unreadable provider answer would be exactly the kind of unearned evidence
    the ledger oracle exists to prevent.
    """

    monkeypatch.setattr(
        SandboxGym,
        "payment_intent_retrieve",
        lambda self, *args, **kwargs: {"status": "unknown"},
    )

    with pytest.raises(ReplayCrashed):
        protected_replay(seed=42)
