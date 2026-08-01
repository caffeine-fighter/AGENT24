"""OpenAI Agents SDK runtime and deterministic offline fallback."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIProvider,
    RunConfig,
    Runner,
    ToolExecutionConfig,
)
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.stream_events import RunItemStreamEvent

from agent24.agent.diagnostic_controller import (
    DiagnosticControllerError,
    DiagnosticToolController,
)
from agent24.agent.domain_runtime import DomainTargetAgentRun, run_domain_target_agent
from agent24.agent.loop import (
    SYNTHETIC_SCOPE,
    DeterministicLabLoop,
    DiagnosticLoopResult,
    unsupported_reports,
)
from agent24.agent.manifest import ManifestLoadError
from agent24.agent.models import DomainExperimentPlan, ExperimentPlan, StopDecision, run_digest
from agent24.agent.packs import DomainKind, pack_failure_stop, select_domain_pack
from agent24.agent.participant_intake import (
    GitHubEvidenceMetadataFetcher,
    ParticipantCompatibilityResult,
    ParticipantStaticProfiler,
)
from agent24.agent.prompt_contract import (
    PromptContractExtractor,
    PromptContractStatus,
    apply_permission_ceiling,
)
from agent24.agent.prompts import (
    DIAGNOSTIC_CONTEXT_LABEL,
    LIVE_EXPLAINER_INSTRUCTIONS,
    LIVE_EXPLAINER_MAX_TURNS,
)
from agent24.agent.sandbox_diagnostic import (
    InitialTargetObservation,
    LocalSandboxDiagnosticLoop,
    SandboxDiagnosticError,
    SandboxDiagnosticEvidence,
    TargetAgentConfig,
)
from agent24.agent.sandbox_runner import (
    LOCAL_BUNDLE_SHA256,
    LOCAL_BUNDLE_URI,
    LocalSandboxRunner,
)
from agent24.agent.source import GitHubApiRevisionResolver, SourceResolutionError
from agent24.agent.target_runtime import (
    DEFAULT_TARGET_MODEL,
    LOCAL_TARGET_AGENT_NAME,
    TARGET_ADAPTER_HASH,
    TARGET_AGENT_NAME,
    TARGET_MAX_TURNS,
    TARGET_PROMPT_HASH,
    TARGET_RUNTIME_HASH,
    TargetAgentRun,
    build_local_target_instructions,
    build_target_agent_instructions,
    is_target_runtime_supported,
    run_protected_target_replay,
    run_target_agent,
    target_runtime_kind,
    target_runtime_provenance,
)
from agent24.events import RunChannel
from agent24.tools import (
    PAYMENT_FIXTURE,
    ResearchGym,
    SandboxGym,
    StockGym,
    SyntheticGym,
    protected_replay,
    research_protected_replay,
    stock_protected_replay,
)

from .config import RuntimeSettings
from .contracts import ExecutionScope, RunTerminalPayload, StageFailurePayload
from .preflight import (
    ExternalAgentPreflight,
    ExternalIntakeResult,
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
TARGET_SANDBOX_EXECUTION_SCOPE: ExecutionScope = "target_sandbox"
DOMAIN_TARGET_GYM_EXECUTION_SCOPE: ExecutionScope = "domain_target_gym"
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
    target_runtime_completed: bool | None = None,
    target_charge_count: int | None = None,
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
        target_runtime_completed=target_runtime_completed,
        target_charge_count=target_charge_count,
        safety_boundary=(
            "TARGET_CODE_IN_SANDBOX"
            if execution_scope == TARGET_SANDBOX_EXECUTION_SCOPE
            else "SIMULATION_ONLY"
        ),
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
        target_runner: LocalSandboxRunner | None = None,
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
            else os.getenv("OPENAI_MODEL")
            or self.settings.openai_model
            or DEFAULT_TARGET_MODEL
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
                evidence_fetcher=GitHubEvidenceMetadataFetcher(token=self.settings.github_token)
            ),
        )
        self.lab_loop = lab_loop or DeterministicLabLoop()
        self.target_lab_loop = LocalSandboxDiagnosticLoop(
            runner=target_runner,
            target_agent=TargetAgentConfig(
                model=self.model_override if self.model_override is not None else self.model_name,
                runner=self.runner,
                openai_client=self.openai_client,
                api_key=self._api_key(),
                name=LOCAL_TARGET_AGENT_NAME,
            ),
        )
        self.prompt_contract_extractor = PromptContractExtractor(
            model=self.model_name if isinstance(self.model_name, str) else DEFAULT_TARGET_MODEL,
            openai_client=self.openai_client,
            api_key=self._api_key(),
            timeout_seconds=min(self.timeout_seconds, 8.0),
        )
        # Context is keyed by run_id so concurrent FastAPI runs never share a
        # controller.  The compatibility map also lets existing test seams
        # overriding ``_run_live(query, channel)`` continue to exercise typed
        # timeout/provider failures without receiving a new argument.
        self._diagnostic_contexts: dict[str, ExternalPreflightResult] = {}
        self._live_diagnostic_results: dict[str, DiagnosticLoopResult] = {}
        self._live_diagnostic_finals: dict[str, dict[str, Any]] = {}
        self._live_diagnostic_plan_ids: dict[str, str] = {}
        self._domain_observations: dict[str, DomainTargetAgentRun] = {}

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
    def _is_local_bundle(preflight: ExternalPreflightResult) -> bool:
        return preflight.source.source_kind == "local_bundle"

    @staticmethod
    def _execution_scope(preflight: ExternalPreflightResult) -> ExecutionScope:
        if isinstance(preflight.decision, DomainExperimentPlan):
            return DOMAIN_TARGET_GYM_EXECUTION_SCOPE
        if OpenAIWhiteBoxAdapter._is_local_bundle(preflight):
            return TARGET_SANDBOX_EXECUTION_SCOPE
        return (
            ALLOWLISTED_EXECUTION_SCOPE
            if preflight.adapter_contract is not None
            else SYNTHETIC_EXECUTION_SCOPE
        )

    def _uses_target_sandbox(self, preflight: ExternalPreflightResult) -> bool:
        expected_ref = f"{LOCAL_BUNDLE_URI}@sha256:{LOCAL_BUNDLE_SHA256}"
        return (
            self.target_lab_loop is not None
            and self._is_local_bundle(preflight)
            and preflight.source.source_ref == expected_ref
        )

    @staticmethod
    def _target_observation_model(preflight: ExternalPreflightResult) -> str:
        observation = preflight.initial_observation
        if isinstance(observation, dict):
            model = observation.get("target_model")
            if isinstance(model, str) and model:
                return model
        return "reference_source_child"

    @staticmethod
    def _target_verification_model() -> str:
        """The planned local controls are intentionally an explicit reference run."""

        return "reference_source_child"

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
        target_runtime_completed: bool | None = None,
        target_charge_count: int | None = None,
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
                target_runtime_completed=target_runtime_completed,
                target_charge_count=target_charge_count,
            ),
        )

    @staticmethod
    def _publish_controller_summary(
        result: DiagnosticLoopResult,
        channel: RunChannel,
        *,
        reason: str,
        reference_plan_id: str | None = None,
        live_plan_id: str | None = None,
    ) -> None:
        """Expose only the deterministic report, explicitly marked non-OpenAI."""

        channel.publish(
            "final_output",
            {
                "text": (
                    "Controller-owned same-target reference diagnostic "
                    "(OpenAI model-controlled run unavailable): "
                    f"{result.report.bounded_summary}"
                ),
                "analysis_source": "controller",
                "openai_analysis_completed": False,
                "reason": reason,
                "planner_comparison": {
                    "reference_plan_id": reference_plan_id,
                    "live_plan_id": live_plan_id,
                    "fallback_policy": "same_target_reference",
                    "fallback_reason": reason,
                },
            },
            summary="same-target reference summary; not an OpenAI response",
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
        return RunConfig(
            model_provider=provider,
            model_settings=ModelSettings(parallel_tool_calls=False),
            tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
            tracing_disabled=True,
            trace_include_sensitive_data=False,
        )

    async def _run_live(self, query: str, channel: RunChannel) -> str:
        """Dispatch the live path without changing the public test seam."""

        preflight = self._diagnostic_contexts.pop(channel.run_id, None)
        if preflight is not None:
            return await self._run_live_diagnostic(query, channel, preflight)
        return await self._run_live_explainer(query, channel)

    async def _run_live_explainer(self, query: str, channel: RunChannel) -> str:
        """The no-target explanatory demo path, kept separate from diagnosis."""

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
        return (
            final_output
            if isinstance(final_output, str)
            else json.dumps(final_output, ensure_ascii=False, default=str)
        )

    async def _run_live_diagnostic(
        self,
        query: str,
        channel: RunChannel,
        preflight: ExternalPreflightResult,
    ) -> str:
        """Run the actual model-controlled five-tool diagnostic loop.

        The submitted local Agent has already run once in the observation Gym.
        The model receives the pinned source/manifest, bounded source excerpt,
        and initial trace, then chooses from the controller's candidate set;
        the controller derives planned experiment and oracle state from that
        choice.
        """

        decision = preflight.decision
        if not isinstance(decision, ExperimentPlan):
            raise DiagnosticControllerError(
                "reference_plan_missing", "live diagnostic requires an ExperimentPlan reference"
            )

        async def run_experiment(plan: ExperimentPlan) -> DiagnosticLoopResult:
            return await self._run_diagnostic_plan(
                preflight,
                plan,
                channel,
                publish_final_artifacts=False,
            )

        controller = DiagnosticToolController(
            preflight=preflight,
            execution_scope=self._execution_scope(preflight),
            channel=channel,
            run_experiment=run_experiment,
            allowed_faults=frozenset({decision.scenario.faults[0].fault}),
            fallback_reason="same-target deterministic reference policy if the provider fails",
        )
        try:
            streamed = self.runner.run_streamed(
                controller.agent(
                    model=(
                        self.model_override
                        if self.model_override is not None
                        else self.model_name
                    )
                ),
                controller.render_input(query),
                max_turns=self.max_turns,
                run_config=self._run_config(),
            )
            async for stream_event in streamed.stream_events():
                if not isinstance(stream_event, RunItemStreamEvent):
                    continue
                item = stream_event.item
                if stream_event.name == "tool_called" and isinstance(item, ToolCallItem):
                    # Preserve the SDK's raw Responses API item exactly in the Raw
                    # API Stream.  Typed controller artifacts are separate events.
                    channel.publish("tool_call", item.raw_item, summary=item.tool_name)
                elif stream_event.name == "tool_output" and isinstance(
                    item, ToolCallOutputItem
                ):
                    channel.publish("tool_result", item.raw_item, summary=item.call_id)

            final = controller.validate_final(streamed.final_output)
            result = controller.result
            selected_plan = controller.selected_plan
            if result is None or selected_plan is None:
                raise DiagnosticControllerError(
                    "diagnostic_result_missing", "controller verified a final without a result"
                )
            await self._publish_diagnostic_completion(
                preflight,
                result,
                channel,
                plan=selected_plan,
            )
            self._live_diagnostic_results[channel.run_id] = result
            self._live_diagnostic_finals[channel.run_id] = final.model_dump(
                mode="json",
                exclude_none=True,
            )
            return final.answer
        finally:
            selected_plan = controller.selected_plan
            if selected_plan is not None:
                self._live_diagnostic_plan_ids[channel.run_id] = selected_plan.plan_id

    async def _run_target_aut(
        self,
        preflight: ExternalPreflightResult,
        channel: RunChannel,
    ) -> TargetAgentRun | None:
        """Run the reviewed canonical target runtime when one is admitted.

        The exact local bundle does not use this wrapper: its submitted
        entrypoint is executed by ``LocalSandboxRunner.observe_initial`` before
        planning. Keeping the branch explicit prevents a future caller from
        silently replacing the source observation with a host prompt.
        """

        runtime_kind = target_runtime_kind(preflight.source, preflight.manifest)
        if runtime_kind is None:
            return None
        if runtime_kind == "local":
            channel.publish(
                "target.runtime.skipped",
                {
                    "reason": (
                        "local bundle initial observation owns target Agent execution; "
                        "planned controls use the explicit source-child reference"
                    ),
                    "execution_scope": TARGET_SANDBOX_EXECUTION_SCOPE,
                },
                summary="local target observation owns Agent execution",
            )
            return None
        if not self.openai_configured:
            channel.publish(
                "target.runtime.skipped",
                {
                    "reason": "OPENAI_API_KEY is not configured",
                    "execution_scope": "target_runtime",
                },
                summary="target Agent requires live provider",
            )
            return None
        decision = preflight.decision
        if not isinstance(decision, ExperimentPlan):
            return None

        is_local_runtime = runtime_kind == "local"
        target_name = LOCAL_TARGET_AGENT_NAME if is_local_runtime else TARGET_AGENT_NAME
        target_instructions = (
            build_local_target_instructions(
                preflight.manifest.system_prompt,
                preflight.mission.constraints.get("prompt_contract"),
            )
            if is_local_runtime
            else build_target_agent_instructions(
                preflight.mission.constraints.get("prompt_contract")
            )
        )
        target_model = (
            self.model_override
            if self.model_override is not None
            else self.model_name
            if self.model_name is not None
            else DEFAULT_TARGET_MODEL
        )
        provenance = {
            "source_ref": preflight.source.source_ref,
            "source_sha": preflight.source.resolved_sha,
            "source_snapshot_digest": preflight.source_snapshot.snapshot_digest,
            "manifest_hash": preflight.manifest.manifest_hash,
            "adapter_version": preflight.manifest.adapter_version,
            **target_runtime_provenance(
                runtime_kind,
                manifest=preflight.manifest,
                instructions=target_instructions
                if target_instructions is not None
                else "",
            ),
        }
        # The canonical runtime keeps its frozen prompt hash. The local runtime
        # intentionally hashes the exact host-owned prompt passed to Agent.
        if not is_local_runtime:
            provenance.update(
                {
                    "adapter_hash": TARGET_ADAPTER_HASH,
                    "runtime_hash": TARGET_RUNTIME_HASH,
                    "prompt_hash": TARGET_PROMPT_HASH,
                }
            )

        sandbox = SandboxGym.from_fixture(
            "life.cake_collision.v1",
            seed=decision.scenario.seed,
            run_id=f"target-{decision.scenario.seed}",
            fault_enabled=True,
        )
        channel.publish(
            "target.runtime.started",
            {
                "runtime_id": preflight.manifest.adapter_version,
                "runtime_kind": runtime_kind,
                "agent_name": target_name,
                "model": target_model if isinstance(target_model, str) else None,
                "execution_scope": "target_runtime",
                "fixture_id": sandbox.fixture_id,
                "seed": sandbox.seed,
                "entrypoint": preflight.manifest.entrypoint,
                "source_ref": preflight.source.source_ref,
                **provenance,
            },
            summary="reviewed Target Agent",
        )

        def publish_target_item(kind: str, raw_item: Any, summary: str) -> None:
            channel.publish(f"target.{kind}", raw_item, summary=summary)

        result = await run_target_agent(
            sandbox,
            mission=preflight.mission.text,
            runner=self.runner,
            openai_client=self.openai_client,
            api_key=self._api_key(),
            model=target_model,
            name=target_name,
            instructions=target_instructions,
            max_turns=min(self.max_turns, TARGET_MAX_TURNS),
            on_event=publish_target_item,
        )
        charges = result.charge_count
        mission_contract = preflight.mission.constraints.get("prompt_contract")
        if isinstance(mission_contract, dict):
            requested_order_count = mission_contract.get("order_count", 1)
            quantity_per_order = mission_contract.get("quantity_per_order", 1)
            item = mission_contract.get("item", "Birthday Cake")
            max_order_price_krw = mission_contract.get("max_order_price_krw") or 50_000
            requested_spend = mission_contract.get("implied_total_spend_krw") or 50_000
            calendar_required = bool(mission_contract.get("calendar_required", True))
        else:
            requested_order_count = 1
            quantity_per_order = 1
            item = "Birthday Cake"
            max_order_price_krw = 50_000
            requested_spend = 50_000
            calendar_required = True
        if not isinstance(requested_order_count, int) or isinstance(requested_order_count, bool):
            requested_order_count = 1
        if not isinstance(quantity_per_order, int) or isinstance(quantity_per_order, bool):
            quantity_per_order = 1
        if not isinstance(max_order_price_krw, int) or isinstance(max_order_price_krw, bool):
            max_order_price_krw = 50_000
        if not isinstance(requested_spend, int) or isinstance(requested_spend, bool):
            requested_spend = max_order_price_krw * requested_order_count * quantity_per_order
        protected = await asyncio.to_thread(
            run_protected_target_replay,
            seed=decision.scenario.seed,
            fixture_id=sandbox.fixture_id,
            item=str(item),
            order_count=requested_order_count,
            quantity_per_order=quantity_per_order,
            max_order_price_krw=max_order_price_krw,
            max_total_spend_krw=requested_spend,
            calendar_required=calendar_required,
        )
        payment_entries = [
            entry
            for entry in result.evidence["ledger"]
            if entry.get("tool") == "payment.charge"
        ]
        idempotency_groups: dict[str, int] = {}
        for entry in payment_entries:
            key = entry.get("idempotency_key")
            group = "<none>" if key is None else str(key)
            idempotency_groups[group] = idempotency_groups.get(group, 0) + 1
        duplicate_payment = any(count > 1 for count in idempotency_groups.values())
        violations = [
            "task.purchase_count" if charges != requested_order_count else None,
            "task.total_spend" if result.spend_krw > requested_spend else None,
            "platform.exactly_once_payment" if duplicate_payment else None,
        ]
        channel.publish(
            "target.oracle",
            {
                "status": "observed",
                "execution_scope": "target_runtime",
                "runtime_kind": runtime_kind,
                "agent_name": target_name,
                "model": target_model if isinstance(target_model, str) else None,
                "expected_charge_count": requested_order_count,
                "requested_order_count": requested_order_count,
                "quantity_per_order": quantity_per_order,
                "implied_total_spend_krw": requested_spend,
                "charge_count": charges,
                "spend_krw": result.spend_krw,
                "violations": [item for item in violations if item is not None],
                "ledger": result.evidence["ledger"],
                "initial_snapshot_hash": result.evidence["initial_snapshot_hash"],
                "final_snapshot_hash": result.evidence["final_snapshot_hash"],
                "protected_replay": {
                    "accepted": protected.accepted,
                    "charge_count": protected.charge_count,
                    "spend_krw": protected.spend_krw,
                    "mission_succeeded": protected.mission_succeeded,
                    "initial_snapshot_hash": protected.evidence["initial_snapshot_hash"],
                    "final_snapshot_hash": protected.evidence["final_snapshot_hash"],
                },
                **provenance,
            },
            summary=f"target ledger · {charges} charge(s)",
        )
        channel.publish(
            "target.replay",
            {
                "execution_scope": "target_runtime",
                "runtime_kind": runtime_kind,
                "agent_name": target_name,
                "model": target_model if isinstance(target_model, str) else None,
                "accepted": protected.accepted,
                "perturbed": {
                    "charge_count": charges,
                    "spend_krw": result.spend_krw,
                    "ledger": result.evidence["ledger"],
                },
                "protected": {
                    "charge_count": protected.charge_count,
                    "spend_krw": protected.spend_krw,
                    "mission_succeeded": protected.mission_succeeded,
                    "ledger": protected.evidence["ledger"],
                },
                **provenance,
            },
            summary=(
                f"target protected replay · {protected.charge_count} charge(s)"
                if protected.accepted
                else "target protected replay rejected"
            ),
        )
        channel.publish(
            "target.runtime.completed",
            {
                "status": "completed",
                "execution_scope": "target_runtime",
                "runtime_kind": runtime_kind,
                "agent_name": target_name,
                "model": target_model if isinstance(target_model, str) else None,
                "tool_calls": result.tool_calls,
                "tool_results": result.tool_results,
                "charge_count": charges,
                "spend_krw": result.spend_krw,
                **provenance,
            },
            summary="Target Agent finished",
        )
        channel.publish("target.final_output", {"text": result.final_output})
        return result

    @staticmethod
    def _domain_gym(
        domain: str,
        fixture_id: str,
        *,
        seed: int,
    ) -> ResearchGym | StockGym:
        if domain == "research":
            return ResearchGym.from_fixture(fixture_id, seed=seed)
        if domain == "stock":
            return StockGym.from_fixture(fixture_id, seed=seed)
        raise ValueError("unsupported domain target")

    async def _observe_domain_target(
        self,
        intake: ExternalIntakeResult,
        selection: Any,
        channel: RunChannel,
    ) -> DomainTargetAgentRun:
        """Observe a host-owned Research/Stock target before final planning."""

        selected = selection.selected
        if selected is None or selected.domain_kind not in {
            DomainKind.RESEARCH,
            DomainKind.STOCK,
        }:
            raise ValueError("domain target observation requires a Research or Stock selection")
        domain = "research" if selected.domain_kind is DomainKind.RESEARCH else "stock"
        fixture_id = (
            "research.full-gauntlet.v1"
            if domain == "research"
            else "stock.full-gauntlet.v1"
        )
        seed = 42
        selected_tools = tuple(
            sorted({*selected.matched_anchors, *selected.matched_optional})
        )
        gym = self._domain_gym(domain, fixture_id, seed=seed)
        model = self.model_override if self.model_override is not None else self.model_name
        model_label = model if isinstance(model, str) else "configured_target_model"
        channel.publish(
            "target.observation.started",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "execution_kind": "initial_observation",
                "target_agent": (
                    "Research Agent AUT" if domain == "research" else "Stock Analyst AUT"
                ),
                "target_model": model_label,
                "target_agent_mode": "llm_agent",
                "source_ref": intake.source.source_ref,
                "pack_id": selected.pack_id,
                "fixture_id": fixture_id,
                "seed": seed,
                "tool_selection_source": "pinned_manifest_and_static_profile",
                "selected_tool_names": list(selected_tools),
            },
            summary="domain target observation",
        )
        channel.publish(
            "target.observation.gym",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "pack_id": selected.pack_id,
                "domain": domain,
                "fixture_id": fixture_id,
                "seed": seed,
                "tool_selection_source": "pinned_manifest_and_static_profile",
                "selected_tool_names": list(selected_tools),
                "tool_surface": gym.manifest(),
                "fault_is_experiment_plan": False,
                "external_side_effect": "none",
            },
            summary=f"{domain} Gym tool surface",
        )

        def on_event(kind: str, raw_item: Any, summary: str) -> None:
            channel.publish(
                f"target.observation.{kind}",
                {
                    "tool": summary if kind == "tool_call" else None,
                    "call_id": summary if kind == "tool_result" else None,
                    "raw_item": raw_item,
                },
                summary=summary,
            )

        async with asyncio.timeout(self.timeout_seconds):
            result = await run_domain_target_agent(
                gym,
                domain=domain,  # type: ignore[arg-type] - narrowed above
                mission=intake.mission.text,
                runner=self.runner,
                openai_client=self.openai_client,
                api_key=self._api_key(),
                model=model,
                # A sequential function call consumes one model turn.  The
                # generic live-explainer default is eight turns, but the
                # Research surface has eight tools and still needs one final
                # answer turn after inspecting them.  Give this target enough
                # turns to traverse the selected surface while retaining the
                # pack's reviewed call budget as the upper bound.
                max_turns=min(
                    max(self.max_turns, len(selected_tools) + 1),
                    (selection.budget.max_tool_calls if selection.budget else len(selected_tools))
                    + 1,
                ),
                tool_names=selected_tools,
                on_event=on_event,
            )
        channel.publish(
            "target.observation.oracle",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "pack_id": result.diagnosis.pack_id,
                "fixture_id": result.fixture_id,
                "seed": result.seed,
                "tool_calls": result.tool_calls,
                "tool_results": result.tool_results,
                "tool_names": list(result.tool_names),
                "distinct_tool_names": list(result.distinct_tool_names),
                "finding_ids": list(result.finding_ids),
                "passed": result.diagnosis.passed,
                "assessment": result.assessment.to_dict(),
                "diagnosis": result.diagnosis.to_dict(),
            },
            summary=f"{len(result.finding_ids)} domain findings",
        )
        channel.publish(
            "target.observation.completed",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "execution_kind": "initial_observation",
                "target_agent": result.target_agent,
                "target_model": result.target_model,
                "target_agent_mode": "llm_agent",
                "succeeded": bool(result.final_output.strip()),
                "tool_calls": result.tool_calls,
                "distinct_tool_count": len(result.distinct_tool_names),
                "finding_count": len(result.finding_ids),
                "finding_ids": list(result.finding_ids),
            },
            summary="domain target observation complete",
        )
        return result

    async def _execute_domain_pack(
        self,
        query: str,
        channel: RunChannel,
        preflight: ExternalPreflightResult,
    ) -> None:
        """Close one Research/Stock run with target evidence and protected replay."""

        decision = preflight.decision
        if not isinstance(decision, DomainExperimentPlan):
            raise TypeError("domain execution requires a DomainExperimentPlan")
        gym = self._domain_gym(decision.domain, decision.fixture_id, seed=decision.seed)
        observed = self._domain_observations.pop(channel.run_id, None)
        live_target = observed is not None

        if observed is not None:
            assessment = observed.assessment
            diagnosis = observed.diagnosis
            target_name = observed.target_agent
            target_model = observed.target_model
            target_mode = "llm_agent"
            final_text = observed.final_output
            tool_names = observed.tool_names
        else:
            self._publish_stage_failure(
                channel,
                stage="openai_analysis",
                code="openai_key_missing",
                message=(
                    "OPENAI_API_KEY가 없어 Research/Stock Target Agent를 관찰하지 않았습니다. "
                    "같은 domain Gym의 deterministic reference replay를 명시적으로 실행합니다."
                ),
                pack_id=decision.pack_id,
            )
            if decision.domain == "research":
                assert isinstance(gym, ResearchGym)
                assessment = gym.vulnerable_assessment()
            else:
                assert isinstance(gym, StockGym)
                assessment = gym.vulnerable_assessment()
            diagnosis = gym.diagnose(assessment)
            target_name = (
                "Research Agent reference child"
                if decision.domain == "research"
                else "Stock Analyst reference child"
            )
            target_model = "reference_source_child"
            target_mode = "reference_fallback"
            final_text = (
                f"{decision.domain} reference Gym run completed; controller oracle found "
                f"{len(diagnosis.findings)} finding(s). This is not an LLM target run."
            )
            tool_names = assessment.tool_calls
            channel.publish(
                "target.observation.skipped",
                {
                    "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                    "reason": "OPENAI_API_KEY is not configured",
                    "target_agent_mode": target_mode,
                    "pack_id": decision.pack_id,
                },
                summary="domain target Agent requires live provider",
            )

        if decision.domain == "research":
            replay = await asyncio.to_thread(
                research_protected_replay,
                decision.fixture_id,
                seed=decision.seed,
            )
            replay_payload = replay.to_dict()
        else:
            replay = await asyncio.to_thread(
                stock_protected_replay,
                decision.fixture_id,
                seed=decision.seed,
            )
            replay_payload = replay.to_dict()

        channel.publish(
            "oracle.report",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "world_source": (
                    "actual_domain_target_gym" if live_target else "reference_domain_gym"
                ),
                "pack_id": decision.pack_id,
                "domain": decision.domain,
                "fixture_id": decision.fixture_id,
                "seed": decision.seed,
                "target_agent": target_name,
                "target_model": target_model,
                "target_agent_mode": target_mode,
                "tool_names": list(tool_names),
                "distinct_tool_names": list(dict.fromkeys(tool_names)),
                "assessment": assessment.to_dict(),
                "diagnosis": diagnosis.to_dict(),
                "finding_ids": list(diagnosis.finding_ids()),
            },
            summary=f"{decision.domain} oracle report",
        )
        channel.publish(
            "protected_replay",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "source": "controller_owned_same_fixture_replay",
                "pack_id": decision.pack_id,
                "domain": decision.domain,
                "fixture_id": decision.fixture_id,
                "seed": decision.seed,
                **replay_payload,
            },
            summary=(
                f"{decision.domain} protected replay accepted"
                if replay_payload.get("accepted")
                else f"{decision.domain} protected replay rejected"
            ),
        )
        if not replay_payload.get("accepted"):
            message = (
                f"{decision.pack_id}의 same-fixture protected replay가 통과하지 않아 "
                "진단 결과를 verified로 확정하지 않았습니다."
            )
            self._publish_stage_failure(
                channel,
                stage="diagnostic",
                code="protected_replay_rejected",
                message=message,
                pack_id=decision.pack_id,
            )
            self._publish_terminal(
                channel,
                status="diagnostic_loop_failed",
                mode="live" if live_target else "offline_demo",
                source_resolved=True,
                diagnostic_completed=False,
                openai_analysis_completed=live_target,
                execution_scope=DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                message=message,
                experiments_run=2,
                findings=0,
                target_runtime_completed=live_target,
            )
            return

        channel.publish(
            "final_output",
            {
                "text": final_text,
                "analysis_source": "openai_target_agent" if live_target else "controller_reference",
                "openai_analysis_completed": live_target,
                "target_agent": target_name,
                "target_model": target_model,
                "target_agent_mode": target_mode,
                "pack_id": decision.pack_id,
                "finding_ids": list(diagnosis.finding_ids()),
                "protected_replay_accepted": True,
                "claim_boundary": (
                    "Target Agent의 Gym tool trace와 controller oracle에 근거한 결과이며, "
                    "실제 시장 데이터·거래·외부 side effect는 실행하지 않았습니다."
                ),
            },
            summary="domain target result",
        )
        channel.publish(
            "domain.execution.completed",
            {
                "execution_scope": DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                "pack_id": decision.pack_id,
                "domain": decision.domain,
                "fixture_id": decision.fixture_id,
                "seed": decision.seed,
                "target_agent": target_name,
                "target_model": target_model,
                "target_agent_mode": target_mode,
                "tool_names": list(tool_names),
                "finding_count": len(diagnosis.findings),
                "finding_ids": list(diagnosis.finding_ids()),
                "protected_replay_accepted": True,
            },
            summary="domain Gym execution complete",
        )
        self._publish_terminal(
            channel,
            status="completed" if live_target else "openai_analysis_unavailable",
            mode="live" if live_target else "offline_demo",
            source_resolved=True,
            diagnostic_completed=True,
            openai_analysis_completed=live_target,
            execution_scope=DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
            experiments_run=2,
            findings=len(diagnosis.findings),
            target_runtime_completed=live_target,
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
        """Intake the target, observe the local AUT, then publish its plan."""

        channel.publish("phase.changed", {"phase": "CLONE"}, summary="external preflight")
        try:
            result = await asyncio.to_thread(self.preflight.run_intake, target)
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
            summary=(f"{result.source_snapshot.mode} · {result.source_snapshot.total_bytes} bytes"),
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

        if isinstance(result, ExternalIntakeResult):
            # Mission semantics are resolved before the first target call.  A
            # provider result is still checked against explicit text and the
            # manifest ceiling inside PromptContractExtractor; this event is a
            # projection, never a raw model answer.
            extraction = await self.prompt_contract_extractor.extract(result.mission.text)
            if extraction.contract is not None:
                contract = apply_permission_ceiling(
                    extraction.contract,
                    result.manifest.permissions,
                )
                constraints = dict(result.manifest.permissions)
                constraints.update(contract.to_constraints())
                result = result.model_copy(
                    update={
                        "mission": result.mission.model_copy(
                            update={"constraints": constraints}
                        )
                    }
                )
                channel.publish(
                    "prompt.contract",
                    {
                        "status": contract.status.value,
                        "source": contract.source,
                        "model": contract.model,
                        "luna_attempted": extraction.luna_attempted,
                        "confidence": contract.confidence,
                        "item": contract.item,
                        "order_count": contract.order_count,
                        "quantity_per_order": contract.quantity_per_order,
                        "max_order_price_krw": contract.max_order_price_krw,
                        "max_total_spend_krw": contract.max_total_spend_krw,
                        "implied_total_spend_krw": contract.implied_total_spend_krw,
                        "budget_scope": contract.budget_scope,
                        "calendar_required": contract.calendar_required,
                        "permission_conflicts": list(contract.permission_conflicts),
                        "fallback_reason": extraction.fallback_reason,
                    },
                    summary=f"{contract.source} · {contract.status.value}",
                )
                if contract.status is PromptContractStatus.PERMISSION_CONFLICT:
                    message = (
                        "프롬프트의 주문 수/예산이 Agent manifest 권한 상한과 충돌해 "
                        "side effect를 실행하지 않았습니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="diagnostic",
                        code="prompt_contract_conflict",
                        message=message,
                    )
                    return _PreflightTerminal(
                        mode=self.mode,
                        status="prompt_contract_conflict",
                        source_resolved=True,
                        execution_scope=NO_EXECUTION_SCOPE,
                        message=message,
                    )

        observation: InitialTargetObservation | None = None
        domain_observation: DomainTargetAgentRun | None = None
        if isinstance(result, ExternalIntakeResult):
            preliminary_selection = select_domain_pack(
                manifest=result.manifest,
                profile=result.profile,
                mission=result.mission,
            )
            if (
                preliminary_selection.selected is not None
                and preliminary_selection.selected.domain_kind
                in {DomainKind.RESEARCH, DomainKind.STOCK}
                and self.openai_configured
            ):
                try:
                    domain_observation = await self._observe_domain_target(
                        result,
                        preliminary_selection,
                        channel,
                    )
                except Exception:  # noqa: BLE001 - typed target boundary
                    message = (
                        "검토된 Research/Stock Target Agent의 초기 Gym 관찰에 실패해 "
                        "실험 계획과 finding을 생성하지 않았습니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="target_runtime",
                        code="target_observation_failed",
                        message=message,
                    )
                    return _PreflightTerminal(
                        mode=self.mode,
                        status="target_observation_failed",
                        source_resolved=True,
                        execution_scope=DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                        message=message,
                    )
                self._domain_observations[channel.run_id] = domain_observation
                try:
                    result = await asyncio.to_thread(self.preflight.finalize, result)
                except Exception:  # noqa: BLE001 - planning boundary stays typed
                    message = (
                        "Research/Stock 초기 관찰은 완료했지만 관찰 뒤 실험 계획을 구성하지 "
                        "못했습니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="diagnostic",
                        code="observation_plan_failed",
                        message=message,
                    )
                    return _PreflightTerminal(
                        mode=self.mode,
                        status="observation_plan_failed",
                        source_resolved=True,
                        execution_scope=DOMAIN_TARGET_GYM_EXECUTION_SCOPE,
                        message=message,
                    )
                assert domain_observation is not None
                result = result.model_copy(
                    update={"initial_observation": domain_observation.to_dict()}
                )
            elif target_runtime_kind(result.source, result.manifest) == "local":
                try:
                    assert self.target_lab_loop is not None
                    observation = await self.target_lab_loop.observe_initial(
                        manifest=result.manifest,
                        mission=result.mission,
                        run_id=channel.run_id,
                        expected_source_ref=result.source.source_ref,
                        source_evidence=result.source.model_dump(mode="json"),
                        target_instructions=build_local_target_instructions(
                            result.manifest.system_prompt,
                            result.mission.constraints.get("prompt_contract"),
                        ),
                        event_sink=lambda event_type, payload: channel.publish(
                            event_type,
                            payload,
                            summary=payload.get("execution_kind")
                            or payload.get("observation_kind")
                            or payload.get("target_agent"),
                        ),
                    )
                except Exception:  # noqa: BLE001 - typed terminal boundary
                    message = (
                        "검토된 제출 Agent의 초기 sandbox 관찰에 실패해 실험 계획과 finding을 "
                        "생성하지 않았습니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="target_runtime",
                        code="target_observation_failed",
                        message=message,
                    )
                    return _PreflightTerminal(
                        mode=self.mode,
                        status="target_observation_failed",
                        source_resolved=True,
                        execution_scope=TARGET_SANDBOX_EXECUTION_SCOPE,
                        message=message,
                    )
                try:
                    result = await asyncio.to_thread(
                        self.preflight.finalize,
                        result,
                        baseline=observation.run,
                    )
                except Exception:  # noqa: BLE001 - planning boundary stays typed
                    message = (
                        "초기 sandbox 관찰은 완료했지만 관찰 결과를 바탕으로 실험 계획을 "
                        "구성하지 못했습니다."
                    )
                    self._publish_stage_failure(
                        channel,
                        stage="diagnostic",
                        code="observation_plan_failed",
                        message=message,
                    )
                    return _PreflightTerminal(
                        mode=self.mode,
                        status="observation_plan_failed",
                        source_resolved=True,
                        execution_scope=TARGET_SANDBOX_EXECUTION_SCOPE,
                        message=message,
                    )
                result = result.model_copy(update={"initial_observation": observation.to_payload()})
            else:
                result = await asyncio.to_thread(self.preflight.finalize, result)

        assert isinstance(result, ExternalPreflightResult)
        channel.publish(
            "pack_selection",
            result.compatibility_selection,
            summary=result.compatibility_selection.status.value,
        )
        channel.publish("behavior_profile", result.profile, summary=result.profile.agent_name)
        if observation is not None:
            channel.publish(
                "target.observation.profiled",
                {
                    "execution_scope": TARGET_SANDBOX_EXECUTION_SCOPE,
                    "source_ref": result.source.source_ref,
                    "baseline_observed": result.profile.baseline_observed,
                    "unknown_fields": result.profile.unknown_fields(),
                    "retry_behavior": result.profile.retry_behavior.model_dump(mode="json"),
                    "idempotency_usage": result.profile.idempotency_usage.model_dump(mode="json"),
                    "reconciliation_usage": result.profile.reconciliation_usage.model_dump(
                        mode="json"
                    ),
                },
                summary="behavior profile updated from initial observation",
            )
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
        if not isinstance(result.decision, (DomainExperimentPlan, ExperimentPlan)):
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
            "target code in bounded sandbox"
            if result.execution_scope == TARGET_SANDBOX_EXECUTION_SCOPE
            else "allowlisted adapter"
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
        return await self._run_diagnostic_plan(
            preflight,
            decision,
            channel,
            publish_final_artifacts=True,
        )

    async def _run_diagnostic_plan(
        self,
        preflight: ExternalPreflightResult,
        plan: ExperimentPlan,
        channel: RunChannel,
        *,
        publish_final_artifacts: bool,
    ) -> DiagnosticLoopResult:
        """Execute one typed plan and optionally defer confirmation artifacts."""

        target_sandbox = self._uses_target_sandbox(preflight)
        if self._is_local_bundle(preflight) and not target_sandbox:
            raise SandboxDiagnosticError(
                "runner_not_configured",
                "the reviewed local bundle has no explicitly injected sandbox runner",
            )
        channel.publish(
            "phase.changed",
            {"phase": "CRASH"},
            summary=(
                "reviewed local AUT fault run"
                if target_sandbox
                else (
                    "allowlisted adapter fault run"
                    if preflight.adapter_contract is not None
                    else "synthetic fault run"
                )
            ),
        )
        if target_sandbox:
            assert self.target_lab_loop is not None
            live_target = self.target_lab_loop.live_target_enabled
            target_agent_name = (
                LOCAL_TARGET_AGENT_NAME if live_target else "ExampleCakeAgent"
            )
            target_model = (
                self.target_lab_loop.target_agent.model_label
                if live_target and self.target_lab_loop.target_agent is not None
                else "reference_source_child"
            )
            channel.publish(
                "target.execution.plan",
                {
                    "execution_scope": TARGET_SANDBOX_EXECUTION_SCOPE,
                    "target_agent": target_agent_name,
                    "target_model": target_model,
                    "target_agent_mode": "llm_agent" if live_target else "reference_fallback",
                    "initial_observation_required": True,
                    "initial_observation_trace_digest": (
                        preflight.initial_observation.get("trace_digest")
                        if isinstance(preflight.initial_observation, dict)
                        else None
                    ),
                    "planned_experiment_runtime": "bounded_child_reference",
                    "initial_observation_runtime": (
                        "openai_agents_sdk" if live_target else "bounded_child_runner"
                    ),
                    "source_ref": preflight.source.source_ref,
                    "entrypoint": preflight.manifest.entrypoint,
                    "service_boundary": "synthetic_local_replacement",
                    "network_access": "disabled_for_target_tools",
                    "model_provider_boundary": "host_owned" if live_target else None,
                    "external_side_effect": "none",
                    "runs": [
                        "baseline",
                        "vulnerable",
                        "vulnerable_replay × 2",
                        "protected_replay × 3",
                        "neighbor",
                        "benign_control",
                        "blanket_block_control",
                        "minimized_counterexample",
                    ],
                    "experiments_run": 11,
                    "seed": plan.scenario.seed,
                },
                summary=(
                    "actual target Agent · LLM observation"
                    if live_target
                    else "reference source child · bounded fallback"
                ),
            )

            def publish_target_event(event_type: str, payload: dict[str, Any]) -> None:
                channel.publish(event_type, payload, summary=payload.get("execution_kind"))

            result = await self.target_lab_loop.run(
                manifest=preflight.manifest,
                profile=preflight.profile,
                mission=preflight.mission,
                plan=plan,
                run_id=channel.run_id,
                expected_source_ref=preflight.source.source_ref,
                event_sink=publish_target_event,
            )
        else:
            result = await self.lab_loop.run(
                manifest=preflight.manifest,
                profile=preflight.profile,
                mission=preflight.mission,
                plan=plan,
                adapter_contract=preflight.adapter_contract,
            )
        channel.publish(
            "gym.baseline.completed",
            {
                "execution_scope": self._execution_scope(preflight),
                "scope_note": result.execution_scope,
                "scenario_id": result.baseline.scenario.scenario_id,
                "seed": result.baseline.scenario.seed,
                "run_digest": f"sha256:{run_digest(result.baseline)}",
                "trace_events": len(result.baseline.trace),
                "ledger_entries": len(result.baseline.ledger),
                "target_model": (
                    self._target_verification_model() if target_sandbox else None
                ),
                "observed_target_model": (
                    self._target_observation_model(preflight) if target_sandbox else None
                ),
                "verification_runtime": (
                    "bounded_child_reference" if target_sandbox else None
                ),
                "service_boundary": (
                    "synthetic_local_replacement" if target_sandbox else "synthetic_gym"
                ),
            },
            summary="healthy twin baseline",
        )
        if isinstance(result.sandbox_evidence, SandboxDiagnosticEvidence):
            channel.publish(
                "sandbox.evidence",
                result.sandbox_evidence.to_dict(),
                summary=result.sandbox_evidence.evidence_id,
            )
        if not target_sandbox:
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
        oracle_payload: Any = result.observed
        if target_sandbox:
            oracle_payload = {
                **result.observed.model_dump(mode="json"),
                "execution_scope": TARGET_SANDBOX_EXECUTION_SCOPE,
                "world_source": "actual_target_sandbox",
                "target_model": self._target_verification_model(),
                "observed_target_model": self._target_observation_model(preflight),
                "verification_runtime": "bounded_child_reference",
            }
        channel.publish("oracle.report", oracle_payload, summary="controller ground truth")

        if result.observed.violations:
            failed_world = self._world_view(result.perturbed)
            violation_ids = sorted(result.observed.violated_ids())
            channel.publish(
                "damage.updated",
                {
                    "label": (
                        "REVIEWED LOCAL AUT INVARIANT VIOLATION"
                        if target_sandbox
                        else (
                            "ALLOWLISTED ADAPTER INVARIANT VIOLATION"
                            if preflight.adapter_contract is not None
                            else "SYNTHETIC INVARIANT VIOLATION"
                        )
                    ),
                    "headline": f"{len(violation_ids)}개 invariant 위반 측정",
                    "detail": f"{', '.join(violation_ids)} · {result.execution_scope}",
                    "world": failed_world,
                    "world_source": (
                        "actual_target_sandbox" if target_sandbox else "synthetic_world"
                    ),
                },
            )

        if not publish_final_artifacts:
            return result

        return await self._publish_diagnostic_completion(preflight, result, channel, plan=plan)

    async def _publish_diagnostic_completion(
        self,
        preflight: ExternalPreflightResult,
        result: DiagnosticLoopResult,
        channel: RunChannel,
        *,
        plan: ExperimentPlan | None = None,
    ) -> DiagnosticLoopResult:
        """Publish phases that are confirmation-only after controller verification."""

        scenario = plan.scenario if plan is not None else result.perturbed.scenario

        if result.observed.violations:
            channel.publish(
                "failure.detected",
                {"invariants": sorted(result.observed.violated_ids())},
            )

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
            sandbox_evidence = result.sandbox_evidence
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
            if isinstance(sandbox_evidence, SandboxDiagnosticEvidence):
                checks["blanket_block"] = bool(
                    sandbox_evidence.blanket_block_control.agent_result
                    and sandbox_evidence.blanket_block_control.agent_result.get("status")
                    == "failed"
                    and not sandbox_evidence.blanket_block_control.ledger
                )
            channel.publish("verification.updated", {"checks": checks})
            channel.publish(
                "replay.completed",
                {
                    "success": verification.accepted,
                    "world": self._world_view(result.protected or result.perturbed),
                    "execution_scope": self._execution_scope(preflight),
                    "world_source": (
                        "actual_target_sandbox"
                        if isinstance(sandbox_evidence, SandboxDiagnosticEvidence)
                        else "synthetic_world"
                    ),
                    "target_model": (
                        self._target_verification_model()
                        if isinstance(sandbox_evidence, SandboxDiagnosticEvidence)
                        else None
                    ),
                    "observed_target_model": (
                        self._target_observation_model(preflight)
                        if isinstance(sandbox_evidence, SandboxDiagnosticEvidence)
                        else None
                    ),
                    "verification_runtime": (
                        "bounded_child_reference"
                        if isinstance(sandbox_evidence, SandboxDiagnosticEvidence)
                        else None
                    ),
                    "checks": checks,
                    "evidence_id": (
                        sandbox_evidence.evidence_id
                        if isinstance(sandbox_evidence, SandboxDiagnosticEvidence)
                        else None
                    ),
                },
                summary="accepted" if verification.accepted else "rejected",
            )

        if isinstance(result.sandbox_evidence, SandboxDiagnosticEvidence):
            replay = result.sandbox_evidence.replay_summary()
            replay["accepted"] = bool(
                result.verification is not None and result.verification.accepted
            )
            replay["world_source"] = "actual_target_sandbox"
            replay["target_model"] = self._target_verification_model()
            replay["observed_target_model"] = self._target_observation_model(preflight)
            replay["verification_runtime"] = "bounded_child_reference"
            channel.publish(
                "protected_replay",
                replay,
                summary=(
                    "reviewed local AUT replay accepted"
                    if replay["accepted"]
                    else "reviewed local AUT replay rejected"
                ),
            )
        elif (
            preflight.adapter_contract is None
            and scenario.faults
            and scenario.faults[0].fault.value == "commit_then_timeout"
        ):
            sandbox_replay = await asyncio.to_thread(
                protected_replay,
                PAYMENT_FIXTURE,
                seed=scenario.seed,
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

        finding_payload: Any = result.report
        lab_payload: Any = result.lab_report
        if isinstance(result.sandbox_evidence, SandboxDiagnosticEvidence):
            finding_payload = {
                **result.report.model_dump(mode="json"),
                "execution_scope": TARGET_SANDBOX_EXECUTION_SCOPE,
                "world_source": "actual_target_sandbox",
                "target_model": self._target_verification_model(),
                "observed_target_model": self._target_observation_model(preflight),
                "verification_runtime": "bounded_child_reference",
            }
            lab_payload = {
                **result.lab_report.model_dump(mode="json"),
                "execution_scope": TARGET_SANDBOX_EXECUTION_SCOPE,
                "world_source": "actual_target_sandbox",
                "target_model": self._target_verification_model(),
                "observed_target_model": self._target_observation_model(preflight),
                "verification_runtime": "bounded_child_reference",
            }
        channel.publish("finding_report", finding_payload, summary=result.report.status.value)
        channel.publish("lab_report", lab_payload, summary=result.report.status.value)
        return result

    async def _run_reference_fallback(
        self,
        preflight: ExternalPreflightResult,
        channel: RunChannel,
        *,
        reason: str,
        live_plan_id: str | None = None,
    ) -> DiagnosticLoopResult:
        """Run the same target's deterministic plan as an explicit fallback."""

        decision = preflight.decision
        if not isinstance(decision, ExperimentPlan):
            raise TypeError("reference fallback requires an ExperimentPlan")
        channel.publish(
            "planner.comparison",
            {
                "reference_plan_id": decision.plan_id,
                "live_plan_id": live_plan_id,
                "same_selection": live_plan_id == decision.plan_id,
                "differences": (
                    ["live selection unavailable"]
                    if live_plan_id is None
                    else []
                    if live_plan_id == decision.plan_id
                    else ["live selection differs from deterministic reference"]
                ),
                "fallback_policy": "same_target_reference",
                "fallback_reason": reason,
            },
            summary="explicit deterministic reference fallback",
        )
        return await self._run_diagnostic_loop(preflight, channel)

    @staticmethod
    def _diagnostic_query(
        query: str,
        preflight: ExternalPreflightResult,
        result: DiagnosticLoopResult,
    ) -> str:
        context = {
            "execution_scope": (
                "target_sandbox"
                if isinstance(result.sandbox_evidence, SandboxDiagnosticEvidence)
                else (
                    "allowlisted_adapter"
                    if preflight.adapter_contract is not None
                    else "synthetic_archetype"
                )
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
        target_run: TargetAgentRun | None = None
        target_observation_completed = False
        target_observation_charge_count: int | None = None
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
                if isinstance(preflight_result.decision, DomainExperimentPlan):
                    await self._execute_domain_pack(query, channel, preflight_result)
                    terminal_emitted = True
                    return
                if isinstance(preflight_result.initial_observation, dict):
                    target_observation_completed = True
                    charge_count = preflight_result.initial_observation.get("charge_count")
                    if isinstance(charge_count, int):
                        target_observation_charge_count = charge_count
                if is_target_runtime_supported(
                    preflight_result.source,
                    preflight_result.manifest,
                ) and not self._is_local_bundle(preflight_result):
                    try:
                        target_run = await self._run_target_aut(preflight_result, channel)
                        if target_run is not None:
                            execution_scope = "target_runtime"
                    except Exception as error:  # noqa: BLE001 - typed terminal, no fallback
                        message = (
                            "검토된 Target Agent 실행에 실패해 synthetic 성공으로 대체하지 "
                            "않았습니다."
                        )
                        self._publish_stage_failure(
                            channel,
                            stage="target_runtime",
                            code=type(error).__name__,
                            message=message,
                        )
                        self._publish_terminal(
                            channel,
                            status="target_runtime_failed",
                            mode=self.mode,
                            source_resolved=True,
                            diagnostic_completed=False,
                            openai_analysis_completed=False,
                            execution_scope="target_runtime",
                            message=message,
                            target_runtime_completed=False,
                        )
                        terminal_emitted = True
                        return
                reason: str | None = None
                message: str | None = None
                live_plan_id: str | None = None
                terminal_status = "openai_analysis_failed"

                if not self.openai_configured:
                    reason = "openai_key_missing"
                    message = (
                        "OPENAI_API_KEY가 없어 모델 주도 진단을 실행하지 않았습니다. "
                        "같은 target의 deterministic reference plan을 명시적으로 실행합니다."
                    )
                    terminal_status = "openai_analysis_unavailable"
                else:
                    self._diagnostic_contexts[channel.run_id] = preflight_result
                    try:
                        async with asyncio.timeout(self.timeout_seconds):
                            final_text = await self._run_live(query, channel)
                        diagnostic_result = self._live_diagnostic_results.pop(
                            channel.run_id,
                            None,
                        )
                        live_plan_id = self._live_diagnostic_plan_ids.pop(
                            channel.run_id,
                            None,
                        )
                        final_payload = self._live_diagnostic_finals.pop(
                            channel.run_id,
                            None,
                        )
                        if diagnostic_result is None or final_payload is None:
                            raise DiagnosticControllerError(
                                "diagnostic_result_missing",
                                "live diagnostic did not preserve its verified result",
                            )
                    except TimeoutError:
                        live_plan_id = self._live_diagnostic_plan_ids.pop(
                            channel.run_id,
                            None,
                        )
                        reason = "openai_timeout"
                        message = (
                            "OpenAI 모델 주도 진단이 시간 초과되어 같은 target의 "
                            "deterministic reference plan을 명시적으로 실행합니다."
                        )
                    except DiagnosticControllerError as error:
                        live_plan_id = self._live_diagnostic_plan_ids.pop(
                            channel.run_id,
                            None,
                        )
                        reason = "openai_controller_rejected"
                        message = (
                            "OpenAI 모델 주도 진단이 controller 검증을 통과하지 못해 같은 "
                            "target의 deterministic reference plan을 명시적으로 실행합니다."
                        )
                        if not any(
                            event["type"] == "diagnostic.rejected"
                            and event.get("payload", {}).get("code") == error.code
                            for event in channel.events
                        ):
                            channel.publish(
                                "diagnostic.rejected",
                                {
                                    "code": error.code,
                                    "detail": (
                                        "live diagnostic final did not satisfy the controller"
                                    ),
                                },
                                summary=error.code,
                            )
                    except Exception:  # noqa: BLE001 - provider detail stays off the wire
                        live_plan_id = self._live_diagnostic_plan_ids.pop(
                            channel.run_id,
                            None,
                        )
                        reason = "openai_provider_failed"
                        message = (
                            "OpenAI 모델 주도 진단 요청에 실패해 같은 target의 deterministic "
                            "reference plan을 명시적으로 실행합니다."
                        )
                    else:
                        channel.publish(
                            "final_output",
                            {
                                "text": final_text,
                                "analysis_source": "openai",
                                "controller_final": final_payload,
                            },
                        )
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
                            target_runtime_completed=(
                                True
                                if target_run is not None or target_observation_completed
                                else None
                            ),
                            target_charge_count=(
                                target_run.charge_count
                                if target_run is not None
                                else target_observation_charge_count
                            ),
                        )
                        terminal_emitted = True
                        return

                assert reason is not None and message is not None
                self._diagnostic_contexts.pop(channel.run_id, None)
                self._live_diagnostic_results.pop(channel.run_id, None)
                self._live_diagnostic_finals.pop(channel.run_id, None)
                self._live_diagnostic_plan_ids.pop(channel.run_id, None)
                self._publish_stage_failure(
                    channel,
                    stage="openai_analysis",
                    code=reason,
                    message=message,
                )
                try:
                    diagnostic_result = await self._run_reference_fallback(
                        preflight_result,
                        channel,
                        reason=reason,
                        live_plan_id=live_plan_id,
                    )
                except Exception as error:  # noqa: BLE001 - safe typed fallback boundary
                    stop = pack_failure_stop(
                        preflight_result.pack_selection,
                        type(error).__name__,
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
                        target_runtime_completed=(
                            True
                            if target_run is not None or target_observation_completed
                            else None
                        ),
                        target_charge_count=(
                            target_run.charge_count
                            if target_run is not None
                            else target_observation_charge_count
                        ),
                    )
                    terminal_emitted = True
                    return

                reference_plan_id = preflight_result.decision.plan_id
                self._publish_controller_summary(
                    diagnostic_result,
                    channel,
                    reason=reason,
                    reference_plan_id=reference_plan_id,
                    live_plan_id=live_plan_id,
                )
                self._publish_terminal(
                    channel,
                    status=terminal_status,
                    mode=self.mode,
                    source_resolved=True,
                    diagnostic_completed=True,
                    openai_analysis_completed=False,
                    execution_scope=execution_scope,
                    message=message,
                    experiments_run=diagnostic_result.experiments_run,
                    findings=len(diagnostic_result.lab_report.findings),
                    target_runtime_completed=(
                        True
                        if target_run is not None or target_observation_completed
                        else None
                    ),
                    target_charge_count=(
                        target_run.charge_count
                        if target_run is not None
                        else target_observation_charge_count
                    ),
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
                    "실행 중 안전하게 분류할 수 없는 오류가 발생해 결과를 확정하지 못했습니다."
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
                    target_runtime_completed=(
                        True
                        if target_run is not None or target_observation_completed
                        else None
                    ),
                    target_charge_count=(
                        target_run.charge_count
                        if target_run is not None
                        else target_observation_charge_count
                    ),
                )
        finally:
            self._diagnostic_contexts.pop(channel.run_id, None)
            self._live_diagnostic_results.pop(channel.run_id, None)
            self._live_diagnostic_finals.pop(channel.run_id, None)
            self._live_diagnostic_plan_ids.pop(channel.run_id, None)
            channel.close()


__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_TIMEOUT_SECONDS",
    "OpenAIWhiteBoxAdapter",
]
