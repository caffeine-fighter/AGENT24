from __future__ import annotations

from agent24.tools import protected_replay


def test_life_v0_protected_replay_keeps_same_seed_and_one_charge() -> None:
    report = protected_replay("life.payment_intent_timeout.v1", seed=42)

    assert report.accepted
    assert report.baseline.initial_snapshot_hash == report.protected.initial_snapshot_hash
    assert report.perturbed.charge_count == 2
    assert report.protected.charge_count == 1
    assert report.protected.mission_succeeded
    assert report.benign_control.mission_succeeded
