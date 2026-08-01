"""One bounded deterministic experiment loop for the external-Agent workflow.

The external repository is metadata-only in P0.  This loop executes the
allowlisted Life-v0 *synthetic behaviour archetype* selected by the plan; it
never imports or runs target source code.  The scope limitation is carried into
both report formats so a measured fixture failure cannot be presented as a
measured failure of the submitted repository.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .divergence import first_divergence
from .fakes import FakeGym, benign_scenario
from .gates import make_neighbors, verify_patch
from .invariants import DEFAULT_MAX_SPEND_KRW, life_v0_invariants
from .minimize import minimize
from .models import (
    UNTRUSTED_LABELS,
    AgentCard,
    AntibodyPatch,
    DenyRule,
    Divergence,
    ExperimentPlan,
    FailureCategory,
    Finding,
    Hypothesis,
    LabReport,
    Mission,
    OracleReport,
    PatchVerification,
    RunResult,
    SideEffectRule,
    StopDecision,
    canonical_json,
    run_digest,
)
from .oracle import canonical_report, evaluate
from .planner import hypothesis_for
from .profile import AgentManifest, BehaviorProfile
from .report import ArtifactRef, FindingReport, build_report, report_from_stop_decision

SYNTHETIC_SCOPE = (
    "외부 저장소 코드는 실행하지 않았으며 manifest로 선택한 Life-v0 synthetic "
    "behavior archetype만 측정했다."
)
REPORT_REDACTED_PROMPT = "[owner manifest system_prompt redacted from report]"
REPRODUCTION_TOTAL = 3


@dataclass(frozen=True, slots=True)
class DiagnosticLoopResult:
    """Artifacts from one deterministic baseline→fault→patch→replay loop."""

    baseline: RunResult
    perturbed: RunResult
    observed: OracleReport
    divergence: Divergence | None
    hypothesis: Hypothesis | None
    patch: AntibodyPatch | None
    verification: PatchVerification | None
    protected: RunResult | None
    report: FindingReport
    lab_report: LabReport
    experiments_run: int
    cost_units_used: int


def unsupported_reports(
    *,
    manifest: AgentManifest,
    profile: BehaviorProfile,
    mission: Mission,
    stop: StopDecision,
) -> tuple[FindingReport, LabReport]:
    """Build both report contracts for a pre-execution unsupported stop."""

    scope = list(manifest.unsupported_tools) or [stop.detail]
    finding_report = report_from_stop_decision(
        f"f-unsupported-{manifest.name}",
        stop,
        unsupported_scope=scope,
    )
    lab_report = LabReport(
        agent=AgentCard(
            name=manifest.name,
            system_prompt=REPORT_REDACTED_PROMPT,
            tools=list(manifest.tools),
            permissions=dict(manifest.permissions),
        ),
        mission=mission,
        capabilities=list(profile.capabilities),
        invariants=[],
        experiments_run=0,
        cost_units_used=0,
        findings=[],
        termination=stop,
        unsupported_scope=scope,
        no_failure_statement=None,
    )
    return finding_report, lab_report


def _max_spend(manifest: AgentManifest, mission: Mission) -> int:
    for source in (manifest.permissions, mission.constraints):
        value = source.get("max_spend_krw")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return DEFAULT_MAX_SPEND_KRW


def _patch_for(category: FailureCategory, *, max_spend_krw: int) -> AntibodyPatch | None:
    if category is FailureCategory.DUPLICATE_SIDE_EFFECT:
        return AntibodyPatch(
            patch_id="pt-duplicate-payment",
            max_spend_krw=max_spend_krw,
            max_purchase_count=1,
            side_effect_rules=[
                SideEffectRule(
                    tool="payment.charge",
                    require_idempotency_key=True,
                    timeout_means_unknown=True,
                    reconcile_with="payment.status",
                )
            ],
            rationale="비가역 결제에 idempotency key와 timeout 후 상태 조회를 강제한다.",
        )
    if category is FailureCategory.INDIRECT_INJECTION:
        return AntibodyPatch(
            patch_id="pt-indirect-injection",
            deny_rules=[
                DenyRule(
                    untrusted_sources=UNTRUSTED_LABELS,
                    denied_tools=frozenset({"file.read", "email.send"}),
                )
            ],
            rationale=(
                "신뢰할 수 없는 출처가 개인 데이터 접근과 외부 발송을 유발하지 못하게 한다."
            ),
        )
    if category is FailureCategory.UNBOUNDED_RETRY:
        return AntibodyPatch(
            patch_id="pt-bounded-retry",
            max_repeated_tool_calls=2,
            rationale="동일 호출 반복에 상한을 두되 복구 경로 자체는 남긴다.",
        )
    return None


def _world_digest(run: RunResult) -> str:
    encoded = canonical_json(run.world_state).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_faults(plan: ExperimentPlan):
    return plan.scenario.model_copy(update={"faults": ()})


def _legacy_report(
    *,
    manifest: AgentManifest,
    profile: BehaviorProfile,
    mission: Mission,
    invariants,
    finding_report: FindingReport,
    hypothesis: Hypothesis | None,
    patch: AntibodyPatch | None,
    verification: PatchVerification | None,
    divergence: Divergence | None,
    minimized,
    experiments_run: int,
    cost_units_used: int,
) -> LabReport:
    findings: list[Finding] = []
    if finding_report.observed is not None and finding_report.observed.violations:
        findings.append(
            Finding(
                finding_id=finding_report.finding_id,
                observed=finding_report.observed,
                repro=finding_report.reproduction(),
                first_divergence=divergence,
                minimized_counterexample=minimized,
                diagnosis=hypothesis,
                proposed_patch=patch,
                verified=verification,
                residual_risk=list(finding_report.residual_risk),
            )
        )

    verified = verification is not None and verification.accepted
    detail = (
        "Life-v0 synthetic archetype의 측정 실패와 보호 재실행을 완료했다."
        if verified
        else "Life-v0 synthetic archetype의 제한된 실험을 완료했다."
    )
    return LabReport(
        agent=AgentCard(
            name=manifest.name,
            system_prompt=REPORT_REDACTED_PROMPT,
            tools=list(manifest.tools),
            permissions=dict(manifest.permissions),
        ),
        mission=mission,
        capabilities=list(profile.capabilities),
        invariants=list(invariants),
        experiments_run=experiments_run,
        cost_units_used=cost_units_used,
        findings=findings,
        termination=StopDecision(stop=True, reason="coverage_complete", detail=detail),
        unsupported_scope=[SYNTHETIC_SCOPE, *manifest.unsupported_tools],
        no_failure_statement=(
            finding_report.bounded_summary
            if finding_report.observed is None or not finding_report.observed.violations
            else None
        ),
    )


class DeterministicLabLoop:
    """Execute one selected P0 plan with a fixed budget and no network."""

    def __init__(self, *, gym: FakeGym | None = None) -> None:
        self.gym = gym or FakeGym()

    async def run(
        self,
        *,
        manifest: AgentManifest,
        profile: BehaviorProfile,
        mission: Mission,
        plan: ExperimentPlan,
    ) -> DiagnosticLoopResult:
        max_spend = _max_spend(manifest, mission)
        invariants = life_v0_invariants(max_spend_krw=max_spend)
        baseline = await self.gym.run(_without_faults(plan))
        perturbed = await self.gym.run(plan.scenario)
        observed = evaluate(invariants, perturbed)
        divergence = first_divergence(baseline.trace, perturbed.trace)

        reproduction_count = 1
        for _ in range(REPRODUCTION_TOTAL - 1):
            replay = await self.gym.run(plan.scenario)
            replay_oracle = evaluate(invariants, replay)
            if (
                run_digest(replay) == run_digest(perturbed)
                and canonical_report(replay_oracle) == canonical_report(observed)
            ):
                reproduction_count += 1

        minimized = None
        experiments_run = 2 + (REPRODUCTION_TOTAL - 1)
        if observed.violations:
            violated_ids = observed.violated_ids()

            async def reproduces(candidate) -> bool:
                candidate_report = evaluate(invariants, await self.gym.run(candidate))
                return bool(violated_ids & candidate_report.violated_ids())

            minimized = await minimize(plan.scenario, reproduces)
            experiments_run += minimized.runs_used

        hypothesis = hypothesis_for(plan, profile, mission)
        patch = (
            _patch_for(hypothesis.category, max_spend_krw=max_spend)
            if hypothesis is not None and observed.violations
            else None
        )
        verification = None
        protected = None
        if patch is not None:
            neighbors = make_neighbors(plan.scenario)
            controls = [benign_scenario(mission), benign_scenario(mission, with_timeout=True)]
            verification = await verify_patch(
                patch,
                plan.scenario,
                self.gym,
                invariants,
                benign=controls,
                neighbors=neighbors,
                unpatched=observed,
            )
            protected = await self.gym.run(plan.scenario, patch)
            experiments_run += 1 + len(neighbors) + len(controls) + 1

        cost_per_experiment = hypothesis.est_cost_units if hypothesis is not None else 1
        cost_units_used = experiments_run * cost_per_experiment
        residual_risk = [SYNTHETIC_SCOPE, *manifest.unsupported_tools]
        finding_report = build_report(
            f"f-{plan.plan_id}",
            observed=observed,
            reproduction_count=reproduction_count,
            reproduction_total=REPRODUCTION_TOTAL,
            diagnosis_hypothesis=hypothesis if observed.violations else None,
            proposed_patch=patch,
            verification=verification,
            first_divergence=divergence,
            minimized_counterexample=minimized,
            evidence=(
                ArtifactRef(
                    kind="run_digest",
                    ref=f"sha256:{run_digest(perturbed)}",
                    detail="Life-v0 perturbed synthetic run",
                ),
                ArtifactRef(
                    kind="snapshot",
                    ref=f"sha256:{_world_digest(perturbed)}",
                    detail="Life-v0 final synthetic world",
                ),
            ),
            residual_risk=residual_risk,
            unsupported_scope=manifest.unsupported_tools,
            experiments_run=experiments_run,
            families=(plan.scenario.faults[0].fault.value,),
            cost_units_used=cost_units_used,
        )
        lab_report = _legacy_report(
            manifest=manifest,
            profile=profile,
            mission=mission,
            invariants=invariants,
            finding_report=finding_report,
            hypothesis=hypothesis,
            patch=patch,
            verification=verification,
            divergence=divergence,
            minimized=minimized,
            experiments_run=experiments_run,
            cost_units_used=cost_units_used,
        )
        return DiagnosticLoopResult(
            baseline=baseline,
            perturbed=perturbed,
            observed=observed,
            divergence=divergence,
            hypothesis=hypothesis,
            patch=patch,
            verification=verification,
            protected=protected,
            report=finding_report,
            lab_report=lab_report,
            experiments_run=experiments_run,
            cost_units_used=cost_units_used,
        )


__all__ = [
    "REPRODUCTION_TOTAL",
    "SYNTHETIC_SCOPE",
    "DeterministicLabLoop",
    "DiagnosticLoopResult",
    "unsupported_reports",
]
