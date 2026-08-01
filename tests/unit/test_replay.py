from __future__ import annotations

from agent24.tools import PaymentReplayPolicy, protected_replay


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


def test_replay_report_is_byte_stable_for_same_seed() -> None:
    first = protected_replay(seed=7)
    second = protected_replay(seed=7)

    assert first.canonical_json() == second.canonical_json()
    assert first.run_digest == second.run_digest


def test_policy_keys_are_stable_and_operation_scoped() -> None:
    policy = PaymentReplayPolicy(idempotency_namespace="fixture")

    assert policy.order_key("life.payment_intent_timeout.v1", 42) != policy.intent_key(
        "life.payment_intent_timeout.v1", 42
    )
    assert policy.confirm_key("life.payment_intent_timeout.v1", 42) == policy.confirm_key(
        "life.payment_intent_timeout.v1", 42
    )
