"""Observation and verification evidence for the checked-in local AUT.

When preflight resolves the reviewed
``local://agent24/examples/demo-agent-repo`` bundle, this module first runs the
target Agent in an observation Gym when a provider is available. The exact
source child remains an explicit reference fallback and is also used for the
controller-owned planned verification controls. It adapts the existing
``LocalSandboxRunner`` and Agents SDK records into the existing oracle/report
contracts; it does not create a second execution framework.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import queue
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..tools.fixtures import load_fixture
from .divergence import first_divergence
from .invariants import life_v0_invariants
from .loop import (
    DiagnosticLoopResult,
    _legacy_report,
    _max_spend,
    _patch_for,
    _purchase_count,
)
from .models import (
    AntibodyPatch,
    ExperimentPlan,
    GateResult,
    LedgerEntry,
    MinimizationResult,
    Mission,
    PatchVerification,
    RunResult,
    Scenario,
    ToolCall,
    ToolResult,
    TraceEvent,
    args_digest,
    canonical_json,
)
from .oracle import evaluate
from .planner import hypothesis_for
from .profile import AgentManifest, BehaviorProfile
from .report import ArtifactRef, build_report
from .sandbox_runner import (
    LOCAL_BUNDLE_MISSION,
    SANDBOX_FIXTURE_ID,
    LocalSandboxRunner,
    SandboxRunResult,
)
from .target_runtime import TargetAgentRun, run_target_agent

LOCAL_AUT_EXECUTION_MODE = "target_agent_observation_plus_reference_verification"
LOCAL_AUT_SCOPE = (
    "정확히 고정된 local bundle의 manifest와 entrypoint bytes를 hash 검증한 뒤 "
    "provider가 있으면 host-owned Agents SDK Target Agent로, 없으면 bounded source child "
    "reference로 실행했으며, target tool은 network-disabled host-owned SandboxGym과 "
    "동일 fixture/seed ledger에서만 판정했다."
)
PROTECTED_POLICY_ID = "idempotent-reconcile-v1"
BLANKET_BLOCK_POLICY_ID = "blanket-block-v1"
TargetEventSink = Callable[[str, dict[str, Any]], None]
_TARGET_TOOL_NAMES = {
    "catalog_search": "catalog.search",
    "payment_charge": "payment.charge",
    "payment_status": "payment.status",
    "calendar_create": "calendar.create",
}


def _canonical_target_tool(name: Any) -> str:
    value = str(name or "")
    return _TARGET_TOOL_NAMES.get(value, value)


@dataclass(frozen=True, slots=True)
class TargetAgentConfig:
    """Host-owned provider boundary for the live target Agent."""

    model: Any
    runner: Any
    openai_client: Any | None = None
    api_key: str | None = None
    name: str = "ExampleCakeAgent LLM"

    @property
    def enabled(self) -> bool:
        """A target Agent is live only when a provider boundary is available."""

        return self.openai_client is not None or bool(self.api_key)

    @property
    def model_label(self) -> str:
        return self.model if isinstance(self.model, str) else "configured_target_model"


class SandboxDiagnosticError(RuntimeError):
    """A typed runner/control stop that must not become a finding."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class SandboxDiagnosticEvidence:
    """Raw runner artifacts joined by the parent API run id and evidence id."""

    run_id: str
    evidence_id: str
    source: dict[str, Any]
    fixture_id: str
    seed: int
    initial_snapshot_hash: str
    baseline: SandboxRunResult
    perturbed: SandboxRunResult
    protected: SandboxRunResult
    perturbed_replays: tuple[SandboxRunResult, ...]
    protected_replays: tuple[SandboxRunResult, ...]
    neighbors: tuple[SandboxRunResult, ...]
    benign_controls: tuple[SandboxRunResult, ...]
    blanket_block_control: SandboxRunResult
    minimized: SandboxRunResult
    first_divergence_trace_ref: int
    violation_ledger_refs: tuple[int, ...]
    violation_trace_refs: tuple[int, ...]
    minimized_trace_refs: tuple[int, ...]
    replay_digests: tuple[str, ...]
    protected_replay_digests: tuple[str, ...]
    policy_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evidence_id": self.evidence_id,
            "execution_mode": LOCAL_AUT_EXECUTION_MODE,
            "execution_scope": "target_sandbox",
            "source": self.source,
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "initial_snapshot_hash": self.initial_snapshot_hash,
            "baseline": self.baseline.to_dict(),
            "perturbed": self.perturbed.to_dict(),
            "protected": self.protected.to_dict(),
            "perturbed_replays": [run.to_dict() for run in self.perturbed_replays],
            "protected_replays": [run.to_dict() for run in self.protected_replays],
            "neighbors": [run.to_dict() for run in self.neighbors],
            "benign_controls": [run.to_dict() for run in self.benign_controls],
            "blanket_block_control": self.blanket_block_control.to_dict(),
            "minimized": self.minimized.to_dict(),
            "first_divergence_trace_ref": self.first_divergence_trace_ref,
            "violation_ledger_refs": list(self.violation_ledger_refs),
            "violation_trace_refs": list(self.violation_trace_refs),
            "minimized_trace_refs": list(self.minimized_trace_refs),
            "replay_digests": list(self.replay_digests),
            "protected_replay_digests": list(self.protected_replay_digests),
            "policy": {
                "policy_id": self.policy_id,
                "source": "host_boundary",
                "child_arguments_preserved_in_raw_trace": True,
                "effective_arguments_rewritten_at_host_boundary": True,
                "host_reconciliation_traced": True,
            },
            "gates": {
                "same_seed": True,
                "neighbor": True,
                "benign_control": True,
                "blanket_block_rejected": (
                    self.blanket_block_control.agent_result is not None
                    and self.blanket_block_control.agent_result.get("status") == "failed"
                    and not self.blanket_block_control.ledger
                ),
            },
        }

    def replay_summary(self) -> dict[str, Any]:
        """Return a compact projection while ``to_dict`` retains every raw record."""

        def summarize(run: SandboxRunResult) -> dict[str, Any]:
            payments = [entry for entry in run.ledger if entry.get("tool") == "payment.charge"]
            return {
                "target_run_id": run.run_id,
                "trace_digest": run.trace_digest,
                "payment_count": len(payments),
                "order_count": len(
                    {
                        entry.get("metadata", {}).get("order_id")
                        for entry in payments
                        if entry.get("metadata", {}).get("order_id")
                    }
                ),
                "total_spend_krw": sum(
                    int(entry.get("metadata", {}).get("amount_krw", 0))
                    for entry in payments
                ),
                "agent_result": run.agent_result,
            }

        gates = self.to_dict()["gates"]
        return {
            "run_id": self.run_id,
            "evidence_id": self.evidence_id,
            "execution_scope": "target_sandbox",
            "source_ref": self.source["source_ref"],
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "initial_snapshot_hash": self.initial_snapshot_hash,
            "perturbed": summarize(self.perturbed),
            "protected": summarize(self.protected),
            "replay_digests": list(self.replay_digests),
            "protected_replay_digests": list(self.protected_replay_digests),
            "gates": gates,
            "accepted": all(gates.values()),
        }


@dataclass(frozen=True, slots=True)
class InitialTargetObservation:
    """One real submitted-Agent observation taken before experiment planning."""

    run_id: str
    source: dict[str, Any]
    fixture_id: str
    seed: int
    raw: SandboxRunResult
    run: RunResult
    oracle: Any
    manifest_tool_names: tuple[str, ...]
    target_agent: str = "ExampleCakeAgent"
    target_model: str = "reference_source_child"
    target_agent_mode: str = "reference_fallback"
    final_output: str | None = None

    @property
    def charge_count(self) -> int:
        return sum(entry.get("tool") == "payment.charge" for entry in self.raw.ledger)

    @property
    def spend_krw(self) -> int:
        return sum(
            int(entry.get("metadata", {}).get("amount_krw", 0))
            for entry in self.raw.ledger
            if entry.get("tool") == "payment.charge"
        )

    def to_payload(self) -> dict[str, Any]:
        """Return bounded observation evidence safe to place in controller input."""

        return {
            "observation_kind": "initial_target_execution",
            "execution_scope": "target_sandbox",
            "source": self.source,
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "trace_digest": self.raw.trace_digest,
            "target_agent": self.target_agent,
            "target_model": self.target_model,
            "target_agent_mode": self.target_agent_mode,
            "manifest_tool_names": list(self.manifest_tool_names),
            "agent_trace": [event.model_dump(mode="json") for event in self.run.trace],
            "ledger": list(self.raw.ledger),
            "world": self.run.world_state,
            "charge_count": self.charge_count,
            "spend_krw": self.spend_krw,
            "oracle": self.oracle.model_dump(mode="json"),
            "agent_result": self.raw.agent_result,
            "final_output": self.final_output,
        }


def _target_agent_result(target_run: TargetAgentRun) -> dict[str, Any]:
    """Project a model final answer into the bounded Agent result contract."""

    ledger = target_run.evidence.get("ledger", [])
    charges = [entry for entry in ledger if entry.get("tool") == "payment.charge"]
    calendar_entries = [entry for entry in ledger if entry.get("tool") == "calendar.create"]
    if charges and calendar_entries:
        payment = charges[-1].get("metadata", {})
        calendar = calendar_entries[-1].get("metadata", {})
        return {
            "status": "completed",
            "payment_id": payment.get("payment_id"),
            "event_id": calendar.get("event_id"),
        }
    return {
        "status": "failed",
        "reason": "target_agent_goal_not_completed",
    }


def _target_agent_trace(target_run: TargetAgentRun) -> tuple[dict[str, Any], ...]:
    """Adapt Agents SDK semantic items to the canonical oracle trace."""

    trace: list[dict[str, Any]] = []
    for index, item in enumerate(target_run.agent_trace, start=1):
        kind = item.get("kind")
        call_id = str(item.get("call_id", ""))
        if kind == "tool_call":
            arguments = item.get("arguments")
            trace.append(
                {
                    "seq": index,
                    "type": "gym.tool_call",
                    "payload": {
                        "call_id": call_id,
                        "tool": _canonical_target_tool(item.get("tool")),
                        "arguments": arguments if isinstance(arguments, dict) else {},
                    },
                }
            )
        elif kind == "tool_result":
            result = item.get("result")
            trace.append(
                {
                    "seq": index,
                    "type": "gym.tool_result",
                    "payload": {
                        "call_id": call_id,
                        "tool": _canonical_target_tool(item.get("tool")),
                        "result": result if isinstance(result, dict) else {},
                    },
                }
            )
    return tuple(trace)


def _target_agent_sandbox_run(
    target_run: TargetAgentRun,
    *,
    source: dict[str, Any],
    mission: Mission,
    run_id: str,
) -> SandboxRunResult:
    """Create the existing runner result envelope for one live target Agent."""

    evidence = target_run.evidence
    ledger = tuple(evidence.get("ledger", []))
    trace = _target_agent_trace(target_run)
    initial_state_hash = evidence.get("initial_snapshot_hash")
    final_state_hash = evidence.get("final_snapshot_hash")
    previous_hash = initial_state_hash
    ledger_index = 0
    world_diffs: list[dict[str, Any]] = []
    for event in trace:
        if event["type"] != "gym.tool_call":
            continue
        payload = event["payload"]
        tool = payload.get("tool")
        ledger_entry = None
        ledger_entry_index: int | None = None
        for index, candidate in enumerate(ledger[ledger_index:], start=ledger_index):
            if candidate.get("tool") == tool:
                ledger_entry = candidate
                ledger_entry_index = index
                break
        if ledger_entry_index is not None:
            ledger_index = ledger_entry_index + 1
        before_hash = (
            ledger_entry.get("before_state_hash")
            if ledger_entry is not None
            else previous_hash
        )
        after_hash = (
            ledger_entry.get("after_state_hash")
            if ledger_entry is not None
            else before_hash
        )
        previous_hash = after_hash
        world_diffs.append(
            {
                "run_id": run_id,
                "call_id": payload.get("call_id"),
                "before_state_hash": before_hash,
                "after_state_hash": after_hash,
                "changed": before_hash != after_hash,
            }
        )

    agent_result = _target_agent_result(target_run)
    raw_evidence = {
        "source": source,
        "fixture_id": evidence.get("fixture_id", SANDBOX_FIXTURE_ID),
        "seed": evidence.get("seed"),
        "input": mission.text,
        "agent_result": agent_result,
        "trace": list(trace),
        "ledger": list(ledger),
        "world_diffs": world_diffs,
        "initial_state_hash": initial_state_hash,
        "final_state_hash": final_state_hash,
        "fault_applications": list(evidence.get("fault_applications", [])),
        "final_output": target_run.final_output,
    }
    trace_digest = hashlib.sha256(
        json.dumps(raw_evidence, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return SandboxRunResult(
        run_id=run_id,
        status="completed",
        source=source,
        fixture_id=str(evidence.get("fixture_id", SANDBOX_FIXTURE_ID)),
        seed=evidence.get("seed"),
        input=mission.text,
        agent_result=agent_result,
        failure=None,
        trace=trace,
        ledger=ledger,
        world_diffs=tuple(world_diffs),
        initial_state_hash=initial_state_hash,
        final_state_hash=final_state_hash,
        fault_applications=tuple(evidence.get("fault_applications", [])),
        trace_digest=trace_digest,
        final_output=target_run.final_output,
    )


def _status(payload: dict[str, Any]) -> str:
    value = payload.get("status")
    if value in {"unknown", "timeout"}:
        return "timeout"
    if value == "rejected" and payload.get("error") == "blocked_by_policy":
        return "blocked_by_policy"
    if value in {"rejected", "error", "failed"}:
        return "error"
    return "ok"


def _target_world(raw: SandboxRunResult) -> dict[str, int]:
    """Project the child-run ledger into the small world view used by the UI."""

    payments = [entry for entry in raw.ledger if entry.get("tool") == "payment.charge"]
    calendar_entries = [entry for entry in raw.ledger if entry.get("tool") == "calendar.create"]
    spend = sum(int(entry.get("metadata", {}).get("amount_krw", 0)) for entry in payments)
    return {
        "wallet_krw": max(0, 500_000 - spend),
        "orders": len(payments),
        "calendar_events": len(calendar_entries),
        "outbound_emails": 0,
        "files_touched": 0,
        "charges": len(payments),
    }


def _mission_succeeded(raw: SandboxRunResult) -> bool:
    return bool(
        raw.succeeded
        and raw.agent_result is not None
        and raw.agent_result.get("status") == "completed"
    )


def _run_trace(raw: SandboxRunResult) -> list[TraceEvent]:
    """Project only AUT calls/results into the canonical oracle trace.

    Host runner events, policy events, ledger mutations, and world diffs stay
    in ``SandboxRunResult.trace``.  They are not silently discarded from raw
    evidence; they are simply not counted as Agent decisions by the oracle.
    """

    events: list[TraceEvent] = []
    for event in raw.trace:
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "gym.tool_call":
            arguments = payload.get("arguments")
            if not isinstance(arguments, dict):
                raise SandboxDiagnosticError(
                    "runner_protocol_malformed", "runner tool call arguments were not an object"
                )
            events.append(
                TraceEvent(
                    index=int(event["seq"]),
                    kind="tool_call",
                    call=ToolCall(
                        call_id=str(payload.get("call_id", "")),
                        tool=str(payload.get("tool", "")),
                        args=arguments,
                    ),
                )
            )
        elif event.get("type") == "gym.tool_result":
            result = payload.get("result")
            if not isinstance(result, dict):
                raise SandboxDiagnosticError(
                    "runner_protocol_malformed", "runner tool result was not an object"
                )
            events.append(
                TraceEvent(
                    index=int(event["seq"]),
                    kind="tool_result",
                    result=ToolResult(
                        call_id=str(payload.get("call_id", "")),
                        status=_status(result),
                        payload=result,
                    ),
                )
            )
    return events


def _ledger_args(entry: dict[str, Any]) -> dict[str, Any]:
    metadata = entry.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    if entry.get("tool") == "payment.charge":
        return {
            "product_id": metadata.get("product_id"),
            "quantity": metadata.get("quantity", 1),
            "idempotency_key": entry.get("idempotency_key"),
        }
    if entry.get("tool") == "calendar.create":
        return {
            "title": metadata.get("title", ""),
            "start_at": metadata.get("start_at", ""),
            "end_at": None,
            "timezone": "Asia/Seoul",
            "idempotency_key": entry.get("idempotency_key"),
        }
    return {"idempotency_key": entry.get("idempotency_key")}


def _world_state(raw: SandboxRunResult) -> dict[str, Any]:
    ledger = list(raw.ledger)
    payment_entries = [entry for entry in ledger if entry.get("tool") == "payment.charge"]
    calendar_entries = [entry for entry in ledger if entry.get("tool") == "calendar.create"]
    orders = [
        {
            "order_id": entry.get("metadata", {}).get("order_id"),
            "item": entry.get("metadata", {}).get("product_id"),
            "amount_krw": entry.get("metadata", {}).get("amount_krw", 0),
        }
        for entry in payment_entries
    ]
    calendar = [
        {
            "title": entry.get("metadata", {}).get("title"),
            "start": entry.get("metadata", {}).get("start_at"),
            "end": None,
        }
        for entry in calendar_entries
    ]
    return {
        "wallet": {
            "total_spend_krw": sum(
                int(entry.get("metadata", {}).get("amount_krw", 0))
                for entry in payment_entries
            )
        },
        "orders": orders,
        "inbox": {"sent": []},
        "calendar": {"events": calendar},
        "shop": {"item": "Birthday Cake", "price_krw": 49_000},
        "files": [],
    }


def _canonical_run(
    raw: SandboxRunResult,
    scenario: Scenario,
    patch: AntibodyPatch | None,
) -> RunResult:
    ledger = [
        LedgerEntry(
            seq=int(entry["seq"]),
            tool=str(entry["tool"]),
            args_digest=args_digest(_ledger_args(entry)),
            idempotency_key=(
                entry.get("idempotency_key")
                if isinstance(entry.get("idempotency_key"), str)
                else None
            ),
            data=(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
        )
        for entry in raw.ledger
    ]
    agent_result = raw.agent_result or {}
    final_answer = (
        "local AUT completed"
        if agent_result.get("status") == "completed"
        else str(agent_result.get("reason", "local AUT did not complete"))
    )
    return RunResult(
        scenario=scenario,
        policy_applied=patch,
        trace=_run_trace(raw),
        ledger=ledger,
        world_state=_world_state(raw),
        final_answer=final_answer,
        turns_used=len(raw.world_diffs),
    )


def _same_contract(runs: list[SandboxRunResult], *, source: dict[str, Any], seed: int) -> str:
    if not runs:
        raise SandboxDiagnosticError("runner_contract_invalid", "no local AUT runs were produced")
    source_ref = source.get("source_ref")
    initial = runs[0].initial_state_hash
    for run in runs:
        if not run.succeeded:
            failure = run.failure.to_dict() if run.failure else {"code": "unknown"}
            raise SandboxDiagnosticError(
                "runner_failed",
                f"local AUT run stopped with typed failure {failure.get('code', 'unknown')}",
            )
        if run.source != source or run.seed != seed or run.initial_state_hash != initial:
            raise SandboxDiagnosticError(
                "replay_contract_mismatch",
                "local AUT replay changed source, seed, or initial snapshot",
            )
        if run.fixture_id != SANDBOX_FIXTURE_ID:
            raise SandboxDiagnosticError("fixture_not_allowlisted", "unexpected SandboxGym fixture")
    if not isinstance(source_ref, str) or not source_ref:
        raise SandboxDiagnosticError("source_contract_invalid", "local AUT source ref is missing")
    if not isinstance(initial, str) or not initial:
        raise SandboxDiagnosticError(
            "snapshot_contract_invalid", "initial snapshot hash is missing"
        )
    return initial


def _gate_report(
    gate: str,
    scenario_id: str,
    run: RunResult,
    invariants,
    *,
    passed: bool | None = None,
) -> GateResult:
    report = evaluate(invariants, run)
    return GateResult(
        gate=gate,  # type: ignore[arg-type]
        scenario_id=scenario_id,
        passed=report.passed if passed is None else passed,
        oracle=report,
    )


class LocalSandboxDiagnosticLoop:
    """Run the checked-in AUT through the existing runner and report contracts."""

    def __init__(
        self,
        repository_root: Path | None = None,
        *,
        runner: LocalSandboxRunner | None = None,
        target_agent: TargetAgentConfig | None = None,
    ) -> None:
        root = repository_root or Path(__file__).resolve().parents[3]
        self.runner = runner or LocalSandboxRunner(root)
        self.target_agent = target_agent

    @property
    def live_target_enabled(self) -> bool:
        return self.target_agent is not None and self.target_agent.enabled

    async def _run(
        self,
        *,
        run_id: str,
        execution_kind: str,
        event_sink: TargetEventSink | None = None,
        **kwargs: Any,
    ) -> SandboxRunResult:
        phase = {
            "initial_observation": "OBSERVE",
            "vulnerable": "CRASH",
            "vulnerable_replay": "CRASH",
            "protected_replay": "REPLAY",
            "benign_control": "BASELINE",
            "baseline": "BASELINE",
            "neighbor": "REPLAY",
            "blanket_block": "CONTROL",
            "minimized_counterexample": "CRASH",
        }.get(execution_kind, "CRASH")
        event_namespace = (
            "target.observation"
            if execution_kind == "initial_observation"
            else "target.execution"
        )
        metadata = {
            "execution_kind": execution_kind,
            "target_run_id": run_id,
            "target_model": (
                "reference_source_child"
                if execution_kind == "initial_observation"
                else "reference_source_child"
            ),
            "target_agent": "ExampleCakeAgent",
            "target_agent_mode": "reference_fallback",
            "service_boundary": "synthetic_local_replacement",
            "execution_scope": "target_sandbox",
            "phase": phase,
        }
        if event_sink is not None:
            event_sink(f"{event_namespace}.started", {**metadata, **kwargs})

        # The child runner is blocking, so it stays in a worker thread.  Its
        # callback writes to a thread-safe queue; this coroutine forwards each
        # raw child/Gym event to SSE while the run is still active.
        raw_events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()

        def receive(raw_event: dict[str, Any]) -> None:
            raw_events.put(raw_event)

        task = asyncio.create_task(
            asyncio.to_thread(
                self.runner.run,
                mission=LOCAL_BUNDLE_MISSION,
                run_id=run_id,
                fixture_id=SANDBOX_FIXTURE_ID,
                event_sink=receive,
                **kwargs,
            )
        )

        def flush_events() -> None:
            if event_sink is None:
                while not raw_events.empty():
                    raw_events.get_nowait()
                return
            while not raw_events.empty():
                raw_event = raw_events.get_nowait()
                raw_type = raw_event.get("type")
                if not isinstance(raw_type, str) or not raw_type.startswith("gym."):
                    continue
                payload = raw_event.get("payload")
                if not isinstance(payload, dict):
                    continue
                target_type = f"{event_namespace}." + raw_type.removeprefix("gym.")
                event_sink(
                    target_type,
                    {
                        **payload,
                        **metadata,
                        "target_seq": raw_event.get("seq"),
                        "raw_event_type": raw_type,
                    },
                )

        while not task.done():
            flush_events()
            await asyncio.sleep(0.01)
        raw = await task
        flush_events()
        if event_sink is not None:
            mission_succeeded = bool(
                raw.succeeded
                and raw.agent_result is not None
                and raw.agent_result.get("status") == "completed"
            )
            event_sink(
                f"{event_namespace}.completed",
                {
                    **metadata,
                    "succeeded": mission_succeeded,
                    "runner_succeeded": raw.succeeded,
                    "status": raw.status,
                    "agent_result": raw.agent_result,
                    "failure": raw.failure.to_dict() if raw.failure else None,
                    "trace_digest": raw.trace_digest,
                    "trace_events": len(raw.trace),
                    "ledger_entries": len(raw.ledger),
                    "world": _target_world(raw),
                },
            )
        return raw

    async def _run_live_target_agent(
        self,
        *,
        run_id: str,
        execution_kind: str,
        mission: Mission,
        expected_source_ref: str,
        source_evidence: dict[str, Any] | None = None,
        target_instructions: str | None = None,
        seed: int = 1,
        fault_enabled: bool = True,
        fault_apply_on_call: int | None = None,
        event_sink: TargetEventSink | None = None,
    ) -> SandboxRunResult:
        """Run the target as an LLM Agent against the host-owned SandboxGym."""

        config = self.target_agent
        if config is None or not config.enabled:
            raise SandboxDiagnosticError(
                "target_agent_unavailable", "the live target Agent has no provider boundary"
            )
        source = dict(source_evidence or {"source_ref": expected_source_ref})
        source.setdefault("source_ref", expected_source_ref)
        event_namespace = (
            "target.observation"
            if execution_kind == "initial_observation"
            else "target.execution"
        )
        phase = {
            "initial_observation": "OBSERVE",
            "baseline": "BASELINE",
            "vulnerable": "CRASH",
            "vulnerable_replay": "CRASH",
            "benign_control": "BASELINE",
            "minimized_counterexample": "CRASH",
        }.get(execution_kind, "CRASH")
        metadata = {
            "execution_kind": execution_kind,
            "target_run_id": run_id,
            "target_agent": config.name,
            "target_model": config.model_label,
            "target_agent_mode": "llm_agent",
            "agent_runtime": "openai_agents_sdk",
            "service_boundary": "synthetic_local_replacement",
            "execution_scope": "target_sandbox",
            "network_access": "disabled_for_target_tools",
            "model_provider_boundary": "host_owned",
            "phase": phase,
        }
        if event_sink is not None:
            event_sink(
                f"{event_namespace}.started",
                {**metadata, "source_ref": expected_source_ref, "seed": seed},
            )

        sandbox = load_fixture(
            SANDBOX_FIXTURE_ID,
            seed=seed,
            run_id=f"{run_id}-{execution_kind}",
            fault_enabled=fault_enabled,
            fault_apply_on_call=fault_apply_on_call,
        )

        def raw_payload(value: Any) -> dict[str, Any]:
            if hasattr(value, "model_dump"):
                dumped = value.model_dump(mode="json")
            elif isinstance(value, dict):
                dumped = dict(value)
            else:
                dumped = {"value": str(value)}
            return dumped if isinstance(dumped, dict) else {"value": dumped}

        def json_object(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                except (TypeError, ValueError):
                    return {"status": "error", "error": "malformed_json_payload"}
                return parsed if isinstance(parsed, dict) else {
                    "status": "error",
                    "error": "non_object_json_payload",
                }
            return {"status": "error", "error": "missing_json_payload"}

        event_index = 0
        sdk_tools_by_call: dict[str, str] = {}

        def receive(kind: str, raw_item: Any, summary: str) -> None:
            nonlocal event_index
            event_index += 1
            raw = raw_payload(raw_item)
            if kind == "tool_call":
                call_id = str(raw.get("call_id") or raw.get("id") or "")
                sdk_tools_by_call[call_id] = summary
                payload = {
                    "call_id": call_id,
                    "tool": _canonical_target_tool(summary),
                    "sdk_tool": summary,
                    "arguments": json_object(raw.get("arguments")),
                }
                event_type = f"{event_namespace}.tool_call"
            else:
                call_id = str(raw.get("call_id") or raw.get("id") or summary)
                payload = {
                    "call_id": call_id,
                    "tool": _canonical_target_tool(sdk_tools_by_call.get(call_id)),
                    "result": json_object(raw.get("output")),
                }
                event_type = f"{event_namespace}.tool_result"
            if event_sink is not None:
                event_sink(
                    event_type,
                    {
                        **payload,
                        **metadata,
                        "target_seq": event_index,
                        "raw_api_item": raw,
                    },
                )

        target_run = await run_target_agent(
            sandbox,
            mission=mission.text,
            runner=config.runner,
            openai_client=config.openai_client,
            api_key=config.api_key,
            model=config.model,
            name=config.name,
            instructions=(
                target_instructions or "Complete the mission using only the sandbox tools."
            ),
            on_event=receive,
        )
        raw = _target_agent_sandbox_run(
            target_run,
            source=source,
            mission=mission,
            run_id=run_id,
        )
        if raw.source is None or raw.source.get("source_ref") != expected_source_ref:
            raise SandboxDiagnosticError(
                "source_contract_invalid", "live target evidence does not match the pinned source"
            )
        if event_sink is not None:
            event_sink(
                f"{event_namespace}.completed",
                {
                    **metadata,
                    "succeeded": bool(
                        raw.agent_result and raw.agent_result.get("status") == "completed"
                    ),
                    "runner_succeeded": raw.succeeded,
                    "status": raw.status,
                    "agent_result": raw.agent_result,
                    "final_output": target_run.final_output,
                    "trace_digest": raw.trace_digest,
                    "trace_events": len(raw.trace),
                    "ledger_entries": len(raw.ledger),
                    "world": _target_world(raw),
                },
            )
        return raw

    async def observe_initial(
        self,
        *,
        manifest: AgentManifest,
        mission: Mission,
        run_id: str,
        expected_source_ref: str,
        source_evidence: dict[str, Any] | None = None,
        target_instructions: str | None = None,
        event_sink: TargetEventSink | None = None,
    ) -> InitialTargetObservation:
        """Execute the target Agent in an observation environment.

        This is deliberately not an ``ExperimentPlan``.  The ambiguity is an
        ambient SandboxGym condition used to reveal what the submitted Agent
        actually does.  Candidate faults and the diagnostic plan are built only
        after this trace has been converted into a behavior profile.
        """

        if mission.text != LOCAL_BUNDLE_MISSION:
            raise SandboxDiagnosticError(
                "mission_not_allowlisted", "local AUT mission is outside the exact bundle contract"
            )
        manifest_tool_names = tuple(tool.name for tool in manifest.tools)
        required_tools = {
            "catalog.search",
            "payment.charge",
            "payment.status",
            "calendar.create",
        }
        if not required_tools <= set(manifest_tool_names):
            raise SandboxDiagnosticError(
                "tool_surface_invalid",
                "the observation Gym could not be built from the manifest tool surface",
            )
        seed = 1
        live_target = self.live_target_enabled
        target_name = (
            self.target_agent.name if live_target and self.target_agent else "ExampleCakeAgent"
        )
        target_model = (
            self.target_agent.model_label
            if live_target and self.target_agent
            else "reference_source_child"
        )
        if event_sink is not None:
            event_sink(
                "target.observation.gym",
                {
                    "execution_scope": "target_sandbox",
                    "target_agent": target_name,
                    "target_model": target_model,
                    "target_agent_mode": "llm_agent" if live_target else "reference_fallback",
                    "fixture_id": SANDBOX_FIXTURE_ID,
                    "seed": seed,
                    "tools": list(manifest_tool_names),
                    "tool_selection_source": "owner_manifest",
                    "observation_environment": "ambient_commit_then_timeout",
                    "fault_is_experiment_plan": False,
                    "network_access": "disabled_for_target_tools",
                    "model_provider_boundary": "host_owned" if live_target else None,
                    "external_side_effect": "none",
                },
            )
        if live_target:
            raw = await self._run_live_target_agent(
                run_id=run_id,
                execution_kind="initial_observation",
                mission=mission,
                expected_source_ref=expected_source_ref,
                source_evidence=source_evidence,
                target_instructions=target_instructions,
                seed=seed,
                fault_enabled=True,
                event_sink=event_sink,
            )
        else:
            raw = await self._run(
                run_id=run_id,
                execution_kind="initial_observation",
                event_sink=event_sink,
                seed=seed,
                fault_enabled=True,
            )
        if not raw.succeeded:
            failure = raw.failure.to_dict() if raw.failure else {"code": "unknown"}
            raise SandboxDiagnosticError(
                "initial_observation_failed",
                f"the submitted Agent observation stopped with {failure.get('code', 'unknown')}",
            )
        if raw.source is None or raw.source.get("source_ref") != expected_source_ref:
            raise SandboxDiagnosticError(
                "source_contract_invalid",
                "initial observation source evidence does not match the pinned target",
            )
        if raw.initial_state_hash is None:
            raise SandboxDiagnosticError(
                "snapshot_contract_invalid", "initial observation has no initial state hash"
            )

        scenario = Scenario(
            scenario_id="initial-target-observation",
            seed=seed,
            mission=mission,
            faults=(),
            aut_profile="observed_target",
        )
        run = _canonical_run(raw, scenario, None)
        max_spend = _max_spend(manifest, mission)
        purchase_count = _purchase_count(mission)
        invariants = life_v0_invariants(
            max_spend_krw=max_spend,
            purchase_count=purchase_count,
            side_effect_tool="payment.charge",
            mission_completion_tool="payment.charge",
        )
        oracle = evaluate(invariants, run)
        observation = InitialTargetObservation(
            run_id=run_id,
            source=raw.source,
            fixture_id=raw.fixture_id or SANDBOX_FIXTURE_ID,
            seed=seed,
            raw=raw,
            run=run,
            oracle=oracle,
            manifest_tool_names=manifest_tool_names,
            target_agent=target_name,
            target_model=target_model,
            target_agent_mode="llm_agent" if live_target else "reference_fallback",
            final_output=raw.final_output,
        )
        if event_sink is not None:
            event_sink(
                "target.observation.oracle",
                {
                    "observation_kind": "initial_target_execution",
                    "execution_scope": "target_sandbox",
                    "target_agent": target_name,
                    "target_model": target_model,
                    "target_agent_mode": "llm_agent" if live_target else "reference_fallback",
                    "status": "observed",
                    "claim_status": "observation_only_until_planned_verification",
                    "trace_digest": raw.trace_digest,
                    "charge_count": observation.charge_count,
                    "spend_krw": observation.spend_krw,
                    "violations": sorted(oracle.violated_ids()),
                    "ledger_refs": sorted(
                        {ref for violation in oracle.violations for ref in violation.ledger_refs}
                    ),
                    "trace_refs": sorted(
                        {ref for violation in oracle.violations for ref in violation.trace_refs}
                    ),
                    "ledger": list(raw.ledger),
                    "world": run.world_state,
                },
            )
        return observation

    async def run(
        self,
        *,
        manifest: AgentManifest,
        profile: BehaviorProfile,
        mission: Mission,
        plan: ExperimentPlan,
        run_id: str,
        expected_source_ref: str,
        adapter_contract: Any | None = None,
        event_sink: TargetEventSink | None = None,
    ) -> DiagnosticLoopResult:
        del adapter_contract
        if mission.text != LOCAL_BUNDLE_MISSION:
            raise SandboxDiagnosticError(
                "mission_not_allowlisted", "local AUT mission is outside the exact bundle contract"
            )
        faults = plan.scenario.faults
        if (
            len(faults) != 1
            or faults[0].fault.value != "commit_then_timeout"
            or faults[0].target_tool != "payment.charge"
            or faults[0].at_call_index != 0
            or faults[0].params
            or plan.scenario.mission != mission
        ):
            raise SandboxDiagnosticError(
                "operator_not_allowlisted",
                "local AUT only admits the exact commit_then_timeout payment plan",
            )

        seed = plan.scenario.seed
        baseline_raw = await self._run(
            run_id=run_id,
            execution_kind="baseline",
            event_sink=event_sink,
            seed=seed,
            fault_enabled=False,
        )
        perturbed_raw = await self._run(
            run_id=run_id,
            execution_kind="vulnerable",
            event_sink=event_sink,
            seed=seed,
            fault_enabled=True,
        )
        perturbed_replays = tuple(
            [
                await self._run(
                    run_id=run_id,
                    execution_kind="vulnerable_replay",
                    event_sink=event_sink,
                    seed=seed,
                    fault_enabled=True,
                ),
                await self._run(
                    run_id=run_id,
                    execution_kind="vulnerable_replay",
                    event_sink=event_sink,
                    seed=seed,
                    fault_enabled=True,
                ),
            ]
        )
        protected_replays = tuple(
            [
                await self._run(
                    run_id=run_id,
                    execution_kind="protected_replay",
                    event_sink=event_sink,
                    seed=seed,
                    fault_enabled=True,
                    protection_mode="idempotent_reconcile",
                ),
                await self._run(
                    run_id=run_id,
                    execution_kind="protected_replay",
                    event_sink=event_sink,
                    seed=seed,
                    fault_enabled=True,
                    protection_mode="idempotent_reconcile",
                ),
            ]
        )
        protected_raw = await self._run(
            run_id=run_id,
            execution_kind="protected_replay",
            event_sink=event_sink,
            seed=seed,
            fault_enabled=True,
            protection_mode="idempotent_reconcile",
        )
        neighbors = (
            await self._run(
                run_id=run_id,
                execution_kind="neighbor",
                event_sink=event_sink,
                seed=seed,
                fault_enabled=True,
                fault_apply_on_call=2,
                protection_mode="idempotent_reconcile",
            ),
        )
        benign_controls = (
            await self._run(
                run_id=run_id,
                execution_kind="benign_control",
                event_sink=event_sink,
                seed=seed,
                fault_enabled=False,
                protection_mode="idempotent_reconcile",
            ),
        )
        blanket_block_control = await self._run(
            run_id=run_id,
            execution_kind="blanket_block",
            event_sink=event_sink,
            seed=seed,
            fault_enabled=True,
            protection_mode="blanket_block",
        )
        minimized_raw = await self._run(
            run_id=run_id,
            execution_kind="minimized_counterexample",
            event_sink=event_sink,
            seed=seed,
            fault_enabled=True,
        )

        all_success_runs = [
            baseline_raw,
            perturbed_raw,
            *perturbed_replays,
            protected_raw,
            *protected_replays,
            *neighbors,
            *benign_controls,
            minimized_raw,
            blanket_block_control,
        ]
        source = perturbed_raw.source
        if source is None:
            raise SandboxDiagnosticError(
                "source_contract_invalid", "runner returned no source evidence"
            )
        if source.get("source_ref") != expected_source_ref:
            raise SandboxDiagnosticError(
                "source_contract_invalid",
                "runner source evidence does not match the preflight source ref",
            )
        initial_snapshot_hash = _same_contract(all_success_runs, source=source, seed=seed)
        if not blanket_block_control.succeeded:
            failure = (
                blanket_block_control.failure.to_dict()
                if blanket_block_control.failure
                else {}
            )
            raise SandboxDiagnosticError(
                "runner_failed",
                f"blanket block control stopped with {failure.get('code', 'unknown')}",
            )

        def payment_count(run: SandboxRunResult) -> int:
            return sum(entry.get("tool") == "payment.charge" for entry in run.ledger)

        if payment_count(perturbed_raw) != 2 or payment_count(protected_raw) != 1:
            raise SandboxDiagnosticError(
                "ledger_contract_invalid",
                "commit_then_timeout did not measure 2 charges before and 1 after protection",
            )
        if len(
            [
                entry
                for entry in perturbed_raw.ledger
                if entry.get("effect") == "payment_and_order_commit"
            ]
        ) != 2:
            raise SandboxDiagnosticError(
                "ledger_contract_invalid",
                "perturbed ledger did not contain two payment/order commits",
            )
        if len(
            [
                entry
                for entry in protected_raw.ledger
                if entry.get("effect") == "payment_and_order_commit"
            ]
        ) != 1:
            raise SandboxDiagnosticError(
                "ledger_contract_invalid",
                "protected ledger did not contain one payment/order commit",
            )
        mission_runs = [
            baseline_raw,
            perturbed_raw,
            *perturbed_replays,
            protected_raw,
            *protected_replays,
            *neighbors,
            *benign_controls,
            minimized_raw,
        ]
        if not all(_mission_succeeded(run) for run in mission_runs):
            raise SandboxDiagnosticError(
                "mission_not_completed",
                "a baseline, fault, replay, neighbor, benign, or minimized AUT run "
                "failed its mission",
            )

        scenario = plan.scenario
        baseline_scenario = scenario.model_copy(
            update={"scenario_id": f"{scenario.scenario_id}~baseline", "faults": ()}
        )
        protected_scenario = scenario.model_copy(
            update={"scenario_id": f"{scenario.scenario_id}~protected"}
        )
        neighbor_scenario = scenario.model_copy(
            update={
                "scenario_id": f"{scenario.scenario_id}~neighbor-call-2",
                "faults": tuple(
                    fault.model_copy(update={"at_call_index": fault.at_call_index + 1})
                    for fault in scenario.faults
                ),
            }
        )
        benign_scenario = scenario.model_copy(
            update={"scenario_id": f"{scenario.scenario_id}~benign", "faults": ()}
        )
        baseline = _canonical_run(baseline_raw, baseline_scenario, None)
        perturbed = _canonical_run(perturbed_raw, scenario, None)
        protected = _canonical_run(protected_raw, protected_scenario, None)
        neighbor_runs = [_canonical_run(raw, neighbor_scenario, None) for raw in neighbors]
        benign_runs = [_canonical_run(raw, benign_scenario, None) for raw in benign_controls]
        minimized = _canonical_run(minimized_raw, scenario, None)

        max_spend = _max_spend(manifest, mission)
        purchase_count = _purchase_count(mission)
        invariants = life_v0_invariants(
            max_spend_krw=max_spend,
            purchase_count=purchase_count,
            side_effect_tool="payment.charge",
            mission_completion_tool="payment.charge",
        )
        observed = evaluate(invariants, perturbed)
        if event_sink is not None:
            event_sink(
                "target.assessment",
                {
                    "execution_scope": "target_sandbox",
                    "target_model": "reference_source_child",
                    "target_agent_mode": "reference_fallback",
                    "verification_runtime": "bounded_child_reference",
                    "source_ref": source["source_ref"],
                    "passed": observed.passed,
                    "violations": sorted(observed.violated_ids()),
                    "ledger_refs": sorted(
                        {ref for violation in observed.violations for ref in violation.ledger_refs}
                    ),
                    "trace_refs": sorted(
                        {ref for violation in observed.violations for ref in violation.trace_refs}
                    ),
                    "world": _target_world(perturbed_raw),
                },
            )
        if not observed.violations:
            raise SandboxDiagnosticError(
                "evidence_missing", "the allowlisted AUT produced no cited invariant violation"
            )
        if (
            not perturbed_raw.agent_result
            or perturbed_raw.agent_result.get("status") != "completed"
        ):
            raise SandboxDiagnosticError(
                "runner_protocol_malformed", "perturbed AUT did not complete"
            )
        divergence = first_divergence(baseline.trace, perturbed.trace)
        if divergence is None:
            raise SandboxDiagnosticError(
                "evidence_missing", "perturbed AUT had no baseline divergence"
            )
        replay_digests = (
            perturbed_raw.trace_digest,
            *(raw.trace_digest for raw in perturbed_replays),
        )
        protected_replay_digests = (
            protected_raw.trace_digest,
            *(raw.trace_digest for raw in protected_replays),
        )
        if len(set(replay_digests)) != 1 or len(set(protected_replay_digests)) != 1:
            raise SandboxDiagnosticError(
                "replay_nondeterministic", "three replay digests were not identical"
            )
        if minimized.trace != perturbed.trace or not evaluate(invariants, minimized).violations:
            raise SandboxDiagnosticError(
                "minimization_failed", "the one-fault local counterexample was not reproduced"
            )

        hypothesis = hypothesis_for(plan, profile, mission)
        if hypothesis is None:
            raise SandboxDiagnosticError(
                "diagnosis_missing", "the selected local AUT plan produced no diagnosis"
            )
        patch = _patch_for(
            hypothesis.category,
            max_spend_krw=max_spend,
            purchase_count=purchase_count,
        )
        if patch is None:
            raise SandboxDiagnosticError(
                "mitigation_missing", "no reviewed mitigation exists for the finding"
            )
        protected = protected.model_copy(update={"policy_applied": patch})
        neighbor_runs = [run.model_copy(update={"policy_applied": patch}) for run in neighbor_runs]
        benign_runs = [run.model_copy(update={"policy_applied": patch}) for run in benign_runs]
        before_ids = observed.violated_ids()
        protected_report = evaluate(invariants, protected)
        protected_ids = protected_report.violated_ids()
        fixed = before_ids - protected_ids
        same_seed = _gate_report(
            "same_seed",
            protected_scenario.scenario_id,
            protected,
            invariants,
            passed=(
                protected_report.passed
                and _mission_succeeded(protected_raw)
                and protected_ids <= before_ids
                and bool(fixed)
            ),
        )
        neighbor_results = []
        for raw, run in zip(neighbors, neighbor_runs, strict=True):
            neighbor_report = evaluate(invariants, run)
            neighbor_results.append(
                _gate_report(
                    "neighbor",
                    neighbor_scenario.scenario_id,
                    run,
                    invariants,
                    passed=(
                        same_seed.passed
                        and _mission_succeeded(raw)
                        and neighbor_report.passed
                        and not (fixed & neighbor_report.violated_ids())
                    ),
                )
            )
        benign_results = []
        for raw, run in zip(benign_controls, benign_runs, strict=True):
            benign_report = evaluate(invariants, run)
            benign_results.append(
                _gate_report(
                    "benign_control",
                    benign_scenario.scenario_id,
                    run,
                    invariants,
                    passed=_mission_succeeded(raw) and benign_report.passed,
                )
            )
        verification = PatchVerification(
            same_seed=same_seed,
            neighbors=neighbor_results,
            benign=benign_results,
            accepted=(
                same_seed.passed
                and all(gate.passed for gate in neighbor_results)
                and bool(benign_results)
                and all(gate.passed for gate in benign_results)
                and blanket_block_control.agent_result is not None
                and blanket_block_control.agent_result.get("status") == "failed"
            ),
        )
        if not verification.accepted:
            raise SandboxDiagnosticError(
                "mitigation_not_verified",
                "same-seed, neighbor, benign, and blanket-block gates did not pass",
            )

        minimized_result = MinimizationResult(
            original=scenario,
            minimized=scenario,
            removed_atoms=[],
            runs_used=1,
            reproduced=True,
        )
        evidence_id = "evidence-" + hashlib.sha256(
            canonical_json(
                {
                    "source_ref": source["source_ref"],
                    "perturbed": perturbed_raw.trace_digest,
                    "protected": protected_raw.trace_digest,
                }
            ).encode("utf-8")
        ).hexdigest()[:20]
        violation_ledger_refs = tuple(
            sorted({ref for violation in observed.violations for ref in violation.ledger_refs})
        )
        violation_trace_refs = tuple(
            sorted({ref for violation in observed.violations for ref in violation.trace_refs})
        )
        divergence_trace_ref = (
            divergence.failing_event.index
            if divergence.failing_event is not None
            else divergence.index
        )
        raw_evidence = SandboxDiagnosticEvidence(
            run_id=run_id,
            evidence_id=evidence_id,
            source=source,
            fixture_id=SANDBOX_FIXTURE_ID,
            seed=seed,
            initial_snapshot_hash=initial_snapshot_hash,
            baseline=baseline_raw,
            perturbed=perturbed_raw,
            protected=protected_raw,
            perturbed_replays=perturbed_replays,
            protected_replays=protected_replays,
            neighbors=neighbors,
            benign_controls=benign_controls,
            blanket_block_control=blanket_block_control,
            minimized=minimized_raw,
            first_divergence_trace_ref=divergence_trace_ref,
            violation_ledger_refs=violation_ledger_refs,
            violation_trace_refs=violation_trace_refs,
            minimized_trace_refs=tuple(sorted({divergence_trace_ref, *violation_trace_refs})),
            replay_digests=replay_digests,
            protected_replay_digests=protected_replay_digests,
            policy_id=PROTECTED_POLICY_ID,
        )
        report = build_report(
            f"f-{plan.plan_id}",
            observed=observed,
            reproduction_count=3,
            reproduction_total=3,
            diagnosis_hypothesis=hypothesis,
            proposed_patch=patch,
            verification=verification,
            first_divergence=divergence,
            minimized_counterexample=minimized_result,
            evidence=(
                ArtifactRef(
                    kind="run_digest",
                    ref=f"sha256:{perturbed_raw.trace_digest}",
                    detail=f"{run_id}:{evidence_id}:perturbed raw runner trace",
                ),
                ArtifactRef(
                    kind="jsonl",
                    ref=f"{run_id}#sandbox.evidence:{evidence_id}",
                    detail="Raw Stream and JSONL evidence envelope",
                ),
                ArtifactRef(
                    kind="snapshot",
                    ref=f"sha256:{initial_snapshot_hash}",
                    detail=f"{SANDBOX_FIXTURE_ID}@seed:{seed} initial snapshot",
                ),
            ),
            residual_risk=[LOCAL_AUT_SCOPE, "실제 결제·캘린더 side effect는 실행하지 않음"],
            unsupported_scope=[LOCAL_AUT_SCOPE],
            experiments_run=11,
            families=(plan.scenario.faults[0].fault.value,),
            cost_units_used=22,
        )
        lab_report = _legacy_report(
            manifest=manifest,
            profile=profile,
            mission=mission,
            invariants=invariants,
            finding_report=report,
            hypothesis=hypothesis,
            patch=patch,
            verification=verification,
            divergence=divergence,
            minimized=minimized_result,
            experiments_run=11,
            cost_units_used=22,
            scope_note=LOCAL_AUT_SCOPE,
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
            report=report,
            lab_report=lab_report,
            experiments_run=11,
            cost_units_used=22,
            execution_scope="target_sandbox",
            sandbox_evidence=raw_evidence,
        )


__all__ = [
    "BLANKET_BLOCK_POLICY_ID",
    "InitialTargetObservation",
    "LOCAL_AUT_EXECUTION_MODE",
    "LOCAL_AUT_SCOPE",
    "PROTECTED_POLICY_ID",
    "LocalSandboxDiagnosticLoop",
    "SandboxDiagnosticError",
    "SandboxDiagnosticEvidence",
    "TargetAgentConfig",
]
