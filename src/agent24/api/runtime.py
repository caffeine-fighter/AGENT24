"""OpenAI Agents SDK runtime and deterministic offline fallback."""

from __future__ import annotations

import asyncio
import json
import os
import uuid
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
from agent24.agent.prompts import (
    DIAGNOSTIC_CONTEXT_LABEL,
    LIVE_EXPLAINER_INSTRUCTIONS,
    LIVE_EXPLAINER_MAX_TURNS,
)
from agent24.agent.source import GitHubApiRevisionResolver, SourceResolutionError
from agent24.events import RunChannel
from agent24.tools import PAYMENT_FIXTURE, SyntheticGym, protected_replay

from .config import RuntimeSettings
from .preflight import (
    ExternalAgentPreflight,
    ExternalPreflightResult,
    ExternalTarget,
    GitHubContentsManifestFetcher,
    ManifestFetchError,
)

DEFAULT_MAX_TURNS = LIVE_EXPLAINER_MAX_TURNS
DEFAULT_TIMEOUT_SECONDS = 45.0


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
        query: str,
        target: ExternalTarget,
        channel: RunChannel,
    ) -> ExternalPreflightResult | None:
        """Publish pinned planning artifacts, or finish with an honest fallback."""

        channel.publish("phase.changed", {"phase": "CLONE"}, summary="external preflight")
        try:
            result = await asyncio.to_thread(self.preflight.run, target)
        except (SourceResolutionError, ManifestLoadError, ManifestFetchError):
            channel.publish(
                "run_failed",
                {"status": "offline_demo", "code": "source_preflight_failed"},
                summary="External Agent source or manifest unavailable",
            )
            await self._run_offline(
                query,
                channel,
                reason="External Agent source or manifest unavailable",
            )
            channel.publish(
                "run_completed",
                {"status": "offline_demo", "mode": "offline_demo"},
            )
            return None

        channel.publish("source_descriptor", result.source, summary=result.source.source_ref)
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
            channel.publish(
                "run_completed",
                {
                    "status": "unsupported",
                    "mode": self.mode,
                    "message": result.decision.detail,
                },
                summary=result.decision.reason,
            )
            return None
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
        steps = [
            {
                "kind": "observed",
                "text": (
                    f"Life-v0 synthetic run에서 {len(result.observed.violations)}개 "
                    "invariant 위반을 controller oracle이 측정"
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

        channel.publish("phase.changed", {"phase": "CRASH"}, summary="synthetic fault run")
        result = await self.lab_loop.run(
            manifest=preflight.manifest,
            profile=preflight.profile,
            mission=preflight.mission,
            plan=decision,
        )
        channel.publish(
            "gym.baseline.completed",
            {
                "execution_scope": "synthetic_archetype",
                "scope_note": SYNTHETIC_SCOPE,
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
                    "label": "SYNTHETIC INVARIANT VIOLATION",
                    "headline": f"{len(violation_ids)}개 invariant 위반 측정",
                    "detail": f"{', '.join(violation_ids)} · {SYNTHETIC_SCOPE}",
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

        if decision.scenario.faults and (
            decision.scenario.faults[0].fault.value == "commit_then_timeout"
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
            "execution_scope": "synthetic_archetype",
            "scope_note": SYNTHETIC_SCOPE,
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
            f"{query}\n\n{DIAGNOSTIC_CONTEXT_LABEL} (controller-owned JSON; synthetic scope):\n"
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

        try:
            channel.publish(
                "run_started",
                {
                    "mode": self.mode,
                    "input_received": True,
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
            preflight_result = None
            diagnostic_result = None
            if target is not None:
                preflight_result = await self._run_external_preflight(query, target, channel)
                if preflight_result is None:
                    return
                try:
                    diagnostic_result = await self._run_diagnostic_loop(
                        preflight_result, channel
                    )
                except Exception as error:  # noqa: BLE001 - keep a safe terminal state
                    # Name the pack that failed.  Re-routing to another pack
                    # here is exactly how one pack's failure would come out
                    # looking like a different pack's success.  The exception
                    # string stays out of the payload; only its class is safe.
                    stop = pack_failure_stop(
                        preflight_result.pack_selection, type(error).__name__
                    )
                    channel.publish(
                        "run_failed",
                        {
                            "status": "offline_demo",
                            "code": "diagnostic_loop_failed",
                            "pack_id": preflight_result.pack_selection.pack_id,
                            "message": stop.detail,
                        },
                        summary="Deterministic diagnostic loop failed",
                    )
                    await self._run_offline(
                        query,
                        channel,
                        reason="Deterministic diagnostic loop failed",
                    )
                    channel.publish(
                        "run_completed",
                        {"status": "offline_demo", "mode": "offline_demo"},
                    )
                    return
                query = self._diagnostic_query(query, preflight_result, diagnostic_result)
            if not self.openai_configured:
                await self._run_offline(query, channel, reason="OPENAI_API_KEY is not configured")
                channel.publish("run_completed", {"status": "offline_demo", "mode": "offline_demo"})
                return

            try:
                async with asyncio.timeout(self.timeout_seconds):
                    final_text = await self._run_live(query, channel)
            except TimeoutError:
                channel.publish(
                    "run_failed",
                    {"status": "offline_demo", "code": "timeout"},
                    summary="OpenAI run timed out",
                )
                await self._run_offline(query, channel, reason="OpenAI run timed out")
                channel.publish("run_completed", {"status": "offline_demo", "mode": "offline_demo"})
                return
            except Exception as error:  # noqa: BLE001 - convert upstream failures to a demo state
                # Do not include the exception string: provider errors can echo
                # request metadata, while the event contract must never expose a
                # credential or transport detail.
                channel.publish(
                    "run_failed",
                    {"status": "offline_demo", "code": type(error).__name__},
                    summary="OpenAI run failed",
                )
                await self._run_offline(query, channel, reason="OpenAI run failed")
                channel.publish("run_completed", {"status": "offline_demo", "mode": "offline_demo"})
                return

            channel.publish("final_output", {"text": final_text})
            channel.publish("run_completed", {"status": "completed", "mode": "live"})
        finally:
            channel.close()


__all__ = ["DEFAULT_MAX_TURNS", "DEFAULT_TIMEOUT_SECONDS", "OpenAIWhiteBoxAdapter"]
