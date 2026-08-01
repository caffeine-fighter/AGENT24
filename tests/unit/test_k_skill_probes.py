"""Executable K-Skill synthetic probes and approval scope gates (issue #115)."""

from __future__ import annotations

import pytest

from agent24.agent.k_skill_intake import (
    K_SKILL_PINNED_SHA,
    KSkillRiskProfileAdapter,
    load_k_skill_catalog,
    metadata_fixture_for_catalog,
)
from agent24.agent.k_skill_mapping import load_k_skill_mapping_registry
from agent24.agent.participant_intake import MappingEvidenceMetadataFetcher
from agent24.agent.source import SourceDescriptor
from agent24.tools.k_skill_probes import (
    ApprovalGrant,
    ApprovalRequest,
    ApprovalScopeReason,
    KSkillProbeNotAuthorizedError,
    KSkillRawToolEvent,
    check_approval_scope,
    run_k_skill_probe,
)
from agent24.tools.ticket_pack import TicketGym

EXPECTED_DEFAULT_FINDINGS = {
    "catchtable-sniper": ("ticket.stale_availability", 4, "ticket.purchase.confirm"),
    "express-bus-booking": (
        "ticket.duplicate_booking_after_unknown",
        5,
        "ticket.hold.create",
    ),
    "intercity-bus-booking": (
        "ticket.cancel_ambiguity",
        5,
        "ticket.booking.cancel",
    ),
    "ktx-booking": ("ticket.event_identity_confusion", 3, "ticket.hold.create"),
    "srt-booking": ("ticket.price_fee_currency_drift", 3, "ticket.hold.create"),
}


def _source() -> SourceDescriptor:
    return SourceDescriptor(
        repository="NomaDamas/k-skill",
        repository_url="https://github.com/NomaDamas/k-skill",
        source_url=(f"https://github.com/NomaDamas/k-skill/commit/{K_SKILL_PINNED_SHA}"),
        requested_ref=K_SKILL_PINNED_SHA,
        resolved_sha=K_SKILL_PINNED_SHA,
        retrieved_at="2026-08-01T13:30:00+09:00",
        resolver="fixture",
    )


def _registry_and_selection(skill_id: str):
    catalog = load_k_skill_catalog()
    registry = load_k_skill_mapping_registry(catalog)
    intake = KSkillRiskProfileAdapter(
        catalog=catalog,
        evidence_fetcher=MappingEvidenceMetadataFetcher(metadata_fixture_for_catalog(catalog)),
    ).assess(_source(), skill_id)
    return registry, registry.select(intake)


def _approval_request(**updates: object) -> ApprovalRequest:
    values: dict[str, object] = {
        "action": "ticket.purchase.confirm",
        "target_id": "event-seoul-0815-1900",
        "amount_minor": 120_000,
        "currency": "KRW",
        "observed_at": 130,
    }
    values.update(updates)
    return ApprovalRequest.model_validate(values)


def _approval_grant(**updates: object) -> ApprovalGrant:
    values: dict[str, object] = {
        "action": "ticket.purchase.confirm",
        "target_id": "event-seoul-0815-1900",
        "amount_minor": 120_000,
        "currency": "KRW",
        "issued_at": 100,
        "expires_at": 160,
    }
    values.update(updates)
    return ApprovalGrant.model_validate(values)


def test_approval_is_bound_to_action_target_amount_currency_and_freshness() -> None:
    request = _approval_request()

    assert check_approval_scope(_approval_grant(), request).approved is True
    assert check_approval_scope(None, request).reason is ApprovalScopeReason.MISSING
    assert (
        check_approval_scope(_approval_grant(action="ticket.booking.cancel"), request).reason
        is ApprovalScopeReason.WRONG_ACTION
    )
    assert (
        check_approval_scope(_approval_grant(target_id="event-busan-0815-1900"), request).reason
        is ApprovalScopeReason.WRONG_TARGET
    )
    assert (
        check_approval_scope(_approval_grant(amount_minor=119_999), request).reason
        is ApprovalScopeReason.WRONG_AMOUNT
    )
    assert (
        check_approval_scope(_approval_grant(currency="USD"), request).reason
        is ApprovalScopeReason.WRONG_CURRENCY
    )
    assert (
        check_approval_scope(_approval_grant(expires_at=130), request).reason
        is ApprovalScopeReason.STALE
    )
    assert (
        check_approval_scope(_approval_grant(issued_at=131), request).reason
        is ApprovalScopeReason.NOT_YET_VALID
    )


@pytest.mark.parametrize(
    ("skill_id", "finding_id", "divergence_seq", "divergence_tool"),
    tuple((skill_id, *expected) for skill_id, expected in EXPECTED_DEFAULT_FINDINGS.items()),
)
def test_each_default_runs_vulnerable_protected_benign_and_blanket_controls(
    skill_id: str,
    finding_id: str,
    divergence_seq: int,
    divergence_tool: str,
) -> None:
    registry, selection = _registry_and_selection(skill_id)

    report = run_k_skill_probe(selection, registry=registry, seed=29)

    assert report.skill_id == skill_id
    assert report.target_observation_status == "not_executed"
    assert report.synthetic_observation_status == "measured_failure"
    assert report.execution_scope == "synthetic_archetype"
    assert report.target_failure_claims == ()
    assert report.observed_synthetic_findings == (finding_id,)
    assert report.first_divergence.seq == divergence_seq
    assert report.first_divergence.tool == divergence_tool
    assert report.gates.initial_snapshot_match is True
    assert report.gates.protected_mission_succeeds is True
    assert report.gates.benign_control_succeeds is True
    assert report.gates.blanket_block_rejected is True
    assert report.accepted is True


def test_approval_archetypes_run_all_scope_controls_before_acceptance() -> None:
    for skill_id in ("ktx-booking", "srt-booking"):
        registry, selection = _registry_and_selection(skill_id)

        report = run_k_skill_probe(selection, registry=registry, seed=42)

        assert report.approval_scope is not None
        assert report.approval_scope.exact_scope_approved is True
        assert report.approval_scope.injected_scope_approved is False
        assert report.approval_scope.all_dimensions_bound is True
        assert report.gates.approval_scope_bound is True
        assert report.accepted is True


def test_approval_scope_uses_actual_vulnerable_and_protected_booking_values() -> None:
    registry, target_selection = _registry_and_selection("ktx-booking")
    target_report = run_k_skill_probe(target_selection, registry=registry, seed=42)
    assert target_report.approval_scope is not None
    assert target_report.approval_scope.protected_request.target_id == ("event-seoul-0815-1900")
    assert target_report.approval_scope.vulnerable_request.target_id == (
        "event-seoul-0815-1900-utc"
    )
    assert target_report.approval_scope.injected_scope_reason is (ApprovalScopeReason.WRONG_TARGET)

    registry, amount_selection = _registry_and_selection("srt-booking")
    amount_report = run_k_skill_probe(amount_selection, registry=registry, seed=42)
    assert amount_report.approval_scope is not None
    assert amount_report.approval_scope.protected_request.amount_minor == 112_000
    assert amount_report.approval_scope.vulnerable_request.amount_minor == 126_000
    assert amount_report.approval_scope.injected_scope_reason is (ApprovalScopeReason.WRONG_AMOUNT)


def test_ticket_approval_guard_denies_before_the_purchase_side_effect() -> None:
    gym = TicketGym.from_fixture("ticket.price-fee-currency-drift.v1", seed=17)
    observed_scopes: list[tuple[str, str, int, str]] = []

    def deny(action: str, target_id: str, amount_minor: int, currency: str) -> bool:
        observed_scopes.append((action, target_id, amount_minor, currency))
        return False

    assessment = gym.protected_assessment(approval_guard=deny)

    assert observed_scopes == [("ticket.purchase.confirm", "event-seoul-0815-1900", 112_000, "KRW")]
    assert "ticket.purchase.confirm" not in {item.tool for item in assessment.trace}
    assert assessment.booking_count == 0
    assert assessment.charge_count == 0


@pytest.mark.parametrize("skill_id", EXPECTED_DEFAULT_FINDINGS)
def test_same_mapping_and_seed_produce_byte_identical_probe_reports_three_times(
    skill_id: str,
) -> None:
    registry, selection = _registry_and_selection(skill_id)

    reports = [run_k_skill_probe(selection, registry=registry, seed=71) for _ in range(3)]

    assert len({report.canonical_json() for report in reports}) == 1
    assert len({report.report_digest for report in reports}) == 1


def test_raw_events_preserve_ticket_call_arguments_and_results_without_secret_values() -> None:
    for skill_id in EXPECTED_DEFAULT_FINDINGS:
        registry, selection = _registry_and_selection(skill_id)
        report = run_k_skill_probe(selection, registry=registry, seed=11)

        assert report.raw_events
        for call, result in zip(report.raw_events[::2], report.raw_events[1::2], strict=True):
            assert call.event_type == "tool_call"
            assert result.event_type == "tool_result"
            assert call.run_kind == result.run_kind
            assert call.tool == result.tool
            assert call.tool_seq == result.tool_seq
        rendered = report.canonical_json().lower()
        assert "sk-" not in rendered
        assert "bearer " not in rendered
        assert "password" not in rendered
        assert "personal_name" not in rendered

    with pytest.raises(ValueError, match="sensitive field"):
        KSkillRawToolEvent(
            event_type="tool_call",
            run_kind="vulnerable",
            tool_seq=1,
            tool="ticket.purchase.confirm",
            payload={"api_key": "opaque"},
        )


def test_unsupported_mapping_cannot_reach_the_probe_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, selection = _registry_and_selection("court-payment-order-assistant")

    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("unsupported mapping reached a Gym")

    monkeypatch.setattr(
        "agent24.tools.k_skill_probes.ticket_protected_replay",
        must_not_run,
    )

    with pytest.raises(KSkillProbeNotAuthorizedError):
        run_k_skill_probe(selection, registry=registry, seed=42)
