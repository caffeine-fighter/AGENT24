"""Bounded OpenAI tool orchestration for one external-target diagnosis.

The Agents SDK owns the model/tool turn loop, while this module owns the state
machine behind the five function tools.  The controller never trusts a model
supplied operator, fault, oracle result, or finding: it accepts only IDs from
the per-run :func:`candidate_operators` set and derives all evidence from the
existing deterministic lab primitive.

This is intentionally a facade over ``DeterministicLabLoop`` in #101.  The
primitive still bundles baseline, fault, oracle, patch, and replay phases.  The
live model decides *whether and which* bounded experiment to run, then must
inspect and verify the controller-owned result before its final answer is
allowed through.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Protocol

from agents import Agent, function_tool
from pydantic import BaseModel, ConfigDict, Field

from agent24.events import RunChannel

from .loop import DiagnosticLoopResult
from .models import (
    DiagnosticFinalOutput,
    ExperimentPlan,
    FaultKind,
    StopDecision,
    canonical_json,
)
from .planner import OperatorCandidate, candidate_operators, plan_for_candidate
from .profile import BehaviorProfile
from .prompts import DIAGNOSTIC_CONTROLLER_INSTRUCTIONS, fence_untrusted

DIAGNOSTIC_TOOL_NAMES: tuple[str, ...] = (
    "inspect_target",
    "list_experiments",
    "run_sandbox_experiment",
    "inspect_evidence",
    "verify_mitigation",
)
DIAGNOSTIC_MAX_TOOL_CALLS = len(DIAGNOSTIC_TOOL_NAMES)
DIAGNOSTIC_MAX_BUDGET_UNITS = 64
NO_MITIGATION_REQUIRED = "no_mitigation_required"

BoundedText = Annotated[str, Field(min_length=1, max_length=500)]
BoundedId = Annotated[str, Field(min_length=1, max_length=160)]
BoundedEvidence = Annotated[list[BoundedText], Field(min_length=1, max_length=8)]
BoundedBudget = Annotated[int, Field(ge=1, le=DIAGNOSTIC_MAX_BUDGET_UNITS)]


class DiagnosticControllerError(RuntimeError):
    """A typed controller stop that must not be converted into a model answer."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class DiagnosticPreflightLike(Protocol):
    """The small preflight surface required by this controller."""

    source: Any
    mission: Any
    profile: BehaviorProfile
    decision: ExperimentPlan | StopDecision
    adapter_contract: Any | None


class DiagnosticDecisionArtifact(BaseModel):
    """Safe decision summary; it is explicitly not hidden chain-of-thought."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    phase: str
    source: Literal["openai_model", "deterministic_reference"]
    decision_summary: str = Field(min_length=1, max_length=500)
    expected_evidence: list[str] = Field(min_length=1, max_length=8)
    requested_budget: int = Field(ge=1, le=DIAGNOSTIC_MAX_BUDGET_UNITS)
    budget_limit: int = Field(ge=1, le=DIAGNOSTIC_MAX_BUDGET_UNITS)
    budget_spent: int = Field(ge=0, le=DIAGNOSTIC_MAX_BUDGET_UNITS)
    fallback: str = Field(min_length=1, max_length=500)
    hidden_reasoning_excluded: Literal[True] = True


class PlannerReferenceArtifact(BaseModel):
    """The deterministic policy exposed as an explicit reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_plan_id: str
    reference_operator_id: str
    reference_fault_id: str
    candidate_operator_ids: list[str]
    fallback_policy: Literal["same_target_reference"] = "same_target_reference"
    fallback_reason: str = Field(min_length=1, max_length=500)


class PlannerComparisonArtifact(BaseModel):
    """The selected live plan compared with the deterministic reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reference_plan_id: str
    reference_operator_id: str
    live_plan_id: str | None = None
    live_operator_id: str | None = None
    same_selection: bool
    differences: list[str]
    fallback_policy: Literal["none", "same_target_reference"]
    fallback_reason: str | None = None


class DiagnosticStateArtifact(BaseModel):
    """A separately maintained, safe controller-state projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    phase: Literal[
        "initial",
        "target_inspected",
        "candidates_listed",
        "experiment_run",
        "evidence_inspected",
        "mitigation_verified",
        "rejected",
    ]
    accepted_tool_calls: int = Field(ge=0, le=DIAGNOSTIC_MAX_TOOL_CALLS)
    rejected_tool_calls: int = Field(ge=0)
    candidate_operator_ids: list[str]
    reference_plan_id: str
    live_plan_id: str | None = None
    evidence_id: str | None = None
    evidence_inspected: bool
    mitigation_verified: bool
    budget_limit: int
    budget_spent: int
    rejection_code: str | None = None


@dataclass
class _ControllerState:
    phase: str = "initial"
    accepted_tool_calls: int = 0
    rejected_tool_calls: int = 0
    candidates: dict[str, OperatorCandidate] = field(default_factory=dict)
    reference_plan: ExperimentPlan | None = None
    selected_plan: ExperimentPlan | None = None
    result: DiagnosticLoopResult | None = None
    evidence_id: str | None = None
    evidence_inspected: bool = False
    mitigation_id: str | None = None
    mitigation_verified: bool = False
    budget_spent: int = 0
    rejected: bool = False
    rejection_code: str | None = None


def _strict_schema_error(name: str, schema: dict[str, Any]) -> str | None:
    if schema.get("type") != "object":
        return f"{name} schema is not an object"
    if schema.get("additionalProperties") is not False:
        return f"{name} schema must set additionalProperties=false"
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict) or not isinstance(required, list):
        return f"{name} schema must declare properties and required"
    if set(required) != set(properties):
        return f"{name} schema must require every property; optional fields must be nullable"
    return None


def assert_strict_diagnostic_tools(tools: list[Any]) -> None:
    """Fail closed if the emitted tool set drifts from the strict contract."""

    names = tuple(tool.name for tool in tools)
    if names != DIAGNOSTIC_TOOL_NAMES:
        raise RuntimeError(f"diagnostic tool set drifted: {names!r}")
    for tool in tools:
        if not tool.strict_json_schema:
            raise RuntimeError(f"{tool.name} is not strict")
        error = _strict_schema_error(tool.name, tool.params_json_schema)
        if error:
            raise RuntimeError(error)


def assert_strict_diagnostic_output() -> None:
    error = _strict_schema_error(
        DiagnosticFinalOutput.__name__, DiagnosticFinalOutput.model_json_schema()
    )
    if error:
        raise RuntimeError(error)


def _json(payload: Any) -> str:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json", exclude_none=True)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DiagnosticToolController:
    """Stateful bounded facade used by an Agents SDK multi-turn run."""

    def __init__(
        self,
        *,
        preflight: DiagnosticPreflightLike,
        execution_scope: str,
        channel: RunChannel,
        run_experiment: Callable[[ExperimentPlan], Awaitable[DiagnosticLoopResult]],
        allowed_faults: frozenset[FaultKind] | None = None,
        fallback_reason: str = "provider failure or an explicit controller stop",
    ) -> None:
        if not isinstance(preflight.decision, ExperimentPlan):
            raise DiagnosticControllerError(
                "reference_plan_missing", "a live diagnostic requires a reference experiment plan"
            )
        self.preflight = preflight
        self.execution_scope = execution_scope
        self.channel = channel
        self.run_experiment = run_experiment
        self.allowed_faults = allowed_faults
        self.fallback_reason = fallback_reason[:500]
        self.state = _ControllerState(reference_plan=preflight.decision)
        self._tools: list[Any] | None = None
        self._final: DiagnosticFinalOutput | None = None

    @property
    def result(self) -> DiagnosticLoopResult | None:
        return self.state.result

    @property
    def selected_plan(self) -> ExperimentPlan | None:
        return self.state.selected_plan

    @property
    def final(self) -> DiagnosticFinalOutput | None:
        return self._final

    @property
    def reference_plan(self) -> ExperimentPlan:
        assert self.state.reference_plan is not None
        return self.state.reference_plan

    def render_input(self, query: str) -> str:
        """Give the model only target intake; experiment evidence comes from tools."""

        payload = {
            "target_ref": self.preflight.source.source_ref,
            "mission": self.preflight.mission.model_dump(mode="json"),
            "execution_scope": self.execution_scope,
            "controller_budget_units": DIAGNOSTIC_MAX_BUDGET_UNITS,
            "reference_policy": "deterministic_reference_only_until_live_selection",
        }
        return (
            f"{query}\n\nDIAGNOSTIC CONTROLLER INPUT (no experiment has run yet):\n"
            f"{fence_untrusted(payload)}\n"
            "Start by calling inspect_target with the exact target_ref."
        )

    def _publish_state(self) -> None:
        payload = DiagnosticStateArtifact(
            phase=self.state.phase,  # type: ignore[arg-type]
            accepted_tool_calls=self.state.accepted_tool_calls,
            rejected_tool_calls=self.state.rejected_tool_calls,
            candidate_operator_ids=sorted(self.state.candidates),
            reference_plan_id=self.reference_plan.plan_id,
            live_plan_id=(self.state.selected_plan.plan_id if self.state.selected_plan else None),
            evidence_id=self.state.evidence_id,
            evidence_inspected=self.state.evidence_inspected,
            mitigation_verified=self.state.mitigation_verified,
            budget_limit=DIAGNOSTIC_MAX_BUDGET_UNITS,
            budget_spent=self.state.budget_spent,
            rejection_code=self.state.rejection_code,
        )
        self.channel.publish("diagnostic.state", payload, summary=payload.phase)

    def _reject(self, code: str, detail: str) -> str:
        self.state.rejected = True
        self.state.phase = "rejected"
        self.state.rejection_code = code
        self.state.rejected_tool_calls += 1
        self.channel.publish(
            "diagnostic.rejected",
            {"code": code, "detail": detail[:500], "tool_calls": self.state.accepted_tool_calls},
            summary=code,
        )
        self._publish_state()
        return _json({"status": "rejected", "code": code, "detail": detail[:500]})

    def _record_decision(
        self,
        *,
        tool: str,
        phase: str,
        decision_summary: str,
        expected_evidence: list[str],
        budget: int,
        fallback: str,
    ) -> bool:
        if not decision_summary.strip() or not fallback.strip() or not expected_evidence:
            self._reject(
                "invalid_rationale",
                "decision_summary, expected_evidence, and fallback are required",
            )
            return False
        if budget < 1 or budget > DIAGNOSTIC_MAX_BUDGET_UNITS:
            self._reject("budget_exceeded", "requested budget exceeds the controller limit")
            return False
        if len(expected_evidence) > 8 or any(not item.strip() for item in expected_evidence):
            self._reject("invalid_rationale", "expected_evidence is not a bounded non-empty list")
            return False
        artifact = DiagnosticDecisionArtifact(
            tool=tool,
            phase=phase,
            source="openai_model",
            decision_summary=decision_summary[:500],
            expected_evidence=[item[:500] for item in expected_evidence],
            requested_budget=budget,
            budget_limit=DIAGNOSTIC_MAX_BUDGET_UNITS,
            budget_spent=self.state.budget_spent,
            fallback=fallback[:500],
        )
        self.channel.publish("diagnostic.rationale", artifact, summary=tool)
        return True

    def _require_phase(self, phase: str, tool: str) -> bool:
        if self.state.rejected:
            self._reject(
                "controller_already_rejected",
                "the run is permanently rejected after a boundary violation",
            )
            return False
        if self.state.phase != phase:
            self._reject(
                "invalid_tool_sequence",
                f"{tool} is not allowed in controller phase {self.state.phase}",
            )
            return False
        if self.state.accepted_tool_calls >= DIAGNOSTIC_MAX_TOOL_CALLS:
            self._reject(
                "tool_budget_exceeded", "the five-tool controller call budget is exhausted"
            )
            return False
        return True

    def _target_payload(self) -> dict[str, Any]:
        profile = self.preflight.profile
        assessments = profile.assessments()
        return {
            "target_ref": self.preflight.source.source_ref,
            "mission": self.preflight.mission.model_dump(mode="json"),
            "execution_scope": self.execution_scope,
            "profile": {
                "agent_name": profile.agent_name,
                "mission_family": profile.mission_family.value,
                "protected_assets": sorted(profile.protected_assets),
                "side_effect_tools": sorted(profile.side_effect_tools),
                "capabilities": [cap.model_dump(mode="json") for cap in profile.capabilities],
                "assessments": {
                    name: assessment.model_dump(mode="json")
                    for name, assessment in sorted(assessments.items())
                },
            },
            "reference_plan_id": self.reference_plan.plan_id,
        }

    async def _inspect_target(
        self,
        target_ref: str,
        decision_summary: str,
        expected_evidence: list[str],
        budget: int,
        fallback: str,
    ) -> str:
        if not self._require_phase("initial", "inspect_target"):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if not self._record_decision(
            tool="inspect_target",
            phase="initial",
            decision_summary=decision_summary,
            expected_evidence=expected_evidence,
            budget=budget,
            fallback=fallback,
        ):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if target_ref != self.preflight.source.source_ref:
            return self._reject(
                "target_not_allowlisted", "target_ref is not the pinned source for this run"
            )
        self.state.accepted_tool_calls += 1
        self.state.phase = "target_inspected"
        payload = self._target_payload()
        self.channel.publish("diagnostic.target", payload, summary="target inspected")
        self._publish_state()
        return _json({"status": "accepted", "target": payload, "next": "list_experiments"})

    def _candidate_payload(self, candidate: OperatorCandidate) -> dict[str, Any]:
        operator = candidate.operator
        return {
            "operator_id": candidate.operator_id,
            "fault_id": operator.fault.value,
            "target_tool": operator.target_tool,
            "gate_field": candidate.gate_field,
            "gate_value": candidate.gate_value,
            "score": candidate.score,
            "hypothesis_id": candidate.hypothesis.hypothesis_id,
            "target_invariants": list(candidate.hypothesis.target_invariants),
            "expected_evidence": operator.expected_observation,
            "estimated_cost_units": candidate.hypothesis.est_cost_units,
            "max_turns": operator.max_turns,
            "rationale": candidate.rationale,
        }

    def _reference_operator_id(self) -> str | None:
        for candidate in self.state.candidates.values():
            if (
                plan_for_candidate(candidate, self.preflight.mission).plan_id
                == self.reference_plan.plan_id
            ):
                return candidate.operator_id
        return None

    async def _list_experiments(
        self,
        decision_summary: str,
        expected_evidence: list[str],
        budget: int,
        fallback: str,
    ) -> str:
        if not self._require_phase("target_inspected", "list_experiments"):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if not self._record_decision(
            tool="list_experiments",
            phase="target_inspected",
            decision_summary=decision_summary,
            expected_evidence=expected_evidence,
            budget=budget,
            fallback=fallback,
        ):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        candidates = candidate_operators(
            self.preflight.profile,
            self.preflight.mission,
            allowed_faults=self.allowed_faults,
        )
        if not candidates:
            return self._reject(
                "unsupported_operator_set", "candidate_operators returned no supported operator"
            )
        self.state.candidates = {candidate.operator_id: candidate for candidate in candidates}
        reference_operator_id = self._reference_operator_id()
        if reference_operator_id is None:
            return self._reject(
                "reference_plan_not_in_candidates",
                "the deterministic reference plan is not a member of this run's candidate set",
            )
        self.state.accepted_tool_calls += 1
        self.state.phase = "candidates_listed"
        reference = PlannerReferenceArtifact(
            reference_plan_id=self.reference_plan.plan_id,
            reference_operator_id=reference_operator_id,
            reference_fault_id=self.reference_plan.scenario.faults[0].fault.value,
            candidate_operator_ids=sorted(self.state.candidates),
            fallback_reason=self.fallback_reason,
        )
        self.channel.publish("planner.reference", reference, summary=reference.reference_plan_id)
        self._publish_state()
        return _json(
            {
                "status": "accepted",
                "candidates": [self._candidate_payload(candidate) for candidate in candidates],
                "reference": reference.model_dump(mode="json"),
                "next": "run_sandbox_experiment",
            }
        )

    async def _run_sandbox_experiment(
        self,
        operator_id: str,
        fault_id: str,
        decision_summary: str,
        expected_evidence: list[str],
        budget: int,
        fallback: str,
    ) -> str:
        if not self._require_phase("candidates_listed", "run_sandbox_experiment"):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if not self._record_decision(
            tool="run_sandbox_experiment",
            phase="candidates_listed",
            decision_summary=decision_summary,
            expected_evidence=expected_evidence,
            budget=budget,
            fallback=fallback,
        ):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        candidate = self.state.candidates.get(operator_id)
        if candidate is None:
            return self._reject(
                "operator_not_allowlisted",
                "operator_id is not in candidate_operators() for this run",
            )
        if candidate.operator.fault.value != fault_id:
            return self._reject(
                "fault_not_allowlisted", "fault_id does not match the selected candidate"
            )
        plan = plan_for_candidate(candidate, self.preflight.mission)
        if plan.scenario.faults[0].fault.value != fault_id:
            return self._reject(
                "fault_plan_mismatch", "compiled plan fault does not match the candidate"
            )
        if candidate.hypothesis.est_cost_units > budget:
            return self._reject(
                "budget_exceeded", "requested budget is below the candidate cost bound"
            )

        self.state.accepted_tool_calls += 1
        self.state.selected_plan = plan
        reference_operator_id = self._reference_operator_id()
        if reference_operator_id is None:
            return self._reject(
                "reference_plan_not_in_candidates", "reference plan disappeared from candidates"
            )
        comparison = PlannerComparisonArtifact(
            reference_plan_id=self.reference_plan.plan_id,
            reference_operator_id=reference_operator_id,
            live_plan_id=plan.plan_id,
            live_operator_id=operator_id,
            same_selection=plan.plan_id == self.reference_plan.plan_id,
            differences=(
                []
                if plan.plan_id == self.reference_plan.plan_id
                else [
                    "live operator differs from deterministic reference",
                    f"reference_fault={self.reference_plan.scenario.faults[0].fault.value}",
                    f"live_fault={fault_id}",
                ]
            ),
            fallback_policy="none",
        )
        self.channel.publish("planner.comparison", comparison, summary="live selection")
        try:
            result = await self.run_experiment(plan)
        except Exception as error:  # noqa: BLE001 - typed boundary, no exception detail to model
            return self._reject(
                "sandbox_experiment_failed",
                f"the bounded experiment failed with {type(error).__name__}",
            )
        if not isinstance(result, DiagnosticLoopResult):
            return self._reject(
                "invalid_experiment_result", "sandbox returned no typed diagnostic result"
            )
        if result.cost_units_used > DIAGNOSTIC_MAX_BUDGET_UNITS:
            return self._reject("budget_exceeded", "bounded primitive exceeded controller budget")
        self.state.result = result
        self.state.budget_spent = result.cost_units_used
        self.state.evidence_id = (
            f"evidence-{hashlib.sha256(run_digest_bytes(result)).hexdigest()[:20]}"
        )
        self.state.phase = "experiment_run"
        self.channel.publish(
            "diagnostic.experiment",
            {
                "plan_id": plan.plan_id,
                "operator_id": operator_id,
                "fault_id": fault_id,
                "reference_plan_id": self.reference_plan.plan_id,
                "live_plan_id": plan.plan_id,
                "execution_scope": self.execution_scope,
                "evidence_id": self.state.evidence_id,
                "budget_spent": self.state.budget_spent,
                "evidence_ready": True,
                "primitive_scope": "DeterministicLabLoop bundled phases",
            },
            summary="bounded experiment complete",
        )
        self._publish_state()
        return _json(
            {
                "status": "accepted",
                "evidence_id": self.state.evidence_id,
                "plan_id": plan.plan_id,
                "operator_id": operator_id,
                "fault_id": fault_id,
                "evidence_ready": True,
                "next": "inspect_evidence",
            }
        )

    def _evidence_payload(self) -> dict[str, Any]:
        assert self.state.result is not None
        result = self.state.result
        observed = result.observed
        citations = sorted(
            {f"ledger#{ref}" for violation in observed.violations for ref in violation.ledger_refs}
            | {f"trace#{ref}" for violation in observed.violations for ref in violation.trace_refs}
            | {
                f"state:{violation.state_path}"
                for violation in observed.violations
                if violation.state_path
            }
        )
        return {
            "evidence_id": self.state.evidence_id,
            "claim_status": "observed_only_until_verification",
            "observed": observed.model_dump(mode="json"),
            "citations": citations,
            "report_status_after_verification": result.report.status.value,
            "reproduction_count": result.report.reproduction_count,
            "reproduction_total": result.report.reproduction_total,
            "proposed_patch_id": result.patch.patch_id if result.patch else None,
            "evidence_refs": [ref.model_dump(mode="json") for ref in result.report.evidence],
            "execution_scope": self.execution_scope,
        }

    async def _inspect_evidence(
        self,
        evidence_id: str,
        decision_summary: str,
        expected_evidence: list[str],
        budget: int,
        fallback: str,
    ) -> str:
        if not self._require_phase("experiment_run", "inspect_evidence"):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if not self._record_decision(
            tool="inspect_evidence",
            phase="experiment_run",
            decision_summary=decision_summary,
            expected_evidence=expected_evidence,
            budget=budget,
            fallback=fallback,
        ):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if evidence_id != self.state.evidence_id:
            return self._reject(
                "evidence_not_allowlisted", "evidence_id is not produced by this run"
            )
        assert self.state.result is not None
        result = self.state.result
        if not result.report.evidence:
            return self._reject(
                "evidence_missing", "controller report contains no evidence references"
            )
        uncited = [
            violation.invariant_id
            for violation in result.observed.violations
            if not (violation.ledger_refs or violation.trace_refs or violation.state_path)
        ]
        if uncited:
            return self._reject("evidence_missing", f"uncited oracle violations: {uncited}")
        self.state.accepted_tool_calls += 1
        self.state.evidence_inspected = True
        self.state.phase = "evidence_inspected"
        payload = self._evidence_payload()
        self.channel.publish("diagnostic.evidence", payload, summary="evidence inspected")
        self._publish_state()
        return _json({"status": "accepted", **payload, "next": "verify_mitigation"})

    async def _verify_mitigation(
        self,
        evidence_id: str,
        mitigation_id: str,
        decision_summary: str,
        expected_evidence: list[str],
        budget: int,
        fallback: str,
    ) -> str:
        if not self._require_phase("evidence_inspected", "verify_mitigation"):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if not self._record_decision(
            tool="verify_mitigation",
            phase="evidence_inspected",
            decision_summary=decision_summary,
            expected_evidence=expected_evidence,
            budget=budget,
            fallback=fallback,
        ):
            return _json({"status": "rejected", "code": self.state.rejection_code})
        if evidence_id != self.state.evidence_id:
            return self._reject(
                "evidence_not_allowlisted", "verification must bind to inspected evidence"
            )
        assert self.state.result is not None
        result = self.state.result
        expected_mitigation_id = result.patch.patch_id if result.patch else NO_MITIGATION_REQUIRED
        if mitigation_id != expected_mitigation_id:
            return self._reject(
                "mitigation_not_allowlisted", "mitigation_id is not produced by this run"
            )
        if result.observed.violations:
            verification = result.verification
            if verification is None or not verification.accepted:
                return self._reject(
                    "mitigation_not_verified",
                    "a violated oracle report requires accepted patch verification "
                    "before final output",
                )
        self.state.accepted_tool_calls += 1
        self.state.mitigation_id = mitigation_id
        self.state.mitigation_verified = True
        self.state.phase = "mitigation_verified"
        self.channel.publish(
            "diagnostic.verification",
            {
                "evidence_id": evidence_id,
                "mitigation_id": mitigation_id,
                "accepted": True,
                "claim_status": "verified_mitigation"
                if result.observed.violations
                else "verified_no_failure",
                "verification": (
                    result.verification.model_dump(mode="json") if result.verification else None
                ),
            },
            summary="mitigation verified",
        )
        self._publish_state()
        return _json(
            {
                "status": "accepted",
                "evidence_id": evidence_id,
                "mitigation_id": mitigation_id,
                "accepted": True,
                "next": "final_output",
            }
        )

    def validate_final(self, value: Any) -> DiagnosticFinalOutput:
        """Reject a premature/evidence-free model final before publication."""

        if self.state.rejected:
            raise DiagnosticControllerError(
                self.state.rejection_code or "controller_rejected",
                "controller rejected a tool call; model final is not publishable",
            )
        if not self.state.mitigation_verified or self.state.phase != "mitigation_verified":
            raise DiagnosticControllerError(
                "premature_final",
                "model final arrived before evidence inspection and mitigation verification",
            )
        try:
            final = (
                value
                if isinstance(value, DiagnosticFinalOutput)
                else DiagnosticFinalOutput.model_validate(value)
            )
        except Exception as error:  # noqa: BLE001 - no provider validation detail on the wire
            raise DiagnosticControllerError(
                "invalid_final_schema", "model final did not match DiagnosticFinalOutput"
            ) from error
        if final.evidence_ids != [self.state.evidence_id]:
            raise DiagnosticControllerError(
                "evidence_id_mismatch",
                "model final must cite only the one controller-produced evidence_id",
            )
        if final.mitigation_id != self.state.mitigation_id:
            raise DiagnosticControllerError(
                "mitigation_mismatch", "model final cited a mitigation not verified by this run"
            )
        assert self.state.result is not None
        if self.state.result.observed.violations and not self.state.result.report.evidence:
            raise DiagnosticControllerError(
                "evidence_free_finding", "finding has no report evidence"
            )
        self._final = final
        return final

    def tools(self) -> list[Any]:
        if self._tools is not None:
            return list(self._tools)

        @function_tool(
            name_override="inspect_target",
            description_override=(
                "Inspect the pinned target profile and mission evidence. Read-only."
            ),
            strict_mode=True,
        )
        async def inspect_target(
            target_ref: BoundedId,
            decision_summary: BoundedText,
            expected_evidence: BoundedEvidence,
            budget: BoundedBudget,
            fallback: BoundedText,
        ) -> str:
            return await self._inspect_target(
                target_ref, decision_summary, expected_evidence, budget, fallback
            )

        @function_tool(
            name_override="list_experiments",
            description_override="List the bounded candidate operators for this target. Read-only.",
            strict_mode=True,
        )
        async def list_experiments(
            decision_summary: BoundedText,
            expected_evidence: BoundedEvidence,
            budget: BoundedBudget,
            fallback: BoundedText,
        ) -> str:
            return await self._list_experiments(
                decision_summary, expected_evidence, budget, fallback
            )

        @function_tool(
            name_override="run_sandbox_experiment",
            description_override=(
                "Run exactly one candidate operator in the bounded local diagnostic primitive."
            ),
            strict_mode=True,
        )
        async def run_sandbox_experiment(
            operator_id: BoundedId,
            fault_id: BoundedId,
            decision_summary: BoundedText,
            expected_evidence: BoundedEvidence,
            budget: BoundedBudget,
            fallback: BoundedText,
        ) -> str:
            return await self._run_sandbox_experiment(
                operator_id,
                fault_id,
                decision_summary,
                expected_evidence,
                budget,
                fallback,
            )

        @function_tool(
            name_override="inspect_evidence",
            description_override=(
                "Inspect controller-owned oracle, ledger, trace, and state evidence."
            ),
            strict_mode=True,
        )
        async def inspect_evidence(
            evidence_id: BoundedId,
            decision_summary: BoundedText,
            expected_evidence: BoundedEvidence,
            budget: BoundedBudget,
            fallback: BoundedText,
        ) -> str:
            return await self._inspect_evidence(
                evidence_id, decision_summary, expected_evidence, budget, fallback
            )

        @function_tool(
            name_override="verify_mitigation",
            description_override=(
                "Verify the controller-owned mitigation gates before a final answer."
            ),
            strict_mode=True,
        )
        async def verify_mitigation(
            evidence_id: BoundedId,
            mitigation_id: BoundedId,
            decision_summary: BoundedText,
            expected_evidence: BoundedEvidence,
            budget: BoundedBudget,
            fallback: BoundedText,
        ) -> str:
            return await self._verify_mitigation(
                evidence_id,
                mitigation_id,
                decision_summary,
                expected_evidence,
                budget,
                fallback,
            )

        self._tools = [
            inspect_target,
            list_experiments,
            run_sandbox_experiment,
            inspect_evidence,
            verify_mitigation,
        ]
        assert_strict_diagnostic_tools(self._tools)
        assert_strict_diagnostic_output()
        return list(self._tools)

    def agent(self, *, model: Any | None = None) -> Agent:
        self.tools()
        return Agent(
            name="Nightmare Lab bounded diagnostic controller",
            instructions=DIAGNOSTIC_CONTROLLER_INSTRUCTIONS,
            tools=list(self._tools or []),
            model=model,
            output_type=DiagnosticFinalOutput,
        )


def run_digest_bytes(result: DiagnosticLoopResult) -> bytes:
    """Stable evidence identity without exposing provider metadata."""

    return canonical_json(result.perturbed.model_dump(mode="json")).encode("utf-8")


__all__ = [
    "DIAGNOSTIC_MAX_BUDGET_UNITS",
    "DIAGNOSTIC_MAX_TOOL_CALLS",
    "DIAGNOSTIC_TOOL_NAMES",
    "DiagnosticControllerError",
    "DiagnosticDecisionArtifact",
    "DiagnosticStateArtifact",
    "DiagnosticToolController",
    "PlannerComparisonArtifact",
    "PlannerReferenceArtifact",
    "assert_strict_diagnostic_output",
    "assert_strict_diagnostic_tools",
]
