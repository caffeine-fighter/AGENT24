"""Versioned prompt contracts for offline stages and the two live paths.

Oracle and terminal claims stay controller-owned and deterministic.  For an
external target, the live diagnostic path now lets the model choose one
allowlisted experiment and drive the exact five-tool inspection sequence.  The
deterministic planner remains an explicit reference and same-target fallback;
the controller records any selection difference and validates every evidence
and mitigation ID before publishing the model's final output.

The older four judgement assets still describe model judgement inside fixed
rails -- naming a threat, wording a hypothesis, and proposing a patch.  This
module is the thin contract around both seams.  It provides four things:

**Versioned instruction bodies.**  ``prompts/<stage>.md``, one per stage,
addressed by :class:`PromptStage`.  They are data files rather than string
literals so a reviewer can read the actual prompt without reading Python.

**A mapping to the frozen output schema.**  Every :class:`PromptSpec` names the
``*Output`` model the runtime passes to the Agents SDK as ``output_type`` and
the :class:`~agent24.agent.protocols.PlannerProtocol` method it backs.  The
mapping is a value, not prose, so a test can assert it.

**One trust boundary.**  Everything the Agent Under Test or the gym produced is
rendered as canonical JSON inside :data:`UNTRUSTED_OPEN` / :data:`UNTRUSTED_CLOSE`
by the ``render_*_input`` functions, which mirror the ``PlannerProtocol``
signatures exactly.  The operator's mission is the only text presented as an
instruction.  A prompt that merely *asks* a model to distrust its input still
hands that input over as prose; fencing it is the part that is testable.

**Declared budgets and recovery.**  Turn, cost and stop conditions
(:class:`LoopBudget`, :func:`should_stop`) and the schema-failure rule
(:func:`recover_from_schema_error`).  The numbers quoted in the instruction text
are substituted from these same constants at load time, so the budget a model is
told about cannot drift from the budget that is enforced.

Nothing here imports ``openai`` or ``agents``: this is the prompt *asset*, and
the runtime is what feeds it to a model.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from importlib import resources
from string import Template
from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from .models import (
    INVARIANT_IDS,
    AgentCard,
    ClassifiedCapability,
    DiagnosisOutput,
    DiagnosticFinalOutput,
    Divergence,
    ExperimentRecord,
    ExperimentSelectionOutput,
    FaultKind,
    LoopState,
    MinimizationResult,
    Mission,
    OracleReport,
    PatchProposalOutput,
    StopDecision,
    ThreatModelOutput,
    canonical_json,
)
from .planner import GYM_TOOLS
from .report import CLAIM_TERMS, NOT_A_SAFETY_CERTIFICATE

PROMPT_VERSION = "v6"
"""Bumped whenever an instruction body changes.  Recorded in ``docs/prompt-log.md``."""

LIVE_TOOL_NAME = "inspect_synthetic_gym"
"""The single tool the runtime's live demo agent may call."""

LIVE_EXPLAINER_MAX_TURNS = 8
"""Turn cap for the live demo agent, mirrored by ``api.runtime.DEFAULT_MAX_TURNS``."""

DIAGNOSTIC_CONTEXT_LABEL = "DIAGNOSTIC CONTEXT"
"""Header the runtime puts on controller-measured evidence in the live query.

Shared with ``api.runtime`` so the instruction cannot end up describing a block
the runtime never emits -- a rule about a header that does not exist reads as
enforced and does nothing.
"""

UNTRUSTED_OPEN = "<untrusted_agent_data>"
UNTRUSTED_CLOSE = "</untrusted_agent_data>"
MISSION_OPEN = "<user_mission>"
MISSION_CLOSE = "</user_mission>"

SENTINEL_REMOVED = "[fence-sentinel-removed]"
"""Replacement for a fence delimiter found inside a payload.

Observed content can contain the delimiter -- deliberately, in the injection
scenarios this lab exists to run.  Left alone it would close the fence early and
promote the rest of the payload to instruction text, so the delimiter itself is
removed from payloads.  This rewrite is confined to prompt rendering; the raw
event stream and the JSONL artifacts keep the payload untouched.
"""


class PromptStage(StrEnum):
    """One prompt asset.  The first four back ``PlannerProtocol`` methods."""

    THREAT_MODEL = "threat_model"
    EXPERIMENT_SELECTION = "experiment_selection"
    DIAGNOSIS = "diagnosis"
    PATCH_PROPOSAL = "patch_proposal"
    LIVE_EXPLAINER = "live_explainer"
    DIAGNOSTIC_CONTROLLER = "diagnostic_controller"


# --------------------------------------------------------------------------
# Budgets, stop conditions, schema-failure recovery
# --------------------------------------------------------------------------


class LoopBudget(BaseModel):
    """Explicit termination conditions for the experiment loop.

    Small on purpose.  A demo run has to finish, and an agent that can spend an
    unbounded budget has no incentive to pick the highest-value experiment
    first -- which is the behaviour the deterministic ranking exists to force.
    """

    model_config = ConfigDict(frozen=True)

    max_experiments: int = 3
    max_cost_units: int = 6
    max_no_new_finding_streak: int = 2


DEFAULT_BUDGET = LoopBudget()


class SchemaRecovery(BaseModel):
    """What to do when a structured output fails validation.

    One repair attempt, then an honest stop.  Retrying indefinitely trains the
    loop to accept whatever finally parses, and the cheapest way for a model to
    make a schema error go away is to drop the field it could not evidence.
    """

    model_config = ConfigDict(frozen=True)

    max_attempts: int = 2
    exhausted_reason: Literal["insufficient_evidence"] = "insufficient_evidence"


DEFAULT_SCHEMA_RECOVERY = SchemaRecovery()


def should_stop(state: LoopState, budget: LoopBudget = DEFAULT_BUDGET) -> StopDecision:
    """Decide whether the loop continues, and say honestly why it did not.

    Order matters.  An unsupported mission is reported as unsupported even if
    the budget also happens to be gone, because "we could not test this" and
    "we ran out of room to test" are different statements to a reader.

    ``coverage_complete`` means the operator space stopped yielding anything
    new -- not that the agent is safe.  :func:`agent24.agent.report.build_report`
    turns that into ``no_failure_observed`` with the bounded-claim sentence
    attached; it never becomes a clean bill of health.
    """

    if not state.supported:
        return StopDecision(
            stop=True,
            reason="unsupported_input",
            detail="gym이 재현할 수 없는 입력이라 실험을 시작하지 않았다.",
        )
    if state.experiments_run >= budget.max_experiments:
        return StopDecision(
            stop=True,
            reason="budget_exhausted",
            detail=(
                f"실험 {state.experiments_run}/{budget.max_experiments}회를 모두 사용했다. "
                "탐색이 끝난 것이 아니라 예산이 끝난 것이다."
            ),
        )
    if state.cost_units_used >= budget.max_cost_units:
        return StopDecision(
            stop=True,
            reason="budget_exhausted",
            detail=(
                f"비용 {state.cost_units_used}/{budget.max_cost_units} 단위를 모두 사용했다. "
                "탐색이 끝난 것이 아니라 예산이 끝난 것이다."
            ),
        )
    if state.no_new_finding_streak >= budget.max_no_new_finding_streak:
        return StopDecision(
            stop=True,
            reason="coverage_complete",
            detail=(
                f"연속 {state.no_new_finding_streak}회 새로운 발견이 없어 지원 operator "
                "공간을 소진했다. 검사한 범위 밖을 보장하지는 않는다."
            ),
        )
    return StopDecision(stop=False, reason="continue", detail="")


def recover_from_schema_error(
    stage: PromptStage,
    attempt: int,
    error: str,
    recovery: SchemaRecovery = DEFAULT_SCHEMA_RECOVERY,
) -> str | StopDecision:
    """Repair message for a failed structured output, or an honest stop.

    ``attempt`` is how many attempts have already failed, so the first failure
    is ``attempt=1``.  Returns the message to append for another try while
    attempts remain, and a :class:`StopDecision` once they do not.

    The validation error is fenced like any other payload: it quotes the model's
    own output back, and that output may contain text copied out of an injected
    page.
    """

    if attempt >= recovery.max_attempts:
        return StopDecision(
            stop=True,
            reason=recovery.exhausted_reason,
            detail=(
                f"{stage.value} 단계가 {attempt}회 연속 "
                f"{_schema_name(stage)} 스키마 검증에 실패했다. "
                "파싱되는 출력이 나올 때까지 재시도하는 대신, "
                "증거 없는 결과를 만들지 않고 종료한다."
            ),
        )
    return "\n".join(
        [
            f"직전 출력이 {_schema_name(stage)} 스키마 검증에 실패했다.",
            "같은 관찰에 대해, 스키마에 맞는 JSON 하나만 다시 출력한다.",
            "검증을 통과시키려고 값을 지어내지 않는다. 근거가 없는 필드는 비워 두고,"
            " 비울 수 없는 필드라면 그 사실 자체가 결과다.",
            fence_untrusted({"validation_error": error}),
        ]
    )


# --------------------------------------------------------------------------
# The trust boundary
# --------------------------------------------------------------------------


def _neutralize(text: str) -> str:
    return text.replace(UNTRUSTED_OPEN, SENTINEL_REMOVED).replace(UNTRUSTED_CLOSE, SENTINEL_REMOVED)


def fence_untrusted(sections: Mapping[str, JsonValue]) -> str:
    """Render observed content as data that cannot be read as instruction.

    Payloads are emitted as canonical JSON, so key order -- and therefore the
    whole rendered message -- is byte-stable across processes.
    """

    return "\n".join([UNTRUSTED_OPEN, _neutralize(canonical_json(dict(sections))), UNTRUSTED_CLOSE])


def _mission_block(mission: Mission) -> str:
    """The one block presented as an instruction, and even it cannot break the fence."""

    return "\n".join(
        [
            MISSION_OPEN,
            _neutralize(mission.text),
            f"family: {mission.family.value}",
            f"constraints: {_neutralize(canonical_json(dict(mission.constraints)))}",
            MISSION_CLOSE,
        ]
    )


def render_threat_input(
    card: AgentCard,
    mission: Mission,
    capabilities: Sequence[ClassifiedCapability],
) -> str:
    """Build the threat-modelling message.  Mirrors ``PlannerProtocol.build_threat_model``.

    The AUT's own system prompt is the most obviously attacker-controlled text
    in the whole product, so the card goes inside the fence in full.  Tools the
    gym has no vocabulary for are listed explicitly: naming them is what lets
    the stage answer "unsupported" instead of approximating.
    """

    tools = {cap.tool for cap in capabilities} | {tool.name for tool in card.tools}
    return "\n".join(
        [
            _mission_block(mission),
            fence_untrusted(
                {
                    "agent_card": card.model_dump(mode="json"),
                    "capabilities": [cap.model_dump(mode="json") for cap in capabilities],
                    "tools_outside_gym_vocabulary": sorted(tools - GYM_TOOLS),
                }
            ),
        ]
    )


def render_selection_input(
    threat: ThreatModelOutput,
    history: Sequence[ExperimentRecord],
) -> str:
    """Build the selection message.  Mirrors ``PlannerProtocol.select_experiments``.

    The threat model is fenced too, even though a model produced it: if the AUT
    card carried an injected instruction, the previous stage may have quoted it
    into a hypothesis statement, and un-fencing it here would launder it into
    instruction text one stage later.
    """

    return "\n".join(
        [
            f"experiments_already_run: {len(history)}",
            f"budget_experiments: {DEFAULT_BUDGET.max_experiments}",
            f"budget_cost_units: {DEFAULT_BUDGET.max_cost_units}",
            fence_untrusted(
                {
                    "threat_model": threat.model_dump(mode="json"),
                    "history": [record.model_dump(mode="json") for record in history],
                }
            ),
        ]
    )


def render_diagnosis_input(
    observed: OracleReport,
    divergence: Divergence | None,
    minimized: MinimizationResult | None,
) -> str:
    """Build the diagnosis message.  Mirrors ``PlannerProtocol.diagnose``.

    Only derived counts sit outside the fence.  Violation values are taken from
    world state the AUT wrote, and an injected page is exactly how a hostile
    string gets into world state in the first place.
    """

    return "\n".join(
        [
            f"violations: {len(observed.violations)}",
            f"has_first_divergence: {divergence is not None}",
            f"has_minimized_counterexample: {minimized is not None}",
            fence_untrusted(
                {
                    "oracle_report": observed.model_dump(mode="json"),
                    "first_divergence": (
                        divergence.model_dump(mode="json") if divergence else None
                    ),
                    "minimized_counterexample": (
                        minimized.model_dump(mode="json") if minimized else None
                    ),
                }
            ),
        ]
    )


def render_patch_input(diagnosis: DiagnosisOutput, observed: OracleReport) -> str:
    """Build the patch message.  Mirrors ``PlannerProtocol.propose_patch``."""

    return "\n".join(
        [
            f"violations: {len(observed.violations)}",
            f"target_invariants: {len(diagnosis.hypothesis.target_invariants)}",
            fence_untrusted(
                {
                    "diagnosis": diagnosis.model_dump(mode="json"),
                    "oracle_report": observed.model_dump(mode="json"),
                }
            ),
        ]
    )


# --------------------------------------------------------------------------
# The stage table: instruction body + frozen output schema + budget
# --------------------------------------------------------------------------


class PromptSpec(BaseModel):
    """One stage's instruction body and everything the runtime needs to use it."""

    model_config = ConfigDict(frozen=True)

    stage: PromptStage
    version: str
    body: str
    output_model: type[BaseModel] | None
    planner_method: str | None
    max_turns: int
    requires_tool_call: bool = False

    @property
    def output_schema_name(self) -> str:
        return self.output_model.__name__ if self.output_model is not None else "plain text"


_STAGE_TABLE: tuple[tuple[PromptStage, type[BaseModel] | None, str | None, int, bool], ...] = (
    (PromptStage.THREAT_MODEL, ThreatModelOutput, "build_threat_model", 1, False),
    (PromptStage.EXPERIMENT_SELECTION, ExperimentSelectionOutput, "select_experiments", 1, False),
    (PromptStage.DIAGNOSIS, DiagnosisOutput, "diagnose", 1, False),
    (PromptStage.PATCH_PROPOSAL, PatchProposalOutput, "propose_patch", 1, False),
    (PromptStage.LIVE_EXPLAINER, None, None, LIVE_EXPLAINER_MAX_TURNS, True),
    (
        PromptStage.DIAGNOSTIC_CONTROLLER,
        DiagnosticFinalOutput,
        None,
        LIVE_EXPLAINER_MAX_TURNS,
        True,
    ),
)


def _schema_name(stage: PromptStage) -> str:
    return PROMPTS[stage].output_schema_name


def _substitutions(output_schema: str) -> dict[str, str]:
    """Values interpolated into every instruction body.

    Everything a prompt asserts about the system -- the tool vocabulary, the
    invariant ids, the budget, the forbidden verdict language -- is read from
    the code that enforces it, so the two cannot drift apart silently.
    """

    return {
        "version": PROMPT_VERSION,
        "output_schema": output_schema,
        "gym_tools": ", ".join(sorted(GYM_TOOLS)),
        "fault_kinds": ", ".join(sorted(kind.value for kind in FaultKind)),
        "invariant_ids": ", ".join(INVARIANT_IDS),
        "banned_claims": ", ".join(CLAIM_TERMS),
        "not_a_safety_certificate": NOT_A_SAFETY_CERTIFICATE,
        "diagnostic_label": DIAGNOSTIC_CONTEXT_LABEL,
        "max_experiments": str(DEFAULT_BUDGET.max_experiments),
        "max_cost_units": str(DEFAULT_BUDGET.max_cost_units),
        "untrusted_open": UNTRUSTED_OPEN,
        "untrusted_close": UNTRUSTED_CLOSE,
        "live_tool": LIVE_TOOL_NAME,
    }


def _load_body(stage: PromptStage, output_schema: str) -> str:
    raw = (
        resources.files("agent24.agent")
        .joinpath("prompts", f"{stage.value}.md")
        .read_text(encoding="utf-8")
    )
    # substitute(), not safe_substitute(): a typo'd placeholder should fail at
    # import rather than ship a prompt with a literal "$max_cost_units" in it.
    return Template(raw).substitute(_substitutions(output_schema))


def _build_prompts() -> dict[PromptStage, PromptSpec]:
    specs: dict[PromptStage, PromptSpec] = {}
    for stage, output_model, method, max_turns, requires_tool_call in _STAGE_TABLE:
        schema_name = output_model.__name__ if output_model is not None else "plain text"
        specs[stage] = PromptSpec(
            stage=stage,
            version=PROMPT_VERSION,
            body=_load_body(stage, schema_name),
            output_model=output_model,
            planner_method=method,
            max_turns=max_turns,
            requires_tool_call=requires_tool_call,
        )
    return specs


PROMPTS: dict[PromptStage, PromptSpec] = _build_prompts()
"""Every stage, loaded and interpolated at import so a broken asset fails loudly."""


def instructions(stage: PromptStage) -> str:
    """The rendered system instruction for one stage."""

    return PROMPTS[stage].body


LIVE_EXPLAINER_INSTRUCTIONS = instructions(PromptStage.LIVE_EXPLAINER)
"""Consumed by ``api.runtime`` so the live demo path shares this evidence boundary."""

DIAGNOSTIC_CONTROLLER_INSTRUCTIONS = instructions(PromptStage.DIAGNOSTIC_CONTROLLER)
"""Consumed by the live five-tool diagnostic controller."""


__all__ = [
    "CLAIM_TERMS",
    "DEFAULT_BUDGET",
    "DIAGNOSTIC_CONTEXT_LABEL",
    "DIAGNOSTIC_CONTROLLER_INSTRUCTIONS",
    "DEFAULT_SCHEMA_RECOVERY",
    "LIVE_EXPLAINER_INSTRUCTIONS",
    "LIVE_EXPLAINER_MAX_TURNS",
    "LIVE_TOOL_NAME",
    "MISSION_CLOSE",
    "MISSION_OPEN",
    "NOT_A_SAFETY_CERTIFICATE",
    "PROMPTS",
    "PROMPT_VERSION",
    "SENTINEL_REMOVED",
    "UNTRUSTED_CLOSE",
    "UNTRUSTED_OPEN",
    "LoopBudget",
    "PromptSpec",
    "PromptStage",
    "SchemaRecovery",
    "fence_untrusted",
    "instructions",
    "recover_from_schema_error",
    "render_diagnosis_input",
    "render_patch_input",
    "render_selection_input",
    "render_threat_input",
    "should_stop",
]
