from __future__ import annotations

from dataclasses import replace

import pytest

from agent24.tools import build_world, load_fixture


def test_same_fixture_and_seed_have_identical_snapshot_hash() -> None:
    first = build_world("life.cake_collision.v1", seed=42).snapshot()
    second = build_world("life.cake_collision.v1", seed=42).snapshot()

    assert first.state_hash == second.state_hash
    assert first.to_dict() == second.to_dict()


def test_snapshot_restore_removes_side_effects_and_preserves_initial_hash() -> None:
    gym = load_fixture("life.cake_collision.v1", seed=42)
    initial = gym.initial_snapshot
    gym.payment_charge("cake-49k")

    assert gym.snapshot().state_hash != initial.state_hash
    assert len(gym.world.orders) == 1

    gym.reset(run_id="run-replay")

    assert gym.snapshot().state_hash == initial.state_hash
    assert gym.ledger.entries == ()
    assert gym.ledger.run_id == "run-replay"
    assert gym.faults.call_counts == {}


def test_snapshot_state_is_returned_as_a_fresh_copy() -> None:
    snapshot = build_world("life.cake_collision.v1").snapshot()
    state = snapshot.state
    state["wallet"]["balance_krw"] = 0

    assert snapshot.state["wallet"]["balance_krw"] == 500_000


def test_snapshot_restore_rejects_tampered_hash() -> None:
    world = build_world("life.cake_collision.v1")
    tampered = replace(world.snapshot(), state_hash="tampered")

    with pytest.raises(ValueError, match="snapshot hash"):
        type(world).from_snapshot(tampered)
