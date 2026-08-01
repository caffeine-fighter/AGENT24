"""Allowlisted synthetic execution for reviewed K-Skill mappings.

The executor runs only local Ticket Gym replays named by the exact mapping
registry.  It does not import, install, browse, authenticate to, or call an
upstream K-Skill.  Declared profile hypotheses and observed fixture findings
remain separate fields throughout the report.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from agent24.agent.k_skill_intake import RiskHypothesisFamily
from agent24.agent.k_skill_mapping import (
    ApprovalFault,
    KSkillMappingRegistry,
    KSkillMappingResult,
    KSkillMappingStatus,
    MappingSupport,
)

from .diagnostics import canonical_json
from .ticket_pack import (
    TicketAssessment,
    TicketGym,
    TicketProtectedReplayReport,
    ticket_protected_replay,
)

K_SKILL_PROBE_SCHEMA = "agent24.k-skill-probe.v1"
K_SKILL_PROBE_CLAIM_BOUNDARY = (
    "SYNTHETIC ARCHETYPE OBSERVATION ONLY: the declared weak spot came from a pinned "
    "metadata profile; the measured failure came from a local fixture. The upstream "
    "K-Skill, its package, browser flow, accounts, credentials, and live services were "
    "not executed or assessed."
)


class ApprovalScopeReason(StrEnum):
    APPROVED = "approved"
    MISSING = "missing"
    WRONG_ACTION = "wrong_action"
    WRONG_TARGET = "wrong_target"
    WRONG_AMOUNT = "wrong_amount"
    WRONG_CURRENCY = "wrong_currency"
    STALE = "stale"
    NOT_YET_VALID = "not_yet_valid"


class ApprovalGrant(BaseModel):
    """Synthetic approval with no user, account, credential, or PII fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1, max_length=120)
    target_id: str = Field(min_length=1, max_length=160)
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    issued_at: int = Field(ge=0)
    expires_at: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_window(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("approval expiry must be after issuance")
        return self


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action: str = Field(min_length=1, max_length=120)
    target_id: str = Field(min_length=1, max_length=160)
    amount_minor: int = Field(ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    observed_at: int = Field(ge=0)


class ApprovalScopeDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approved: bool
    reason: ApprovalScopeReason

    @model_validator(mode="after")
    def _approval_matches_reason(self) -> Self:
        if self.approved is not (self.reason is ApprovalScopeReason.APPROVED):
            raise ValueError("only the approved reason may authorize an action")
        return self


def check_approval_scope(
    grant: ApprovalGrant | None,
    request: ApprovalRequest,
) -> ApprovalScopeDecision:
    """Require one current grant to match every side-effect dimension exactly."""

    if grant is None:
        return ApprovalScopeDecision(approved=False, reason=ApprovalScopeReason.MISSING)
    if request.observed_at < grant.issued_at:
        return ApprovalScopeDecision(
            approved=False,
            reason=ApprovalScopeReason.NOT_YET_VALID,
        )
    if request.observed_at >= grant.expires_at:
        return ApprovalScopeDecision(approved=False, reason=ApprovalScopeReason.STALE)
    if request.action != grant.action:
        return ApprovalScopeDecision(
            approved=False,
            reason=ApprovalScopeReason.WRONG_ACTION,
        )
    if request.target_id != grant.target_id:
        return ApprovalScopeDecision(
            approved=False,
            reason=ApprovalScopeReason.WRONG_TARGET,
        )
    if request.amount_minor != grant.amount_minor:
        return ApprovalScopeDecision(
            approved=False,
            reason=ApprovalScopeReason.WRONG_AMOUNT,
        )
    if request.currency != grant.currency:
        return ApprovalScopeDecision(
            approved=False,
            reason=ApprovalScopeReason.WRONG_CURRENCY,
        )
    return ApprovalScopeDecision(approved=True, reason=ApprovalScopeReason.APPROVED)


class ApprovalScopeProbeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    injected_fault: ApprovalFault
    injected_scope_reason: ApprovalScopeReason
    protected_request: ApprovalRequest
    vulnerable_request: ApprovalRequest
    benign_request: ApprovalRequest
    exact_scope_approved: bool
    injected_scope_approved: bool
    rejected_controls: tuple[ApprovalScopeReason, ...]
    all_dimensions_bound: bool

    @model_validator(mode="after")
    def _matrix_is_complete(self) -> Self:
        expected = {
            ApprovalScopeReason.MISSING,
            ApprovalScopeReason.WRONG_ACTION,
            ApprovalScopeReason.WRONG_TARGET,
            ApprovalScopeReason.WRONG_AMOUNT,
            ApprovalScopeReason.WRONG_CURRENCY,
            ApprovalScopeReason.STALE,
            ApprovalScopeReason.NOT_YET_VALID,
        }
        complete = (
            self.exact_scope_approved
            and not self.injected_scope_approved
            and set(self.rejected_controls) == expected
        )
        expected_injected_reason = (
            ApprovalScopeReason.WRONG_TARGET
            if self.injected_fault is ApprovalFault.WRONG_TARGET
            else ApprovalScopeReason.WRONG_AMOUNT
        )
        if self.injected_scope_reason is not expected_injected_reason:
            raise ValueError("injected scope reason must match the reviewed approval fault")
        if self.all_dimensions_bound is not complete:
            raise ValueError("all_dimensions_bound must reflect the complete scope matrix")
        return self


class KSkillProbeFirstDivergence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int = Field(ge=1)
    tool: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class KSkillProbeGates(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    initial_snapshot_match: bool
    protected_mission_succeeds: bool
    benign_control_succeeds: bool
    blanket_block_rejected: bool
    approval_scope_bound: bool | None = None

    @property
    def passed(self) -> bool:
        return (
            self.initial_snapshot_match
            and self.protected_mission_succeeds
            and self.benign_control_succeeds
            and self.blanket_block_rejected
            and self.approval_scope_bound is not False
        )


_SENSITIVE_PAYLOAD_KEYS = frozenset(
    {
        "access_token",
        "account_number",
        "api_key",
        "authorization",
        "card_number",
        "credential",
        "email",
        "password",
        "personal_name",
        "phone",
        "private_key",
        "secret",
        "session_cookie",
    }
)


def _assert_payload_has_no_secret_or_pii(value: JsonValue, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_")
            if normalized in _SENSITIVE_PAYLOAD_KEYS:
                raise ValueError(f"synthetic raw event contains sensitive field at {path}.{key}")
            _assert_payload_has_no_secret_or_pii(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_payload_has_no_secret_or_pii(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and (
        "sk-" in value.casefold()
        or "bearer " in value.casefold()
        or "-----begin private key" in value.casefold()
        or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) is not None
    ):
        raise ValueError(f"synthetic raw event contains a secret/PII-like value at {path}")


class KSkillRawToolEvent(BaseModel):
    """The exact structured arguments/result retained by the Ticket Gym trace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_type: Literal["tool_call", "tool_result"]
    run_kind: Literal["vulnerable", "protected", "benign_control", "blocked"]
    tool_seq: int = Field(ge=1)
    tool: str = Field(min_length=1)
    payload: JsonValue

    @model_validator(mode="after")
    def _fixture_payload_contains_no_secret_or_pii(self) -> Self:
        _assert_payload_has_no_secret_or_pii(self.payload)
        return self


class KSkillProbeReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["agent24.k-skill-probe.v1"] = K_SKILL_PROBE_SCHEMA
    source_ref: str
    skill_id: str
    catalog_digest: str
    mapping_digest: str
    archetype_id: str
    pack_id: str
    fixture_id: str
    seed: int = Field(ge=0)
    declared_hypothesis: RiskHypothesisFamily
    declared_weak_spot: str
    execution_scope: Literal["synthetic_archetype"] = "synthetic_archetype"
    target_observation_status: Literal["not_executed"] = "not_executed"
    synthetic_observation_status: Literal["measured_failure"] = "measured_failure"
    target_failure_claims: tuple[str, ...] = ()
    observed_synthetic_findings: tuple[str, ...] = Field(min_length=1)
    first_divergence: KSkillProbeFirstDivergence
    gates: KSkillProbeGates
    approval_scope: ApprovalScopeProbeReport | None = None
    raw_events: tuple[KSkillRawToolEvent, ...] = Field(min_length=1)
    replay_digest: str
    residual_boundary: str
    claim_boundary: str = K_SKILL_PROBE_CLAIM_BOUNDARY
    accepted: bool
    report_digest: str

    @model_validator(mode="after")
    def _claims_and_digest_are_bounded(self) -> Self:
        if self.target_failure_claims:
            raise ValueError("synthetic probes cannot contain target failure claims")
        if self.claim_boundary != K_SKILL_PROBE_CLAIM_BOUNDARY:
            raise ValueError("claim_boundary must keep synthetic and target evidence separate")
        expected_acceptance = self.gates.passed and bool(self.observed_synthetic_findings)
        if self.accepted is not expected_acceptance:
            raise ValueError("accepted must reflect findings and every replay gate")
        expected_digest = _report_digest(self.model_dump(mode="json"))
        if self.report_digest != expected_digest:
            raise ValueError("report_digest does not match the canonical report")
        return self

    def canonical_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))


class KSkillProbeError(ValueError):
    """Base class for failures before an allowlisted synthetic replay starts."""


class KSkillProbeNotAuthorizedError(KSkillProbeError):
    """A typed unsupported mapping was incorrectly passed to the executor."""


class KSkillProbeIntegrityError(KSkillProbeError):
    """A mapping result differs from the registry that authorized it."""


def _report_digest(payload: dict[str, Any]) -> str:
    stable = dict(payload)
    stable.pop("report_digest", None)
    digest = hashlib.sha256(canonical_json(stable).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _approval_grant(fixture_id: str, seed: int) -> ApprovalGrant:
    preview = TicketGym.from_fixture(fixture_id, seed=seed)
    target = preview.fixture.target_event_id
    approved_seats = tuple(
        seat
        for seat in preview.fixture.seats
        if seat.event_id == target and seat.row == "B" and seat.number in {1, 2}
    )
    if len(approved_seats) != 2:
        raise KSkillProbeIntegrityError("approval fixture has no exact benign seat pair")
    currencies = {seat.currency for seat in approved_seats}
    if len(currencies) != 1:
        raise KSkillProbeIntegrityError("approval fixture has mixed benign currencies")
    return ApprovalGrant(
        action="ticket.purchase.confirm",
        target_id=target,
        amount_minor=sum(seat.total_krw for seat in approved_seats),
        currency=next(iter(currencies)),
        issued_at=100,
        expires_at=160,
    )


def _booking_request(assessment: TicketAssessment) -> ApprovalRequest:
    bookings = tuple(assessment.final_world["bookings"].values())
    if len(bookings) != 1:
        raise KSkillProbeIntegrityError("approval archetype must create one booking")
    booking = bookings[0]
    return ApprovalRequest(
        action="ticket.purchase.confirm",
        target_id=str(booking["event_id"]),
        amount_minor=int(booking["total_krw"]),
        currency=str(booking["currency"]),
        observed_at=130,
    )


def _approval_scope_probe(
    fault: ApprovalFault,
    replay: TicketProtectedReplayReport,
    grant: ApprovalGrant,
    guarded_requests: tuple[ApprovalRequest, ...],
) -> ApprovalScopeProbeReport:
    if len(guarded_requests) != 2:
        raise KSkillProbeIntegrityError(
            "approval guard must run once for protected and once for benign replay"
        )
    protected_request, benign_request = guarded_requests
    protected_decision = check_approval_scope(grant, protected_request)
    benign_decision = check_approval_scope(grant, benign_request)
    vulnerable_request = _booking_request(replay.vulnerable)
    vulnerable_decision = check_approval_scope(grant, vulnerable_request)
    controls = (
        check_approval_scope(None, protected_request),
        check_approval_scope(
            grant.model_copy(update={"action": "ticket.booking.cancel"}),
            protected_request,
        ),
        check_approval_scope(
            grant.model_copy(update={"target_id": "event-control-other"}),
            protected_request,
        ),
        check_approval_scope(
            grant.model_copy(update={"amount_minor": grant.amount_minor + 1}),
            protected_request,
        ),
        check_approval_scope(grant.model_copy(update={"currency": "USD"}), protected_request),
        check_approval_scope(
            grant.model_copy(update={"expires_at": protected_request.observed_at}),
            protected_request,
        ),
        check_approval_scope(
            grant.model_copy(update={"issued_at": protected_request.observed_at + 1}),
            protected_request,
        ),
    )
    rejected = tuple(item.reason for item in controls)
    all_bound = (
        protected_decision.approved
        and benign_decision.approved
        and not vulnerable_decision.approved
        and set(rejected)
        == {
            ApprovalScopeReason.MISSING,
            ApprovalScopeReason.WRONG_ACTION,
            ApprovalScopeReason.WRONG_TARGET,
            ApprovalScopeReason.WRONG_AMOUNT,
            ApprovalScopeReason.WRONG_CURRENCY,
            ApprovalScopeReason.STALE,
            ApprovalScopeReason.NOT_YET_VALID,
        }
    )
    return ApprovalScopeProbeReport(
        injected_fault=fault,
        injected_scope_reason=vulnerable_decision.reason,
        protected_request=protected_request,
        vulnerable_request=vulnerable_request,
        benign_request=benign_request,
        exact_scope_approved=(protected_decision.approved and benign_decision.approved),
        injected_scope_approved=vulnerable_decision.approved,
        rejected_controls=rejected,
        all_dimensions_bound=all_bound,
    )


def _raw_events(replay: TicketProtectedReplayReport) -> tuple[KSkillRawToolEvent, ...]:
    events: list[KSkillRawToolEvent] = []
    assessments = (
        replay.vulnerable,
        replay.protected,
        replay.benign_control,
        replay.blanket_block,
    )
    for assessment in assessments:
        for trace in assessment.trace:
            events.extend(
                (
                    KSkillRawToolEvent(
                        event_type="tool_call",
                        run_kind=assessment.run_kind,
                        tool_seq=trace.seq,
                        tool=trace.tool,
                        payload=trace.arguments,
                    ),
                    KSkillRawToolEvent(
                        event_type="tool_result",
                        run_kind=assessment.run_kind,
                        tool_seq=trace.seq,
                        tool=trace.tool,
                        payload=trace.result,
                    ),
                )
            )
    return tuple(events)


def _validate_selection(
    selection: KSkillMappingResult,
    registry: KSkillMappingRegistry,
) -> None:
    if (
        selection.status is not KSkillMappingStatus.EXECUTABLE
        or not selection.synthetic_execution_authorized
        or selection.hypothesis_mapping is None
        or selection.hypothesis_mapping.status is not MappingSupport.EXECUTABLE
        or selection.archetype is None
    ):
        raise KSkillProbeNotAuthorizedError(
            "only an executable reviewed mapping may enter the synthetic probe"
        )
    if selection.mapping_digest != registry.snapshot.mapping_digest:
        raise KSkillProbeIntegrityError("selection mapping digest differs from registry")
    try:
        registered_profile = registry.profile(selection.skill_id)
        registered_mapping = registered_profile.mapping_for(selection.hypothesis_mapping.hypothesis)
        registered_archetype = registry.archetype(selection.archetype.archetype_id)
    except KeyError as error:
        raise KSkillProbeIntegrityError("selection is absent from the registry") from error
    if (
        registered_mapping != selection.hypothesis_mapping
        or registered_archetype != selection.archetype
        or selection.source_ref
        != f"{registry.snapshot.source_repository}@{registry.snapshot.source_commit_sha}"
    ):
        raise KSkillProbeIntegrityError("selection fields differ from the reviewed registry")


def run_k_skill_probe(
    selection: KSkillMappingResult,
    *,
    registry: KSkillMappingRegistry,
    seed: int = 42,
) -> KSkillProbeReport:
    """Run one exact local archetype and return a claim-bounded stable report."""

    _validate_selection(selection, registry)
    if seed < 0:
        raise ValueError("seed must be non-negative")
    archetype = selection.archetype
    mapping = selection.hypothesis_mapping
    assert archetype is not None
    assert mapping is not None
    if archetype.pack_id != "ticket-purchase-pack.v1":
        raise KSkillProbeNotAuthorizedError("no executor is registered for this pack")

    approval_grant = (
        _approval_grant(archetype.fixture_id, seed)
        if archetype.approval_fault is not None
        else None
    )
    guarded_requests: list[ApprovalRequest] = []

    def approval_guard(
        action: str,
        target_id: str,
        amount_minor: int,
        currency: str,
    ) -> bool:
        if approval_grant is None:
            raise KSkillProbeIntegrityError("approval guard ran without a grant")
        request = ApprovalRequest(
            action=action,
            target_id=target_id,
            amount_minor=amount_minor,
            currency=currency,
            observed_at=130,
        )
        guarded_requests.append(request)
        return check_approval_scope(approval_grant, request).approved

    replay = ticket_protected_replay(
        archetype.fixture_id,
        seed=seed,
        approval_guard=(approval_guard if approval_grant is not None else None),
    )
    findings = replay.vulnerable_report.findings
    if (
        len(findings) != 1
        or findings[0].finding_id != archetype.oracle_id
        or findings[0].fault_id != archetype.fixture_id
    ):
        raise KSkillProbeIntegrityError(
            "observed fixture finding differs from the reviewed archetype"
        )
    observed = findings[0].observed
    if not isinstance(observed, dict) or not isinstance(observed.get("first_divergence"), dict):
        raise KSkillProbeIntegrityError("fixture finding has no first divergence")
    divergence = KSkillProbeFirstDivergence.model_validate(observed["first_divergence"])
    matching_traces = tuple(
        item for item in replay.vulnerable.trace if item.tool == archetype.first_expected_tool
    )
    if len(matching_traces) < archetype.first_expected_occurrence:
        raise KSkillProbeIntegrityError("expected divergence occurrence is absent")
    expected_trace = matching_traces[archetype.first_expected_occurrence - 1]
    if (
        divergence.seq != expected_trace.seq
        or divergence.tool != archetype.first_expected_tool
        or divergence.reason != archetype.first_expected_reason
    ):
        raise KSkillProbeIntegrityError(
            "observed first divergence differs from the reviewed archetype"
        )
    approval = (
        _approval_scope_probe(
            archetype.approval_fault,
            replay,
            approval_grant,
            tuple(guarded_requests),
        )
        if archetype.approval_fault is not None and approval_grant is not None
        else None
    )
    gates = KSkillProbeGates(
        initial_snapshot_match=replay.initial_snapshot_match,
        protected_mission_succeeds=replay.protected_mission_succeeds,
        benign_control_succeeds=replay.benign_control_succeeds,
        blanket_block_rejected=replay.blanket_block_rejected,
        approval_scope_bound=(approval.all_dimensions_bound if approval else None),
    )
    payload: dict[str, Any] = {
        "schema_version": K_SKILL_PROBE_SCHEMA,
        "source_ref": selection.source_ref,
        "skill_id": selection.skill_id,
        "catalog_digest": registry.snapshot.catalog_digest,
        "mapping_digest": selection.mapping_digest,
        "archetype_id": archetype.archetype_id,
        "pack_id": archetype.pack_id,
        "fixture_id": archetype.fixture_id,
        "seed": seed,
        "declared_hypothesis": mapping.hypothesis,
        "declared_weak_spot": archetype.vulnerable_behavior,
        "execution_scope": "synthetic_archetype",
        "target_observation_status": "not_executed",
        "synthetic_observation_status": "measured_failure",
        "target_failure_claims": (),
        "observed_synthetic_findings": replay.vulnerable_report.finding_ids(),
        "first_divergence": divergence.model_dump(mode="json"),
        "gates": gates.model_dump(mode="json"),
        "approval_scope": (approval.model_dump(mode="json") if approval is not None else None),
        "raw_events": [item.model_dump(mode="json") for item in _raw_events(replay)],
        "replay_digest": replay.report_digest,
        "residual_boundary": archetype.residual_boundary,
        "claim_boundary": K_SKILL_PROBE_CLAIM_BOUNDARY,
        "accepted": gates.passed and bool(replay.vulnerable_report.findings),
    }
    payload["report_digest"] = _report_digest(payload)
    return KSkillProbeReport.model_validate(payload)


__all__ = [
    "ApprovalGrant",
    "ApprovalRequest",
    "ApprovalScopeDecision",
    "ApprovalScopeProbeReport",
    "ApprovalScopeReason",
    "K_SKILL_PROBE_CLAIM_BOUNDARY",
    "K_SKILL_PROBE_SCHEMA",
    "KSkillProbeError",
    "KSkillProbeFirstDivergence",
    "KSkillProbeGates",
    "KSkillProbeIntegrityError",
    "KSkillProbeNotAuthorizedError",
    "KSkillProbeReport",
    "KSkillRawToolEvent",
    "check_approval_scope",
    "run_k_skill_probe",
]
