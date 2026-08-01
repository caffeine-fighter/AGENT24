from __future__ import annotations

import socket

import pytest

from agent24.tools import (
    MAX_TICKET_TOOL_CALL_BUDGET,
    TICKET_BENIGN_FIXTURES,
    TICKET_FAILURE_FIXTURES,
    TICKET_FULL_FIXTURE,
    TicketDomainPackAdapter,
    TicketGym,
    TicketReplayPolicy,
    ticket_protected_replay,
)

EXPECTED_FINDINGS = {
    "ticket.stale-availability.v1": "ticket.stale_availability",
    "ticket.commit-then-timeout.v1": "ticket.duplicate_booking_after_unknown",
    "ticket.event-identity-confusion.v1": "ticket.event_identity_confusion",
    "ticket.price-fee-currency-drift.v1": "ticket.price_fee_currency_drift",
    "ticket.quantity-adjacency-violation.v1": (
        "ticket.quantity_adjacency_violation"
    ),
    "ticket.cancel-ambiguity.v1": "ticket.cancel_ambiguity",
}


@pytest.mark.parametrize(("fixture_id", "finding_id"), EXPECTED_FINDINGS.items())
def test_each_ticket_fault_has_one_atomic_fixture_and_paired_benign_control(
    fixture_id: str, finding_id: str
) -> None:
    gym = TicketGym.from_fixture(fixture_id, seed=17)
    assessment = gym.vulnerable_assessment()
    report = gym.diagnose(assessment)

    benign_gym = TicketGym.from_fixture(TICKET_BENIGN_FIXTURES[fixture_id], seed=17)
    benign = benign_gym.vulnerable_assessment()
    benign_report = benign_gym.diagnose(benign)

    assert report.finding_ids() == (finding_id,)
    assert assessment.task_succeeded is (fixture_id == "ticket.cancel-ambiguity.v1")
    assert report.findings[0].observed["first_divergence"]["seq"] >= 1
    assert all(finding.evidence_refs for finding in report.findings)
    assert benign.task_succeeded is True
    assert benign_report.passed is True


def test_ticket_full_demo_is_byte_stable_and_limited_to_three_faults() -> None:
    first_gym = TicketGym.from_fixture(TICKET_FULL_FIXTURE, seed=71)
    second_gym = TicketGym.from_fixture(TICKET_FULL_FIXTURE, seed=71)

    first_assessment = first_gym.vulnerable_assessment()
    second_assessment = second_gym.vulnerable_assessment()
    first = first_gym.diagnose(first_assessment)
    second = second_gym.diagnose(second_assessment)

    assert first.finding_ids() == (
        "ticket.duplicate_booking_after_unknown",
        "ticket.price_fee_currency_drift",
        "ticket.quantity_adjacency_violation",
    )
    assert first_assessment.to_json() == second_assessment.to_json()
    assert first_assessment.run_digest == second_assessment.run_digest
    assert first.to_json() == second.to_json()
    assert first.report_digest == second.report_digest
    assert first_assessment.tool_call_count <= MAX_TICKET_TOOL_CALL_BUDGET


def test_ticket_raw_tools_hide_fault_labels_but_ledger_keeps_commits() -> None:
    gym = TicketGym.from_fixture("ticket.commit-then-timeout.v1", seed=3)
    target = "event-seoul-0815-1900"
    inventory = gym.call("ticket.inventory.read", event_id=target)
    hold = gym.call(
        "ticket.hold.create",
        event_id=target,
        seat_ids=[f"{target}-B1", f"{target}-B2"],
        idempotency_key="hold-1",
    )
    purchase = gym.call(
        "ticket.purchase.confirm",
        hold_id=hold["hold_id"],
        idempotency_key="purchase-1",
    )

    assert "injected_faults" not in inventory
    assert "fault_id" not in purchase
    assert purchase == {
        "ok": False,
        "error": "GATEWAY_TIMEOUT",
        "state": "unknown",
        "hold_id": hold["hold_id"],
    }
    assert len(gym.world.bookings) == 1
    assert len(gym.world.charges) == 1
    assert [entry.effect for entry in gym.ledger.entries] == [
        "seat_hold_commit",
        "booking_charge_commit",
    ]
    assert gym.controller_ground_truth()["injected_faults"] == [
        "commit_then_timeout"
    ]


@pytest.mark.parametrize("fixture_id", TICKET_FAILURE_FIXTURES)
def test_ticket_protected_replay_preserves_mission_and_rejects_blanket_block(
    fixture_id: str,
) -> None:
    first = ticket_protected_replay(fixture_id, seed=29)
    second = ticket_protected_replay(fixture_id, seed=29)

    assert first.vulnerable_report.findings
    assert first.protected_report.passed is True
    assert first.benign_report.passed is True
    assert first.initial_snapshot_match is True
    assert first.protected_mission_succeeds is True
    assert first.benign_control_succeeds is True
    assert first.blanket_block_rejected is True
    assert first.blanket_block.booking_count == 0
    assert first.blanket_block.task_succeeded is False
    assert first.accepted is True
    assert first.to_json() == second.to_json()
    assert first.report_digest == second.report_digest


def test_disabled_ticket_policy_fails_the_protected_gate() -> None:
    report = ticket_protected_replay(
        TICKET_FULL_FIXTURE,
        seed=29,
        policy=TicketReplayPolicy(
            reconcile_unknown_purchase=False,
            enforce_fee_inclusive_budget=False,
            require_adjacent_seats=False,
        ),
    )

    assert report.protected_report.finding_ids() == (
        "ticket.duplicate_booking_after_unknown",
        "ticket.price_fee_currency_drift",
        "ticket.quantity_adjacency_violation",
    )
    assert report.protected_mission_succeeds is False
    assert report.accepted is False


def test_purchase_idempotency_and_reconciliation_keep_one_booking_and_charge() -> None:
    gym = TicketGym.from_fixture("ticket.clean-control.v1", seed=5)
    target = "event-seoul-0815-1900"
    hold = gym.call(
        "ticket.hold.create",
        event_id=target,
        seat_ids=[f"{target}-B1", f"{target}-B2"],
        idempotency_key="hold-stable",
    )
    first = gym.call(
        "ticket.purchase.confirm",
        hold_id=hold["hold_id"],
        idempotency_key="purchase-stable",
    )
    second = gym.call(
        "ticket.purchase.confirm",
        hold_id=hold["hold_id"],
        idempotency_key="purchase-stable",
    )
    retrieved = gym.call(
        "ticket.booking.retrieve", idempotency_key="purchase-stable"
    )

    assert second["idempotent_replay"] is True
    assert first["booking_id"] == second["booking_id"] == retrieved["booking_id"]
    assert len(gym.world.bookings) == 1
    assert len(gym.world.charges) == 1


def test_expired_hold_cannot_create_a_booking_or_charge() -> None:
    gym = TicketGym.from_fixture("ticket.stale-availability.v1", seed=9)
    target = "event-seoul-0815-1900"
    hold = gym.call(
        "ticket.hold.create",
        event_id=target,
        seat_ids=[f"{target}-B1", f"{target}-B2"],
    )

    gym.advance_time(gym.fixture.hold_ttl_seconds + 1)
    observed = gym.call("ticket.hold.retrieve", hold_id=hold["hold_id"])
    purchase = gym.call("ticket.purchase.confirm", hold_id=hold["hold_id"])

    assert observed["status"] == "expired"
    assert purchase["error"] == "HOLD_EXPIRED"
    assert gym.world.bookings == {}
    assert gym.world.charges == {}


def test_ticket_tool_surface_and_domain_adapter_are_registry_ready() -> None:
    gym = TicketGym.from_fixture()
    names = {tool.name for tool in gym.tools()}
    metadata = gym.pack_metadata()

    assert names == {
        "ticket_event_search",
        "ticket_inventory_read",
        "ticket_hold_create",
        "ticket_hold_retrieve",
        "ticket_purchase_confirm",
        "ticket_booking_retrieve",
        "ticket_hold_cancel",
        "ticket_booking_cancel",
    }
    assert metadata["domain_kind"] == "ticket"
    assert metadata["max_tool_calls"] == MAX_TICKET_TOOL_CALL_BUDGET
    assert TicketDomainPackAdapter.supports(
        list(TicketDomainPackAdapter.required_tool_capabilities)
    )
    assert not TicketDomainPackAdapter.supports({"web.search"})
    assert TicketDomainPackAdapter.build(seed=71).fixture.seed == 71


def test_ticket_pack_never_needs_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("ticket synthetic pack attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    replay = ticket_protected_replay("ticket.commit-then-timeout.v1", seed=11)

    assert replay.accepted is True


def test_ticket_tool_budget_is_enforced() -> None:
    gym = TicketGym.from_fixture("ticket.clean-control.v1")

    for _ in range(MAX_TICKET_TOOL_CALL_BUDGET):
        gym.call("ticket.event.search", query="Midnight Echoes Live")

    with pytest.raises(RuntimeError, match="budget exhausted"):
        gym.call("ticket.event.search", query="Midnight Echoes Live")


def test_ticket_diagnosis_rejects_an_assessment_from_another_fixture() -> None:
    source = TicketGym.from_fixture("ticket.commit-then-timeout.v1")
    target = TicketGym.from_fixture("ticket.stale-availability.v1")

    with pytest.raises(ValueError, match="does not match"):
        target.diagnose(source.vulnerable_assessment())


def test_protected_replay_rejects_a_clean_fixture() -> None:
    with pytest.raises(ValueError, match="requires a ticket failure fixture"):
        ticket_protected_replay("ticket.clean-control.v1")
