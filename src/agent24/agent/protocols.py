"""Structural interfaces between the Lab Agent and its replaceable parts.

Three seams, each owned by a different part of the team:

* :class:`GymProtocol` -- the sandbox the Agent Under Test runs inside.
* :class:`PlannerProtocol` -- the only place an LLM is allowed to sit.
* :class:`ExperimentPolicyProtocol` -- deterministic scheduling and honesty.

Nothing here imports ``openai`` or ``agents``.  The deterministic lab modules
depend on these protocols alone, so the whole evaluation suite runs offline
against the in-memory fakes.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .models import (
    AgentCard,
    AntibodyPatch,
    ClassifiedCapability,
    DiagnosisOutput,
    Divergence,
    ExperimentPlan,
    ExperimentRecord,
    ExperimentSelectionOutput,
    LoopState,
    MinimizationResult,
    Mission,
    OracleReport,
    PatchProposalOutput,
    RunResult,
    Scenario,
    StopDecision,
    ThreatModelOutput,
)


@runtime_checkable
class GymProtocol(Protocol):
    """A controllable sandbox that can replay an experiment exactly.

    **Determinism is required, not best-effort.**  The same
    ``(scenario, policy)`` pair must produce a byte-identical
    :class:`~agent24.agent.models.RunResult` -- identical trace, ledger, world
    state and final answer -- on every call, in any process.  Minimization,
    first-divergence analysis and the replay gates are all built on that
    guarantee; without it they silently produce nonsense rather than failing.

    World reset is implied by ``scenario.seed`` plus
    ``scenario.world_overrides``: an implementation must not carry state between
    calls.  No network access.

    Implementations must also honour the frozen semantics documented in
    ``models.py``: a policy-blocked call commits nothing but still appears in
    the trace with ``status="blocked_by_policy"``, and a blocked call does not
    advance a fault's ``at_call_index`` counter.
    """

    async def run(self, scenario: Scenario, policy: AntibodyPatch | None = None) -> RunResult: ...


@runtime_checkable
class PlannerProtocol(Protocol):
    """The judgement calls: threat modelling, selection, diagnosis, patching.

    This is the only seam where a language model may be used.  The runtime wires
    the versioned prompt bodies and the ``*Output`` models into the Agents SDK as
    ``output_type``; the deterministic test suite substitutes a scripted planner
    so that no test needs a network or an API key.

    Each method returns a validated structured output, never prose.
    """

    async def build_threat_model(
        self,
        card: AgentCard,
        mission: Mission,
        capabilities: Sequence[ClassifiedCapability],
    ) -> ThreatModelOutput: ...

    async def select_experiments(
        self,
        threat: ThreatModelOutput,
        history: Sequence[ExperimentRecord],
    ) -> ExperimentSelectionOutput: ...

    async def diagnose(
        self,
        observed: OracleReport,
        divergence: Divergence | None,
        minimized: MinimizationResult | None,
    ) -> DiagnosisOutput: ...

    async def propose_patch(
        self,
        diagnosis: DiagnosisOutput,
        observed: OracleReport,
    ) -> PatchProposalOutput: ...


@runtime_checkable
class ExperimentPolicyProtocol(Protocol):
    """Deterministic experiment scheduling and honest termination.

    Everything here is pure Python -- no LLM.  Ranking has to be reproducible
    for a replay to mean anything, and the stop conditions are what keep the
    loop from spending an unbounded budget.
    """

    def rank(
        self,
        plans: Sequence[ExperimentPlan],
        history: Sequence[ExperimentRecord],
    ) -> list[ExperimentPlan]: ...

    def should_stop(self, state: LoopState) -> StopDecision: ...

    def assess_support(
        self,
        mission: Mission,
        caps: Sequence[ClassifiedCapability],
    ) -> StopDecision: ...
    """Decide whether this mission is in scope before spending any budget.

    Returns ``StopDecision(stop=True, reason="unsupported_input", ...)`` for a
    mission the gym cannot faithfully simulate.  This lives on the protocol
    rather than only as a free function because the loop populates
    ``LabReport.unsupported_scope`` from it and only ever holds the protocol.
    Reporting an honest "out of scope" is always preferred to inventing a
    finding.
    """


__all__ = ["ExperimentPolicyProtocol", "GymProtocol", "PlannerProtocol"]
