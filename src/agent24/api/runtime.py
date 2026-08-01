"""OpenAI Agents SDK runtime and deterministic offline fallback."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from agents import Agent, OpenAIProvider, RunConfig, Runner
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.stream_events import RunItemStreamEvent

from agent24.agent.loop import (
    SYNTHETIC_SCOPE,
    DeterministicLabLoop,
    DiagnosticLoopResult,
    unsupported_reports,
)
from agent24.agent.manifest import ManifestLoadError
from agent24.agent.models import ExperimentPlan, StopDecision, run_digest
from agent24.agent.packs import pack_failure_stop
from agent24.agent.participant_intake import (
    GitHubEvidenceMetadataFetcher,
    ParticipantCompatibilityResult,
    ParticipantStaticProfiler,
)
from agent24.agent.prompts import (
    DIAGNOSTIC_CONTEXT_LABEL,
    LIVE_EXPLAINER_INSTRUCTIONS,
    LIVE_EXPLAINER_MAX_TURNS,
)
from agent24.agent.source import GitHubApiRevisionResolver, SourceResolutionError
from agent24.events import RunChannel
from agent24.tools import PAYMENT_FIXTURE, SyntheticGym, protected_replay

from .config import RuntimeSettings
from .contracts import ExecutionScope, RunTerminalPayload, StageFailurePayload
from .preflight import (
    ExternalAgentPreflight,
    ExternalPreflightResult,
    ExternalTarget,
    GitHubContentsManifestFetcher,
    GitHubContentsSourceFetcher,
    ManifestFetchError,
)

DEFAULT_MAX_TURNS = LIVE_EXPLAINER_MAX_TURNS
DEFAULT_TIMEOUT_SECONDS = 45.0
NO_TARGET_SCOPE: ExecutionScope = "no_target"
NO_TARGET_OFFLINE_SCOPE: ExecutionScope = "no_target_offline_demo"
SYNTHETIC_EXECUTION_SCOPE: ExecutionScope = "synthetic_archetype"
ALLOWLISTED_EXECUTION_SCOPE: ExecutionScope = "allowlisted_adapter"
COMPATIBILITY_EXECUTION_SCOPE: ExecutionScope = "compatibility_only"
NO_EXECUTION_SCOPE: ExecutionScope = "none"


@dataclass(frozen=True, slots=True)
class _PreflightTerminal:
    """A typed preflight stop passed to the central terminal builder."""

    mode: str
    status: str
    source_resolved: bool
    execution_scope: ExecutionScope
    message: str
    experiments_run: int = 0
    findings: int = 0


def _terminal_payload(
    *,
    status: str,
    mode: str,
    source_resolved: bool,
    diagnostic_completed: bool,
    openai_analysis_completed: bool,
    execution_scope: ExecutionScope,
    message: str | None = None,
    experiments_run: int | None = None,
    findings: int | None = None,
) -> dict[str, Any]:
    """Build every terminal payload through one schema and one field set."""

    return RunTerminalPayload(
        status=status,
        mode=mode,
        source_resolved=source_resolved,
        diagnostic_completed=diagnostic_completed,
        openai_analysis_completed=openai_analysis_completed,
        execution_scope=execution_scope,
        message=message[:500] if message else None,
        experiments_run=experiments_run,
        findings=findings,
    ).model_dump(mode="json", exclude_none=True)


def _read_timeout(value: float | None) -> float:
    if value is not None:
        return max(0.1, value)
    raw_value = os.getenv("AGENT24_RUN_TIMEOUT_SECONDS", "")
    try:
        return max(0.1, float(raw_value)) if raw_value else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


class OpenAIWhiteBoxAdapter:
    """Run one input through an Agents SDK agent and the synthetic gym.

    The adapter intentionally does not implement an OpenAI request loop.  The
    official ``Runner`` owns model turns, tool dispatch, and the final answer;
    this class only observes semantic SDK stream events and forwards raw tool
    items to the event channel.

    ``openai_client`` and ``model_override`` are dependency-injection seams for
    an integration test.  Production reads ``OPENAI_API_KEY`` at runtime and
    passes it only to the official provider; it is never placed in an event or
    log.
    """

    def __init__(
        self,
        *,
        gym: SyntheticGym | None = None,
        runner: Any = Runner,
        openai_client: Any | None = None,
        model_override: Any | None = None,
        model_name: str | None = None,
        timeout_seconds: float | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        preflight: ExternalAgentPreflight | None = None,
        lab_loop: DeterministicLabLoop | None = None,
        settings: RuntimeSettings | None = None,
    ) -> None:
        self.gym = gym or SyntheticGym()
        self.runner = runner
        self.openai_client = openai_client
        self.model_override = model_override
        self.settings = settings or RuntimeSettings()
        self.model_name = (
            model_name
            if model_name is not None
            else os.getenv("OPENAI_MODEL") or self.settings.openai_model or None
        )
        configured_timeout = (
            timeout_seconds if timeout_seconds is not None else self.settings.run_timeout_seconds
        )
        self.timeout_seconds = _read_timeout(configured_timeout)
        self.max_turns = max(1, max_turns)
        self.preflight = preflight or ExternalAgentPreflight(
            source_resolver=GitHubApiRevisionResolver(token=self.settings.github_token),
            manifest_fetcher=GitHubContentsManifestFetcher(token=self.settings.github_token),
            source_file_fetcher=GitHubContentsSourceFetcher(token=self.settings.github_token),
            static_profiler=ParticipantStaticProfiler(
                evidence_fetcher=GitHubEvidenceMetadataFetcher(
                    token=self.settings.github_token
                )
            ),
        )
        self.lab_loop = lab_loop or DeterministicLabLoop()

    def _api_key(self) -> str | None:
        # An explicitly exported process variable wins over .env settings.  No
        # caller receives this value; it is only handed to AsyncOpenAI.
        return os.getenv("OPENAI_API_KEY") or self.settings.openai_api_key

    @property
    def openai_configured(self) -> bool:
        """Whether the live SDK path is allowed to construct an OpenAI client."""

        return self.openai_client is not None or bool((self._api_key() or "").strip())

    @property
    def mode(self) -> str:
        return "live" if self.openai_configured else "offline_demo"

    @staticmethod
    def _execution_scope(preflight: ExternalPreflightResult) -> ExecutionScope:
        return (
            ALLOWLISTED_EXECUTION_SCOPE
            if preflight.adapter_contract is not None
            else SYNTHETIC_EXECUTION_SCOPE
        )

    @staticmethod
    def _publish_stage_failure(
        channel: RunChannel,
        *,
        stage: str,
        code: str,
        message: str,
        pack_id: str | None = None,
    ) -> None:
        """Publish bounded failure metadata without making a second terminal."""

        payload = StageFailurePayload(
            stage=stage,  # type: ignore[arg-type] - callers use the closed stage vocabulary
            code=code,
            message=message[:500],
            pack_id=pack_id,
        )
        channel.publish("stage_failed", payload, summary=f"{stage} stage failed")

    @staticmethod
    def _publish_terminal(
        channel: RunChannel,
        *,
        status: str,
        mode: str,
        source_resolved: bool,
        diagnostic_completed: bool,
        openai_analysis_completed: bool,
        execution_scope: ExecutionScope,
        message: str | None = None,
        experiments_run: int | None = None,
        findings: int | None = None,
    ) -> None:
        channel.publish(
            "run_completed",
            _terminal_payload(
                status=status,
                mode=mode,
                source_resolved=source_resolved,
                diagnostic_completed=diagnostic_completed,
                openai_analysis_completed=openai_analysis_completed,
                execution_scope=execution_scope,
                message=message,
                experiments_run=experiments_run,
                findings=findings,
            ),
        )

    @staticmethod
    def _publish_controller_summary(
        result: DiagnosticLoopResult,
        channel: RunChannel,
        *,
        reason: str,
    ) -> None:
        """Expose only the deterministic report, explicitly marked non-OpenAI."""

        channel.publish(
            "final_output",
            {
                "text": (
                    "Controller-owned diagnostic (OpenAI explanation unavailable): "
                    f"{result.report.bounded_summary}"
                ),
                "analysis_source": "controller",
                "openai_analysis_completed": False,
                "reason": reason,
            },
            summary="controller-derived summary; not an OpenAI response",
        )

    def _agent(self) -> Agent:
        return Agent(
            name="Nightmare Lab AUT",
            # Versioned asset, not a literal: the live demo answer has to respect
            # the same evidence boundary as the offline report path, and a prompt
            # kept here would drift from the one the eval suite checks.
            instructions=LIVE_EXPLAINER_INSTRUCTIONS,
            tools=self.gym.tools(),
            model=self.model_override if self.model_override is not None else self.model_name,
        )

    def _run_config(self) -> RunConfig:
        if self.openai_client is not None:
            provider = OpenAIProvider(openai_client=self.openai_client)
        else:
            # The official provider lazily creates AsyncOpenAI and reads
            # OPENAI_API_KEY here.  Passing the secret through an event or a log
            # is deliberately avoided.
            provider = OpenAIProvider(api_key=self._api_key())
        return RunConfig(model_provider=provider, tracing_disabled=True)

    async def _run_live(self, query: str, channel: RunChannel) -> str:
        streamed = self.runner.run_streamed(
            self._agent(),
            query,
            max_turns=self.max_turns,
            run_config=self._run_config(),
        )
        async for stream_event in streamed.stream_events():
            if not isinstance(stream_event, RunItemStreamEvent):
                continue
            item = stream_event.item
            if stream_event.name == "tool_called" and isinstance(item, ToolCallItem):
                # raw_item is the SDK's unedited Responses API function-call
                # object.  The event layer performs JSON encoding only at the
                # transport boundary.
                channel.publish("tool_call", item.raw_item, summary=item.tool_name)
            elif stream_event.name == "tool_output" and isinstance(item, ToolCallOutputItem):
                channel.publish("tool_result", item.raw_item, summary=item.call_id)

        final_output = streamed.final_output
        return final_output if isinstance(final_output, str) else json.dumps(
            final_output, ensure_ascii=False, default=str
        )

    async def _run_offline(self, query: str, channel: RunChannel, *, reason: str) -> str:
        call_id = f"offline_{uuid.uuid4().hex}"
        arguments = json.dumps({"query": query[:240]}, ensure_ascii=False, separators=(",", ":"))
        channel.publish(
            "offline_demo",
            {"status": "offline_demo", "reason": reason},
            summary="SDK live path unavailable",
        )
        channel.publish(
            "tool_call",
            {
                "type": "function_call",
                "name": "inspect_synthetic_gym",
                "call_id": call_id,
                "arguments": arguments,
            },
            summary="inspect_synthetic_gym",
        )
        result = self.gym.offline_result(query)
        result_text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        channel.publish(
            "tool_result",
            {"type": "function_call_output", "call_id": call_id, "output": result_text},
            summary=call_id,
        )
        final_text = (
            f"Offline Nightmare Lab 진단: {result['label']} ({result['scenario_id']}). "
            f"관찰 신호는 '{result['failure_signal']}'이며, "
            f"우선 '{result['probe']}' 검증을 실행하세요."
        )
        channel.publish("final_output", {"text": final_text})
        return final_text

    async def _run_external_preflight(
        self,
        target: ExternalTarget,
        channel: RunChannel,
    ) -> ExternalPreflightResult | _PreflightTerminal:
        """Publish pinned planning artifacts, or return a typed preflight stop."""

        channel.publish("phase.changed", {"phase": "CLONE"}, summary="external preflight")
        try:
            result = await asyncio.to_thread(self.preflight.run, target)
        except (SourceResolutionError, ManifestLoadError, ManifestFetchError) as error:
            source_failure = isinstance(error, SourceResolutionError)
            code = "source_unresolved" if source_failure else "source_preflight_failed"
            message = (
                "GitHub source를 immutable SHA로 고정하지 못해 제출 Agent 분석과 실험을 "
                "실행하지 않았습니다."
                if source_failure
                else "외부 Agent source preflight를 완료하지 못해 제출 Agent 분석과 synthetic "
                "target 실험을 실행하지 않았습니다."
            )
            self._publish_stage_failure(
                channel,
                stage="source",
                code=code,
                message=message,
            )
            return _PreflightTerminal(
                mode=self.mode,
                status=code,
                source_resolved=not source_failure,
                execution_scope=NO_EXECUTION_SCOPE,
                message=message,
            )
        except Exception:  # noqa: BLE001 - source boundary must close with safe typed state
            message = (
                "외부 Agent source preflight 중 안전하게 분류할 수 없는 오류가 발생해 "
                "제출 Agent 분석과 실험을 실행하지 않았습니다."
            )
            self._publish_stage_failure(
                channel,
                stage="source",
                code="source_preflight_failed",
                message=message,
            )
            return _PreflightTerminal(
                mode=self.mode,
                status="source_preflight_failed",
                source_resolved=False,
                execution_scope=NO_EXECUTION_SCOPE,
                message=message,
            )

        channel.publish("source_descriptor", result.source, summary=result.source.source_ref)
        channel.publish(
            "source_snapshot",
            result.source_snapshot,
            summary=(
                f"{result.source_snapshot.mode} · "
                f"{result.source_snapshot.total_bytes} bytes"
            ),
        )
        adapter_contract = getattr(result, "adapter_contract", None)
        if adapter_contract is not None:
            channel.publish(
                "adapter.matched",
                adapter_contract,
                summary=adapter_contract.adapter_id,
            )
        channel.publish(
            "target_profile",
            result.target_profile,
            summary=result.target_profile.profile_label,
        )
        if isinstance(result, ParticipantCompatibilityResult):
            channel.publish(
                "pack_selection",
                result.pack_selection,
                summary=result.pack_selection.status.value,
            )
            channel.publish(
                "compatibility_report",
                result.compatibility_report,
                summary=result.compatibility_report.status.value,
            )
            return _PreflightTerminal(
                mode="compatibility_only",
                status=result.pack_selection.status.value,
                source_resolved=True,
                execution_scope=COMPATIBILITY_EXECUTION_SCOPE,
                message=result.compatibility_report.message,
            )

        channel.publish(
            "pack_selection",
            result.compatibility_selection,
            summary=result.compatibility_selection.status.value,
        )
        channel.publish("behavior_profile", result.profile, summary=result.profile.agent_name)
        # Published before the stop branch on purpose: the routing decision is
        # exactly what a reader needs when the run terminates as unsupported,
        # and that path never reaches ``experiment_plan``.
        channel.publish(
            "pack.selected",
            result.pack_selection,
            summary=result.pack_selection.pack_id or "ambiguous",
        )
        if isinstance(result.decision, StopDecision):
            finding_report, lab_report = unsupported_reports(
                manifest=result.manifest,
                profile=result.profile,
                mission=result.mission,
                stop=result.decision,
            )
            channel.publish(
                "finding_report",
                finding_report,
                summary=finding_report.status.value,
            )
            channel.publish("lab_report", lab_report, summary=result.decision.reason)
            return _PreflightTerminal(
                mode=self.mode,
                status="unsupported",
                source_resolved=True,
                execution_scope=NO_EXECUTION_SCOPE,
                message=result.decision.detail,
            )
        if not isinstance(result.decision, ExperimentPlan):
            raise TypeError("preflight returned an unknown decision type")
        channel.publish(
            "experiment_plan",
            result.decision,
            summary=result.decision.plan_id,
        )
        return result

    @staticmethod
    def _world_view(run) -> dict[str, int]:
        spend = int(run.world_state.get("wallet", {}).get("total_spend_krw", 0))
        return {
            "wallet_krw": max(0, 500_000 - spend),
            "orders": len(run.world_state.get("orders", [])),
            "outbound_emails": len(run.world_state.get("inbox", {}).get("sent", [])),
            "calendar_events": len(run.world_state.get("calendar", {}).get("events", [])),
            "files_touched": 0,
        }

    @staticmethod
    def _autopsy_steps(result: DiagnosticLoopResult) -> list[dict[str, str]]:
        scope_label = (
            "allowlisted adapter"
            if result.execution_scope != SYNTHETIC_SCOPE
            else "synthetic run"
        )
        steps = [
            {
                "kind": "observed",
                "text": (
                    f"{len(result.observed.violations)}개 invariant 위반을 controller oracle이 "
                    f"{scope_label}에서 측정"
                ),
            }
        ]
        if result.divergence is not None:
            steps.append(
                {
                    "kind": "divergence",
                    "text": (
                        f"healthy twin과 최초 분기: trace[{result.divergence.index}] "
                        f"({result.divergence.kind})"
                    ),
                }
            )
        steps.extend(
            {
                "kind": "observed",
                "text": (
                    f"{violation.invariant_id}: actual={violation.actual}, "
                    f"expected={violation.expected}"
                ),
            }
            for violation in result.observed.violations
        )
        return steps

    async def _run_diagnostic_loop(
        self,
        preflight: ExternalPreflightResult,
        channel: RunChannel,
    ) -> DiagnosticLoopResult:
        decision = preflight.decision
        if not isinstance(decision, ExperimentPlan):
            raise TypeError("diagnostic loop requires an ExperimentPlan")

        channel.publish(
            "phase.changed",
            {"phase": "CRASH"},
            summary=(
                "allowlisted adapter fault run"
                if preflight.adapter_contract is not None
                else "synthetic fault run"
            ),
        )
        result = await self.lab_loop.run(
            manifest=preflight.manifest,
            profile=preflight.profile,
            mission=preflight.mission,
            plan=decision,
            adapter_contract=preflight.adapter_contract,
        )
        channel.publish(
            "gym.baseline.completed",
            {
                "execution_scope": (
                    "allowlisted_adapter"
                    if preflight.adapter_contract is not None
                    else "synthetic_archetype"
                ),
                "scope_note": result.execution_scope,
                "scenario_id": result.baseline.scenario.scenario_id,
                "seed": result.baseline.scenario.seed,
                "run_digest": f"sha256:{run_digest(result.baseline)}",
                "trace_events": len(result.baseline.trace),
                "ledger_entries": len(result.baseline.ledger),
            },
            summary="healthy twin baseline",
        )
        for trace_event in result.perturbed.trace:
            if trace_event.kind == "tool_call" and trace_event.call is not None:
                channel.publish(
                    "gym.tool_call",
                    trace_event.call,
                    summary=trace_event.call.tool,
                )
            elif trace_event.kind == "tool_result" and trace_event.result is not None:
                channel.publish(
                    "gym.tool_result",
                    trace_event.result,
                    summary=trace_event.result.status,
                )
        channel.publish("oracle.report", result.observed, summary="controller ground truth")

        if result.observed.violations:
            failed_world = self._world_view(result.perturbed)
            violation_ids = sorted(result.observed.violated_ids())
            channel.publish(
                "damage.updated",
                {
                    "label": (
                        "ALLOWLISTED ADAPTER INVARIANT VIOLATION"
                        if preflight.adapter_contract is not None
                        else "SYNTHETIC INVARIANT VIOLATION"
                    ),
                    "headline": f"{len(violation_ids)}개 invariant 위반 측정",
                    "detail": f"{', '.join(violation_ids)} · {result.execution_scope}",
                    "world": failed_world,
                },
            )
            channel.publish("failure.detected", {"invariants": violation_ids})

        channel.publish("phase.changed", {"phase": "AUTOPSY"}, summary="first divergence")
        channel.publish("autopsy.ready", {"steps": self._autopsy_steps(result)})
        channel.publish("phase.changed", {"phase": "VACCINE"}, summary="bounded policy")
        if result.patch is not None:
            channel.publish(
                "vaccine.proposed",
                {"patch": result.patch.model_dump_json(indent=2)},
                summary=result.patch.patch_id,
            )

        channel.publish("phase.changed", {"phase": "REPLAY"}, summary="protected replay")
        if result.verification is not None:
            verification = result.verification
            checks = {
                "budget": not any(
                    violation.invariant_id == "task.total_spend"
                    for violation in verification.same_seed.oracle.violations
                ),
                "count": not any(
                    violation.invariant_id
                    in {"task.purchase_count", "platform.exactly_once_payment"}
                    for violation in verification.same_seed.oracle.violations
                ),
                "task": not any(
                    violation.invariant_id == "task.mission_completed"
                    for violation in verification.same_seed.oracle.violations
                ),
                "benign": bool(verification.benign)
                and all(gate.passed for gate in verification.benign),
            }
            channel.publish("verification.updated", {"checks": checks})
            channel.publish(
                "replay.completed",
                {
                    "success": verification.accepted,
                    "world": self._world_view(result.protected or result.perturbed),
                    "checks": checks,
                },
                summary="accepted" if verification.accepted else "rejected",
            )

        if (
            preflight.adapter_contract is None
            and decision.scenario.faults
            and decision.scenario.faults[0].fault.value == "commit_then_timeout"
        ):
            sandbox_replay = await asyncio.to_thread(
                protected_replay,
                PAYMENT_FIXTURE,
                seed=decision.scenario.seed,
            )
            channel.publish(
                "protected_replay",
                sandbox_replay.to_dict(),
                summary="SandboxGym payment replay accepted"
                if sandbox_replay.accepted
                else "SandboxGym payment replay rejected",
            )
        elif preflight.adapter_contract is not None and result.protected is not None:
            channel.publish(
                "protected_replay",
                {
                    "accepted": bool(
                        result.verification is not None and result.verification.accepted
                    ),
                    "execution_scope": "allowlisted_adapter",
                    "perturbed": {
                        "purchase_count": sum(
                            entry.tool == "complete_purchase" for entry in result.perturbed.ledger
                        ),
                        "orders": len(result.perturbed.world_state.get("orders", [])),
                    },
                    "protected": {
                        "purchase_count": sum(
                            entry.tool == "complete_purchase" for entry in result.protected.ledger
                        ),
                        "orders": len(result.protected.world_state.get("orders", [])),
                    },
                    "scope_note": result.execution_scope,
                },
                summary="allowlisted adapter replay accepted"
                if result.verification is not None and result.verification.accepted
                else "allowlisted adapter replay rejected",
            )

        channel.publish("finding_report", result.report, summary=result.report.status.value)
        channel.publish("lab_report", result.lab_report, summary=result.report.status.value)
        return result

    @staticmethod
    def _diagnostic_query(
        query: str,
        preflight: ExternalPreflightResult,
        result: DiagnosticLoopResult,
    ) -> str:
        context = {
            "execution_scope": (
                "allowlisted_adapter"
                if preflight.adapter_contract is not None
                else "synthetic_archetype"
            ),
            "scope_note": result.execution_scope,
            "source_ref": preflight.source.source_ref,
            "pack_id": preflight.pack_selection.pack_id,
            "plan_id": preflight.decision.plan_id,
            "finding_status": result.report.status.value,
            "bounded_summary": result.report.bounded_summary,
            "violated_invariants": sorted(result.observed.violated_ids()),
            "mitigation_verified": bool(
                result.verification is not None and result.verification.accepted
            ),
        }
        return (
            f"{query}\n\n{DIAGNOSTIC_CONTEXT_LABEL} (controller-owned JSON; execution scope):\n"
            f"{json.dumps(context, ensure_ascii=False, separators=(',', ':'))}"
        )

    async def execute(
        self,
        query: str,
        channel: RunChannel,
        *,
        target: ExternalTarget | None = None,
    ) -> None:
        """Execute a run and always close the channel after its final event."""

        preflight_result: ExternalPreflightResult | None = None
        diagnostic_result: DiagnosticLoopResult | None = None
        terminal_emitted = False
        execution_scope: ExecutionScope = (
            NO_EXECUTION_SCOPE if target is not None else NO_TARGET_SCOPE
        )
        try:
            channel.publish(
                "run_started",
                {
                    "mode": self.mode,
                    "input_received": True,
                    "external_target": target is not None,
                    "source_resolved": False,
                    "diagnostic_completed": False,
                    "openai_analysis_completed": False,
                    "execution_scope": NO_EXECUTION_SCOPE,
                    **(
                        {
                            "target": {
                                "repositoryUrl": target.repository_url,
                                "requestedRef": target.requested_ref,
                                "resolvedSha": None,
                                "mission": target.mission,
                            },
                            "mission": target.mission,
                        }
                        if target is not None
                        else {}
                    ),
                },
            )
            if target is not None:
                preflight_outcome = await self._run_external_preflight(target, channel)
                if isinstance(preflight_outcome, _PreflightTerminal):
                    self._publish_terminal(
                        channel,
                        status=preflight_outcome.status,
                        mode=preflight_outcome.mode,
                        source_resolved=preflight_outcome.source_resolved,
                        diagnostic_completed=False,
                        openai_analysis_completed=False,
                        execution_scope=preflight_outcome.execution_scope,
                        message=preflight_outcome.message,
                        experiments_run=preflight_outcome.experiments_run,
                        findings=preflight_outcome.findings,
                    )
                    terminal_emitted = True
                    return
                preflight_result = preflight_outcome
                execution_scope = self._execution_scope(preflight_result)
                try:
                    diagnostic_result = await self._run_diagnostic_loop(
                        preflight_result, channel
                    )
                except Exception as error:  # noqa: BLE001 - keep a safe typed terminal state
                    # Name the pack that failed.  Re-routing to another pack
                    # here is exactly how one pack's failure would come out
                    # looking like a different pack's success.  The exception
                    # string stays out of the payload; only its class is safe.
                    stop = pack_failure_stop(
                        preflight_result.pack_selection, type(error).__name__
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="diagnostic",
                        code="diagnostic_loop_failed",
                        message=stop.detail,
                        pack_id=preflight_result.pack_selection.pack_id,
                    )
                    self._publish_terminal(
                        channel,
                        status="diagnostic_loop_failed",
                        mode=self.mode,
                        source_resolved=True,
                        diagnostic_completed=False,
                        openai_analysis_completed=False,
                        execution_scope=NO_EXECUTION_SCOPE,
                        message=stop.detail,
                        experiments_run=0,
                        findings=0,
                    )
                    terminal_emitted = True
                    return
                query = self._diagnostic_query(query, preflight_result, diagnostic_result)

            if target is not None and diagnostic_result is not None:
                if not self.openai_configured:
                    reason = "openai_key_missing"
                    message = (
                        "결정적 controller 진단은 완료했지만 OPENAI_API_KEY가 없어 "
                        "OpenAI 설명을 실행하지 않았습니다. controller report만 보존합니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="openai_analysis",
                        code=reason,
                        message=message,
                    )
                    self._publish_controller_summary(diagnostic_result, channel, reason=reason)
                    self._publish_terminal(
                        channel,
                        status="openai_analysis_unavailable",
                        mode=self.mode,
                        source_resolved=True,
                        diagnostic_completed=True,
                        openai_analysis_completed=False,
                        execution_scope=execution_scope,
                        message=message,
                        experiments_run=diagnostic_result.experiments_run,
                        findings=len(diagnostic_result.lab_report.findings),
                    )
                    terminal_emitted = True
                    return

                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        final_text = await self._run_live(query, channel)
                except TimeoutError:
                    reason = "openai_timeout"
                    message = (
                        "결정적 controller 진단은 완료했지만 OpenAI 설명 요청이 시간 초과되어 "
                        "controller report만 보존합니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="openai_analysis",
                        code=reason,
                        message=message,
                    )
                    self._publish_controller_summary(diagnostic_result, channel, reason=reason)
                    self._publish_terminal(
                        channel,
                        status="openai_analysis_failed",
                        mode=self.mode,
                        source_resolved=True,
                        diagnostic_completed=True,
                        openai_analysis_completed=False,
                        execution_scope=execution_scope,
                        message=message,
                        experiments_run=diagnostic_result.experiments_run,
                        findings=len(diagnostic_result.lab_report.findings),
                    )
                    terminal_emitted = True
                    return
                except Exception:  # noqa: BLE001 - provider detail must stay out of the wire
                    reason = "openai_provider_failed"
                    message = (
                        "결정적 controller 진단은 완료했지만 OpenAI 설명 요청에 실패해 "
                        "controller report만 보존합니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="openai_analysis",
                        code=reason,
                        message=message,
                    )
                    self._publish_controller_summary(diagnostic_result, channel, reason=reason)
                    self._publish_terminal(
                        channel,
                        status="openai_analysis_failed",
                        mode=self.mode,
                        source_resolved=True,
                        diagnostic_completed=True,
                        openai_analysis_completed=False,
                        execution_scope=execution_scope,
                        message=message,
                        experiments_run=diagnostic_result.experiments_run,
                        findings=len(diagnostic_result.lab_report.findings),
                    )
                    terminal_emitted = True
                    return

                channel.publish("final_output", {"text": final_text})
                self._publish_terminal(
                    channel,
                    status="completed",
                    mode="live",
                    source_resolved=True,
                    diagnostic_completed=True,
                    openai_analysis_completed=True,
                    execution_scope=execution_scope,
                    experiments_run=diagnostic_result.experiments_run,
                    findings=len(diagnostic_result.lab_report.findings),
                )
                terminal_emitted = True
                return

            if not self.openai_configured:
                execution_scope = NO_TARGET_OFFLINE_SCOPE
                await self._run_offline(query, channel, reason="OPENAI_API_KEY is not configured")
                self._publish_terminal(
                    channel,
                    status="offline_demo",
                    mode="offline_demo",
                    source_resolved=False,
                    diagnostic_completed=False,
                    openai_analysis_completed=False,
                    execution_scope=execution_scope,
                )
                terminal_emitted = True
                return

            try:
                async with asyncio.timeout(self.timeout_seconds):
                    final_text = await self._run_live(query, channel)
            except TimeoutError:
                reason = "openai_timeout"
                message = "OpenAI 설명 요청이 시간 초과되어 no-target offline_demo로 전환했습니다."
                self._publish_stage_failure(
                    channel,
                    stage="openai_analysis",
                    code=reason,
                    message=message,
                )
                execution_scope = NO_TARGET_OFFLINE_SCOPE
                await self._run_offline(query, channel, reason="OpenAI run timed out")
                self._publish_terminal(
                    channel,
                    status="offline_demo",
                    mode="offline_demo",
                    source_resolved=False,
                    diagnostic_completed=False,
                    openai_analysis_completed=False,
                    execution_scope=execution_scope,
                    message=message,
                )
                terminal_emitted = True
                return
            except Exception:  # noqa: BLE001 - provider detail must stay out of the wire
                reason = "openai_provider_failed"
                message = "OpenAI 설명 요청에 실패해 no-target offline_demo로 전환했습니다."
                self._publish_stage_failure(
                    channel,
                    stage="openai_analysis",
                    code=reason,
                    message=message,
                )
                execution_scope = NO_TARGET_OFFLINE_SCOPE
                await self._run_offline(query, channel, reason="OpenAI run failed")
                self._publish_terminal(
                    channel,
                    status="offline_demo",
                    mode="offline_demo",
                    source_resolved=False,
                    diagnostic_completed=False,
                    openai_analysis_completed=False,
                    execution_scope=execution_scope,
                    message=message,
                )
                terminal_emitted = True
                return

            channel.publish("final_output", {"text": final_text})
            self._publish_terminal(
                channel,
                status="completed",
                mode="live",
                source_resolved=False,
                diagnostic_completed=False,
                openai_analysis_completed=True,
                execution_scope=NO_TARGET_SCOPE,
            )
            terminal_emitted = True
        except Exception:  # noqa: BLE001 - last-resort typed closure; never fallback or leak detail
            if not terminal_emitted and not any(
                event["type"] == "run_completed" for event in channel.events
            ):
                message = (
                    "실행 중 안전하게 분류할 수 없는 오류가 발생해 "
                    "결과를 확정하지 못했습니다."
                )
                self._publish_stage_failure(
                    channel,
                    stage="runtime",
                    code="runtime_failed",
                    message=message,
                )
                self._publish_terminal(
                    channel,
                    status="runtime_failed",
                    mode=self.mode,
                    source_resolved=preflight_result is not None,
                    diagnostic_completed=diagnostic_result is not None,
                    openai_analysis_completed=False,
                    execution_scope=(
                        execution_scope
                        if diagnostic_result is not None
                        else NO_EXECUTION_SCOPE
                        if target is not None
                        else NO_TARGET_OFFLINE_SCOPE
                    ),
                    message=message,
                )
        finally:
            channel.close()


__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_SECONDS",
    "OpenAIWhiteBoxAdapter",
]
