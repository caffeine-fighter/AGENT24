"""End-to-end evidence for the Life-v0 payment incident (issue #102).

One fixture, one seed, one starting world, run every way the report needs to
argue its case.  Each test below follows a single claim from the diagnosis all
the way to the append-only ledger rows that support it, because the whole point
of this path is that nothing here rests on what a model said about the run.
"""

from __future__ import annotations

from agent24.tools import protected_replay

FIXTURE = "life.payment_intent_timeout.v1"
SOURCE_REF = "example/cake-agent@0123456789abcdef0123456789abcdef01234567"


def test_life_v0_protected_replay_keeps_same_seed_and_one_charge() -> None:
    report = protected_replay(FIXTURE, seed=42)

    assert report.accepted
    assert report.baseline.initial_snapshot_hash == report.protected.initial_snapshot_hash
    assert report.perturbed.charge_count == 2
    assert report.protected.charge_count == 1
    assert report.protected.mission_succeeded
    assert report.benign_control.mission_succeeded


def test_the_whole_report_is_bound_to_one_revision_fixture_and_starting_world() -> None:
    """A reader can name what was tested without trusting the prose.

    Every arm carries the same source revision, fixture version, seed and
    initial snapshot, and the fault operator is recorded per arm so a
    fault-free control is not mistaken for a perturbed run that did nothing.
    """

    report = protected_replay(FIXTURE, seed=42, source_ref=SOURCE_REF)

    assert report.provenance_match is True
    assert report.initial_snapshot_match is True
    assert report.perturbed.provenance.to_dict() == {
        "source_ref": SOURCE_REF,
        "fixture_id": FIXTURE,
        "fault_id": "fault.payment-intent.confirm-timeout.v1",
        "fault_type": "commit_then_timeout",
        "seed": 42,
        "initial_snapshot_hash": report.baseline.initial_snapshot_hash,
    }


def test_the_duplicate_charge_is_measured_from_the_ledger_before_and_after() -> None:
    """Two committed charges before the policy, one after, read off the ledger.

    ``order.create`` stays at one on both sides because the idempotency key
    deduplicates it -- the duplicated observable is the charge and the
    fulfillment it triggers, which is why those are the rows cited.
    """

    report = protected_replay(FIXTURE, seed=42)
    committed = {
        entry["seq"]: entry
        for entry in report.perturbed.ledger
        if entry["status"] == "committed"
    }

    assert report.perturbed.charge_refs == (3, 5)
    assert report.perturbed.fulfillment_refs == (7, 9)
    assert report.protected.charge_refs == (3,)
    assert report.protected.fulfillment_refs == (5,)

    # The two charges are separate provider objects, not one row counted twice.
    first, second = (committed[seq] for seq in report.perturbed.charge_refs)
    assert first["metadata"]["payment_intent_id"] != second["metadata"]["payment_intent_id"]
    assert report.perturbed.order_count == report.protected.order_count == 1
    assert report.perturbed.total_spend_krw == 98_000
    assert report.protected.total_spend_krw == 49_000


def test_divergence_counterexample_and_violation_all_cite_the_same_rows() -> None:
    """Three independent pieces of evidence, one story.

    The first divergence says *where* the runs parted, the minimal
    counterexample says *what had to be true*, and the oracle says *which
    invariant broke* -- and all three resolve to the same committed ledger
    rows.  If they disagreed, at least one of them would be describing a
    different failure.
    """

    report = protected_replay(FIXTURE, seed=42)
    divergence = report.first_divergence
    counterexample = report.minimal_counterexample
    assert divergence is not None and counterexample is not None

    violation = next(
        item
        for item in report.perturbed.oracle["violations"]
        if item["id"] == "exactly_once_payment"
    )

    # Where: the failing run opens a second intent where the twin delivered a
    # webhook, and never retrieved the first one.
    assert divergence.baseline_step[0] == "webhook.deliver"
    assert divergence.failing_step[0] == "payment_intent.create"
    assert report.perturbed.reconciliation_observed is False

    # What: both conditions verified necessary by leave-one-out.
    assert counterexample.minimal is True

    # Which: the same two committed charges, from all three sides.
    assert tuple(violation["ledger_refs"]) == report.perturbed.charge_refs
    assert counterexample.ledger_refs == report.perturbed.charge_refs
    assert violation["actual"] == 2 and violation["expected"] == 1


def test_the_protected_run_reconciles_and_rejoins_the_healthy_twin() -> None:
    """The mitigation returns the run to the baseline's behaviour."""

    report = protected_replay(FIXTURE, seed=42)

    def steps(run):
        return [(entry["tool"], entry["effect"], entry["status"]) for entry in run.ledger]

    assert report.protected.reconciliation_observed is True
    assert steps(report.protected) == steps(report.baseline)
    assert report.protected.oracle["passed"] is True


def test_a_mitigation_is_verified_only_after_same_seed_neighbour_and_benign() -> None:
    """All three gates, plus the control that rejects refusing to pay.

    Each is asserted separately rather than through ``accepted`` alone, so a
    regression names the gate that broke instead of only reporting that the
    replay stopped being accepted.
    """

    report = protected_replay(FIXTURE, seed=42)

    assert report.protected_reduces_duplicate_charge is True
    assert report.protected_mission_succeeds is True
    assert report.neighbors_hold is True
    assert report.benign_control_succeeds is True
    assert report.blanket_block_rejected is True
    assert report.blanket_block.charge_count == 0
    assert report.blanket_block.mission_succeeded is False

    # The neighbour gate is only meaningful because some neighbour reproduced.
    assert len(report.reproducing_neighbors) >= 1
    assert all(item.holds for item in report.neighbors)

    assert report.accepted is True


def test_three_replays_of_the_same_seed_produce_one_digest() -> None:
    reports = [protected_replay(FIXTURE, seed=42) for _ in range(3)]

    assert len({report.run_digest for report in reports}) == 1
    assert len({report.canonical_json() for report in reports}) == 1
