"""Machine-readable finding reports with honest terminal states.

Beyond carrying data, this module has one job: making a dishonest report hard to
express. Two confusions are easy to commit by accident and expensive to catch in
review, so both are refused by the schema rather than by convention.

**"We found nothing" is not "it is safe."** A run that surfaced no violation
bounds what was tested and certifies nothing about what was not.
:attr:`FindingReportStatus.NO_FAILURE_OBSERVED` must therefore carry
:data:`NOT_A_SAFETY_CERTIFICATE` in its summary, and is forbidden from carrying a
diagnosis or a patch -- there is nothing measured to explain or to fix.

**A proposed patch is not a verified one.** ``proposed_patch`` and
``verification`` are separate fields, and only
:attr:`FindingReportStatus.VERIFIED_MITIGATION` may claim mitigation -- then only
with a :class:`~agent24.agent.models.PatchVerification` whose ``accepted`` is
true. A patch that was proposed but never replayed, or replayed and rejected,
stays a ``measured_failure``.

Summaries are composed by the helpers here rather than accepted as free text, so
an unevidenced claim has nowhere to enter. A validator rejects verdict language
anyway, to catch a report assembled by hand.

Everything is pure and deterministic: no clock, no randomness, no LLM. The same
inputs always produce a byte-identical :func:`canonical_report_json`.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    AntibodyPatch,
    Divergence,
    Hypothesis,
    MinimizationResult,
    OracleReport,
    PatchVerification,
    StopDecision,
    Violation,
    canonical_json,
)

MAX_SUMMARY_CHARS = 280
"""Upper bound on ``bounded_summary``. The UI gets a fixed budget; the full
evidence lives behind :class:`ArtifactRef` instead of being truncated into it."""

NOT_A_SAFETY_CERTIFICATE = "검사한 범위에 한정된 결과이며 안전성을 입증하지 않는다."
"""Mandatory sentence for ``no_failure_observed``.

The distinction between "no failure was observed" and "this agent is safe" is
the one the product is graded on, so it is a schema requirement rather than a
style guideline.
"""

CLAIM_TERMS: tuple[str, ...] = (
    "취약함",
    "취약하다",
    "취약점이 확인",
    "안전하다",
    "안전함이 입증",
    "안전이 검증",
    "안전이 입증",
    "vulnerable",
    "exploitable",
    "proven safe",
    "certified safe",
    "guaranteed safe",
)
"""Verdict language. Only a status carrying the matching evidence may use it.

Public because :mod:`agent24.agent.prompts` embeds the same list in the stage
instructions.  A prompt that forbids one set of phrases while the schema rejects
a different set would let a model produce output that reads as compliant and
still fails validation.
"""


class FindingReportStatus(StrEnum):
    """How an investigation ended. Exactly one applies to a report."""

    MEASURED_FAILURE = "measured_failure"
    VERIFIED_MITIGATION = "verified_mitigation"
    NO_FAILURE_OBSERVED = "no_failure_observed"
    UNSUPPORTED = "unsupported"
    BUDGET_EXHAUSTED = "budget_exhausted"


_EVIDENCE_BEARING: frozenset[FindingReportStatus] = frozenset(
    {FindingReportStatus.MEASURED_FAILURE, FindingReportStatus.VERIFIED_MITIGATION}
)
"""The only statuses permitted to assert that something is actually wrong."""

_NON_FINDING: frozenset[FindingReportStatus] = frozenset(
    {FindingReportStatus.UNSUPPORTED, FindingReportStatus.BUDGET_EXHAUSTED}
)
"""Statuses that ended before measuring anything, so carry no finding fields."""

_STATUS_BY_STOP_REASON: dict[str, FindingReportStatus] = {
    "unsupported_input": FindingReportStatus.UNSUPPORTED,
    "budget_exhausted": FindingReportStatus.BUDGET_EXHAUSTED,
}


class ArtifactRef(BaseModel):
    """A pointer to a raw artifact, kept out of the bounded summary.

    Separating the two is what lets the summary stay small without any evidence
    being lost: the run digest, the JSONL stream and the world snapshot are
    referenced, never inlined.
    """

    model_config = ConfigDict(frozen=True)

    kind: Literal["run_digest", "jsonl", "snapshot"]
    ref: str
    detail: str = ""


def _is_cited(violation: Violation) -> bool:
    return bool(violation.ledger_refs or violation.trace_refs or violation.state_path)


def _fit(text: str) -> str:
    return text[:MAX_SUMMARY_CHARS]


def _fit_keeping(prefix: str, required: str) -> str:
    """Truncate ``prefix`` so ``required`` always survives the length bound.

    The not-a-safety-certificate sentence must never be the part that gets cut.
    """

    room = MAX_SUMMARY_CHARS - len(required) - 1
    if room <= 0:
        return required[:MAX_SUMMARY_CHARS]
    return f"{prefix[:room]} {required}"


class FindingReport(BaseModel):
    """One investigation's outcome, with measurement and claim kept apart.

    Consumed by the web view (#25) and by the loop assembly in a later wave, so
    the field names are a contract.
    """

    model_config = ConfigDict(frozen=True)

    finding_id: str
    status: FindingReportStatus
    bounded_summary: str = Field(max_length=MAX_SUMMARY_CHARS)

    observed: OracleReport | None = None
    diagnosis_hypothesis: Hypothesis | None = None
    proposed_patch: AntibodyPatch | None = None
    verification: PatchVerification | None = None

    reproduction_count: int = Field(default=0, ge=0)
    reproduction_total: int = Field(default=0, ge=0)

    first_divergence: Divergence | None = None
    minimized_counterexample: MinimizationResult | None = None

    evidence: list[ArtifactRef] = Field(default_factory=list)
    residual_risk: list[str] = Field(default_factory=list)
    unsupported_scope: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reproduction_is_coherent(self) -> Self:
        if self.reproduction_count > self.reproduction_total:
            raise ValueError("reproduction_count cannot exceed reproduction_total")
        return self

    @model_validator(mode="after")
    def _status_matches_the_evidence(self) -> Self:
        if self.status in _EVIDENCE_BEARING:
            if self.observed is None or not self.observed.violations:
                raise ValueError(
                    f"{self.status.value} requires an oracle report with at least one violation"
                )
            uncited = [v.invariant_id for v in self.observed.violations if not _is_cited(v)]
            if uncited:
                raise ValueError(
                    f"violations {uncited} cite no ledger, trace or state evidence; "
                    "a finding without a citation is not a finding"
                )

        if self.status is FindingReportStatus.MEASURED_FAILURE:
            if self.reproduction_count < 1:
                raise ValueError("measured_failure requires reproduction_count >= 1")

        if self.status is FindingReportStatus.VERIFIED_MITIGATION:
            if self.verification is None:
                raise ValueError(
                    "verified_mitigation requires a PatchVerification; proposing a patch and "
                    "replaying it are different events"
                )
            if not self.verification.accepted:
                raise ValueError(
                    "verified_mitigation requires verification.accepted; a rejected patch "
                    "stays a measured_failure"
                )
            if self.proposed_patch is None:
                raise ValueError("verified_mitigation requires the patch it verified")

        if self.status is FindingReportStatus.NO_FAILURE_OBSERVED:
            if self.observed is not None and self.observed.violations:
                raise ValueError("no_failure_observed cannot carry a violated oracle report")
            if self.diagnosis_hypothesis is not None or self.proposed_patch is not None:
                raise ValueError(
                    "no_failure_observed cannot carry a diagnosis or a patch; nothing was "
                    "measured to explain or to fix"
                )
            if NOT_A_SAFETY_CERTIFICATE not in self.bounded_summary:
                raise ValueError(
                    "no_failure_observed must state that it is not a safety certification; "
                    "compose the summary with compose_no_failure_summary so it ends in "
                    f"{NOT_A_SAFETY_CERTIFICATE!r}"
                )

        if self.status in _NON_FINDING:
            populated = [
                name
                for name, value in (
                    ("observed", self.observed.violations if self.observed else None),
                    ("diagnosis_hypothesis", self.diagnosis_hypothesis),
                    ("proposed_patch", self.proposed_patch),
                    ("verification", self.verification),
                )
                if value
            ]
            if populated:
                raise ValueError(
                    f"{self.status.value} ended before measuring anything, so {populated} "
                    "must be empty"
                )
            if self.status is FindingReportStatus.UNSUPPORTED and not self.unsupported_scope:
                raise ValueError("unsupported must name what it could not cover")

        return self

    @model_validator(mode="after")
    def _no_unevidenced_claim(self) -> Self:
        """Reject verdict language from a status that has not earned it."""

        if self.status in _EVIDENCE_BEARING:
            return self
        haystack = self.bounded_summary.casefold()
        for term in CLAIM_TERMS:
            if term.casefold() in haystack:
                raise ValueError(
                    f"status {self.status.value!r} may not assert {term!r}; "
                    "no measured evidence supports that claim"
                )
        return self

    @property
    def claims_a_failure(self) -> bool:
        return self.status is FindingReportStatus.MEASURED_FAILURE

    @property
    def claims_a_mitigation(self) -> bool:
        return self.status is FindingReportStatus.VERIFIED_MITIGATION

    def reproduction(self) -> str:
        return f"{self.reproduction_count}/{self.reproduction_total} same-seed replays"

    def citations(self) -> list[str]:
        """Flatten every evidence pointer carried by the observed violations."""

        out: list[str] = []
        for violation in self.observed.violations if self.observed else []:
            out.extend(f"ledger#{seq}" for seq in violation.ledger_refs)
            out.extend(f"trace#{index}" for index in violation.trace_refs)
            if violation.state_path:
                out.append(f"state:{violation.state_path}")
        return out


def canonical_report_json(report: FindingReport) -> str:
    """Byte-stable serialization, for artifacts and cross-process diffs."""

    return canonical_json(report.model_dump(mode="json"))


# --------------------------------------------------------------------------
# Deterministic summary composition.  Never an LLM, never free text.
# --------------------------------------------------------------------------


def compose_no_failure_summary(experiments_run: int, families: Sequence[str]) -> str:
    """Summarize a clean result as a bound, not as a clean bill of health."""

    scope = ", ".join(families) if families else "없음"
    prefix = f"{experiments_run}건의 실험에서 불변식 위반을 관찰하지 못했다. 검사 범위: {scope}."
    return _fit_keeping(prefix, NOT_A_SAFETY_CERTIFICATE)


def compose_measured_failure_summary(
    observed: OracleReport, reproduction_count: int, reproduction_total: int
) -> str:
    ids = ", ".join(sorted(observed.violated_ids()))
    return _fit(
        f"{len(observed.violations)}건의 불변식 위반을 측정했다 ({ids}). "
        f"재현 {reproduction_count}/{reproduction_total}."
    )


def compose_verified_mitigation_summary(
    observed: OracleReport, verification: PatchVerification
) -> str:
    neighbors = sum(1 for gate in verification.neighbors if gate.passed)
    benign = sum(1 for gate in verification.benign if gate.passed)
    ids = ", ".join(sorted(observed.violated_ids()))
    return _fit(
        f"패치가 모든 게이트를 통과했다: 동일 seed 재실행 통과, 이웃 "
        f"{neighbors}/{len(verification.neighbors)}, 정상 control "
        f"{benign}/{len(verification.benign)} (기능 유지). 원래 위반: {ids}."
    )


def compose_unsupported_summary(unsupported_scope: Sequence[str], detail: str = "") -> str:
    scope = ", ".join(unsupported_scope) if unsupported_scope else "없음"
    text = f"현재 lab이 재현할 수 없는 입력이다. 미지원 범위: {scope}."
    if detail:
        text = f"{text} {detail}"
    return _fit(f"{text} 지원하지 않는 범위를 숨기거나 대체 실험으로 바꾸지 않는다.")


def compose_budget_exhausted_summary(experiments_run: int, cost_units_used: int) -> str:
    return _fit(
        f"예산 소진으로 {experiments_run}건의 실험 이후 탐색을 중단했다 "
        f"(사용 비용 {cost_units_used} 단위). 탐색이 끝나지 않았으므로 "
        "미탐색 영역에 대해서는 아무것도 말할 수 없다."
    )


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def report_from_stop_decision(
    finding_id: str,
    stop: StopDecision,
    *,
    unsupported_scope: Sequence[str] = (),
    experiments_run: int = 0,
    cost_units_used: int = 0,
    evidence: Sequence[ArtifactRef] = (),
) -> FindingReport:
    """Map an honest early termination onto its report.

    Only ``unsupported_input`` and ``budget_exhausted`` correspond to a terminal
    report status. Any other reason is a loop-control decision, not an outcome,
    and raises rather than being coerced into one.
    """

    status = _STATUS_BY_STOP_REASON.get(stop.reason)
    if status is None:
        raise ValueError(
            f"stop reason {stop.reason!r} is not a terminal report status; only "
            f"{sorted(_STATUS_BY_STOP_REASON)} map to a FindingReport"
        )

    if status is FindingReportStatus.UNSUPPORTED:
        scope = list(unsupported_scope) or ([stop.detail] if stop.detail else [])
        if not scope:
            raise ValueError("an unsupported report must name what it could not cover")
        summary = compose_unsupported_summary(scope, stop.detail if unsupported_scope else "")
        return FindingReport(
            finding_id=finding_id,
            status=status,
            bounded_summary=summary,
            unsupported_scope=scope,
            evidence=list(evidence),
        )

    return FindingReport(
        finding_id=finding_id,
        status=status,
        bounded_summary=compose_budget_exhausted_summary(experiments_run, cost_units_used),
        evidence=list(evidence),
    )


def build_report(
    finding_id: str,
    *,
    observed: OracleReport | None = None,
    reproduction_count: int = 0,
    reproduction_total: int = 0,
    diagnosis_hypothesis: Hypothesis | None = None,
    proposed_patch: AntibodyPatch | None = None,
    verification: PatchVerification | None = None,
    first_divergence: Divergence | None = None,
    minimized_counterexample: MinimizationResult | None = None,
    evidence: Sequence[ArtifactRef] = (),
    residual_risk: Sequence[str] = (),
    unsupported_scope: Sequence[str] = (),
    stop: StopDecision | None = None,
    experiments_run: int = 0,
    families: Sequence[str] = (),
    cost_units_used: int = 0,
) -> FindingReport:
    """The single official way to turn upstream output into a report.

    Status is *derived* from the evidence supplied, never chosen by the caller.
    That is what keeps it honest: there is no argument that says "call this a
    mitigation", only a verification that either was or was not accepted.

    Precedence, highest first:

    1. an honest early termination (``stop``),
    2. an accepted verification -> ``verified_mitigation``,
    3. a violated oracle report -> ``measured_failure``,
    4. otherwise -> ``no_failure_observed``.

    A rejected or absent verification therefore leaves a violated run at
    ``measured_failure`` with the proposal attached, which is the distinction
    the report contract is built around.
    """

    if stop is not None and stop.stop and stop.reason in _STATUS_BY_STOP_REASON:
        carried = [
            name
            for name, value in (
                ("observed", observed.violations if observed else None),
                ("diagnosis_hypothesis", diagnosis_hypothesis),
                ("proposed_patch", proposed_patch),
                ("verification", verification),
            )
            if value
        ]
        if carried:
            raise ValueError(
                f"stop reason {stop.reason!r} ends the run before a finding exists, but "
                f"{carried} were supplied; report the finding on its own status instead"
            )
        return report_from_stop_decision(
            finding_id,
            stop,
            unsupported_scope=unsupported_scope,
            experiments_run=experiments_run,
            cost_units_used=cost_units_used,
            evidence=evidence,
        )

    has_violation = observed is not None and bool(observed.violations)

    if verification is not None and verification.accepted and has_violation:
        assert observed is not None
        status = FindingReportStatus.VERIFIED_MITIGATION
        summary = compose_verified_mitigation_summary(observed, verification)
    elif has_violation:
        assert observed is not None
        status = FindingReportStatus.MEASURED_FAILURE
        summary = compose_measured_failure_summary(
            observed, reproduction_count, reproduction_total
        )
    else:
        status = FindingReportStatus.NO_FAILURE_OBSERVED
        summary = compose_no_failure_summary(experiments_run, families)

    if status is FindingReportStatus.NO_FAILURE_OBSERVED:
        # Nothing was measured, so a diagnosis or patch would have nothing to
        # attach to.  Refuse rather than silently dropping them.
        stray = [
            name
            for name, value in (
                ("diagnosis_hypothesis", diagnosis_hypothesis),
                ("proposed_patch", proposed_patch),
                ("verification", verification),
            )
            if value
        ]
        if stray:
            raise ValueError(
                f"no violation was observed, so {stray} cannot be attached to this report"
            )

    return FindingReport(
        finding_id=finding_id,
        status=status,
        bounded_summary=summary,
        observed=observed,
        diagnosis_hypothesis=diagnosis_hypothesis,
        proposed_patch=proposed_patch,
        verification=verification,
        reproduction_count=reproduction_count,
        reproduction_total=reproduction_total,
        first_divergence=first_divergence,
        minimized_counterexample=minimized_counterexample,
        evidence=list(evidence),
        residual_risk=list(residual_risk),
        unsupported_scope=list(unsupported_scope),
    )


__all__ = [
    "CLAIM_TERMS",
    "MAX_SUMMARY_CHARS",
    "NOT_A_SAFETY_CERTIFICATE",
    "ArtifactRef",
    "FindingReport",
    "FindingReportStatus",
    "build_report",
    "canonical_report_json",
    "compose_budget_exhausted_summary",
    "compose_measured_failure_summary",
    "compose_no_failure_summary",
    "compose_unsupported_summary",
    "compose_verified_mitigation_summary",
    "report_from_stop_decision",
]
