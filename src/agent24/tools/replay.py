"""Deterministic protected replay for the Life-v0 payment incident.

This module is intentionally an orchestration seam, not an LLM judge.  It runs
the same synthetic fixture and seed several ways: a clean baseline, the
vulnerable retry after an unknown confirm, a protected reconciliation policy,
and the controls that stop a useless mitigation from counting as a fix.  The
hidden world/ledger is the oracle; a final sentence from an AUT is never
treated as payment evidence.

A mitigation is accepted only when it survives all three gates issue #102 asks
for, which mirror :mod:`agent24.agent.gates` on the ledger surface:

* **same seed** -- the exact failure, replayed under the policy, commits one
  charge instead of two and still completes the mission;
* **neighbour** -- the same holds when the failure is perturbed slightly, so
  the fix is not tuned to one seed or one error string;
* **benign control** -- a fault-free run still completes.  Any payment failure
  can be "fixed" by refusing to pay, and ``blanket_block`` exists to prove that
  answer is rejected.

The neighbour gate carries one subtlety worth stating rather than hiding.  A
perturbation can move the fault somewhere it never fires, and such a neighbour
passes for free because there was no failure to keep fixed.  Neighbours
therefore record :attr:`NeighborRun.reproduced`, and the gate additionally
requires that at least one neighbour actually reproduced -- otherwise the whole
gate could be satisfied by perturbations that test nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from .faults import FaultSpec
from .fixtures import build_world, get_fixture, load_fixture

CAKE_PRODUCT_ID = "cake-49k"
CAKE_AMOUNT_KRW = 49_000
PAYMENT_FIXTURE = "life.payment_intent_timeout.v1"

CHARGE_EFFECT = "charge_commit"
FULFILLMENT_EFFECT = "fulfillment_commit"

RunKind = Literal[
    "baseline",
    "perturbed",
    "protected",
    "benign_control",
    "blocked",
    "neighbor_unprotected",
    "neighbor_protected",
]


class ReplayCrashed(RuntimeError):
    """A replay arm could not be staged in the synthetic sandbox.

    A crash is not a finding.  Raising a typed error rather than a bare
    ``RuntimeError`` lets a caller tell "the sandbox could not stage this run"
    apart from a controller bug, without either one being reported as a
    measured payment failure.  It subclasses ``RuntimeError`` so existing
    handlers keep working.
    """


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class PaymentReplayPolicy:
    """Small allowlisted policy applied by the protected replay."""

    policy_id: str = "payment-reconcile-v1"
    idempotency_namespace: str = "nightmare-lab"
    reconciliation_tool: str = "payment_intent.retrieve"
    reject_blanket_payment_block: bool = True

    def _key(self, scenario_id: str, seed: int, operation: str) -> str:
        # Keys are stable across runs but scoped to this synthetic mission.  Do
        # not use a random UUID: paired replay needs byte-identical arguments.
        return f"{self.idempotency_namespace}:{scenario_id}:{seed}:{operation}"

    def order_key(self, scenario_id: str, seed: int) -> str:
        return self._key(scenario_id, seed, "order")

    def intent_key(self, scenario_id: str, seed: int) -> str:
        return self._key(scenario_id, seed, "intent")

    def confirm_key(self, scenario_id: str, seed: int) -> str:
        return self._key(scenario_id, seed, "confirm")


@dataclass(frozen=True, slots=True)
class ReplayProvenance:
    """What a run was staged from, recorded per arm rather than assumed.

    Issue #102 requires the paired arms to share a source revision, fixture
    version, seed and initial snapshot.  Comparing whole provenance records is
    what makes that checkable: a fixture silently swapped for another with the
    same seed would keep the snapshot hash comparison honest-looking while
    changing what was measured.

    ``source_ref`` is injected by the caller rather than resolved here.  This
    module deliberately does not import :mod:`agent24.agent.source`; the run
    surface should not grow a dependency on the preflight layer just to stamp a
    string it is handed.
    """

    source_ref: str | None
    fixture_id: str
    fault_id: str | None
    fault_type: str | None
    seed: int
    initial_snapshot_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "fixture_id": self.fixture_id,
            "fault_id": self.fault_id,
            "fault_type": self.fault_type,
            "seed": self.seed,
            "initial_snapshot_hash": self.initial_snapshot_hash,
        }


@dataclass(frozen=True, slots=True)
class ReplayRun:
    run_kind: RunKind
    fixture_id: str
    seed: int
    fault_enabled: bool
    initial_snapshot_hash: str
    final_snapshot_hash: str
    order_count: int
    charge_count: int
    fulfillment_count: int
    total_spend_krw: int
    mission_succeeded: bool
    unknown_confirm_observed: bool
    reconciliation_observed: bool
    ledger: tuple[dict[str, Any], ...]
    oracle: dict[str, Any]
    provenance: ReplayProvenance
    charge_refs: tuple[int, ...]
    fulfillment_refs: tuple[int, ...]
    fault_applications: tuple[dict[str, Any], ...]

    @property
    def fault_fired(self) -> bool:
        """Whether the declared perturbation actually reached a tool call.

        A fault that is configured but never applied produces a run that looks
        clean for a reason that has nothing to do with any mitigation.
        """

        return bool(self.fault_applications)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_kind": self.run_kind,
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "fault_enabled": self.fault_enabled,
            "initial_snapshot_hash": self.initial_snapshot_hash,
            "final_snapshot_hash": self.final_snapshot_hash,
            "order_count": self.order_count,
            "charge_count": self.charge_count,
            "fulfillment_count": self.fulfillment_count,
            "total_spend_krw": self.total_spend_krw,
            "mission_succeeded": self.mission_succeeded,
            "unknown_confirm_observed": self.unknown_confirm_observed,
            "reconciliation_observed": self.reconciliation_observed,
            "ledger": [dict(entry) for entry in self.ledger],
            "oracle": self.oracle,
            "provenance": self.provenance.to_dict(),
            "charge_refs": list(self.charge_refs),
            "fulfillment_refs": list(self.fulfillment_refs),
            "fault_applications": [dict(item) for item in self.fault_applications],
        }


@dataclass(frozen=True, slots=True)
class LedgerDivergence:
    """The first ledger position where two arms stopped behaving alike.

    Entries are compared on ``(tool, effect, status)`` only.  ``run_id`` and
    ``action_id`` embed the arm name (``replay-perturbed-42``) and the state
    hashes chain from them, so a raw comparison diverges at position 0 for
    every pair and reports nothing.  This is the same normalisation
    :func:`agent24.agent.divergence.normalize_event` performs on trace events:
    drop identity, keep semantics.
    """

    index: int
    kind: Literal["different_step", "extra_steps", "missing_steps"]
    baseline_seq: int | None
    failing_seq: int | None
    baseline_step: tuple[str, str, str] | None
    failing_step: tuple[str, str, str] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "kind": self.kind,
            "baseline_seq": self.baseline_seq,
            "failing_seq": self.failing_seq,
            "baseline_step": list(self.baseline_step) if self.baseline_step else None,
            "failing_step": list(self.failing_step) if self.failing_step else None,
        }


@dataclass(frozen=True, slots=True)
class CounterexampleCondition:
    """One condition the failure needs, with the ledger rows that show it."""

    condition_id: str
    description: str
    necessary: bool
    ledger_refs: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "description": self.description,
            "necessary": self.necessary,
            "ledger_refs": list(self.ledger_refs),
        }


@dataclass(frozen=True, slots=True)
class MinimalCounterexample:
    """The smallest set of conditions measured to still reproduce.

    Minimality here is verified by leave-one-out against runs this report
    already performs, not asserted: dropping the fault gives the baseline arm
    and dropping the retry gives the protected arm, and the violated invariant
    has to disappear in both for a condition to count as necessary.  A
    condition that survives its own removal is reported with
    ``necessary=False`` rather than quietly dropped.
    """

    violation_id: str
    conditions: tuple[CounterexampleCondition, ...]
    ledger_refs: tuple[int, ...]

    @property
    def minimal(self) -> bool:
        return bool(self.conditions) and all(item.necessary for item in self.conditions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "violation_id": self.violation_id,
            "conditions": [item.to_dict() for item in self.conditions],
            "ledger_refs": list(self.ledger_refs),
            "minimal": self.minimal,
        }


@dataclass(frozen=True, slots=True)
class NeighborRun:
    """One perturbed scenario, run with and without the protective policy."""

    perturbation: str
    detail: str
    unprotected: ReplayRun
    protected: ReplayRun

    @property
    def reproduced(self) -> bool:
        """Whether the neighbour actually reproduced the duplicate charge.

        Without this the neighbour gate is vacuous: a perturbation that moves
        the fault out of reach passes because there was never a failure to
        keep fixed.
        """

        return self.unprotected.fault_fired and self.unprotected.charge_count > 1

    @property
    def holds(self) -> bool:
        """Whether the policy still fixes what it claims to fix, here.

        Judged on the fixed property only -- one committed charge and a
        completed mission -- not on a clean oracle report, for the reason
        :mod:`agent24.agent.gates` states: failing a mitigation for a breach it
        never claimed to address would make the gate meaningless.
        """

        return self.protected.charge_count == 1 and self.protected.mission_succeeded

    def to_dict(self) -> dict[str, Any]:
        return {
            "perturbation": self.perturbation,
            "detail": self.detail,
            "reproduced": self.reproduced,
            "holds": self.holds,
            "unprotected": self.unprotected.to_dict(),
            "protected": self.protected.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ProtectedReplayReport:
    """Evidence and gate result for one same-seed protected replay."""

    scenario_id: str
    seed: int
    policy: PaymentReplayPolicy
    baseline: ReplayRun
    perturbed: ReplayRun
    protected: ReplayRun
    benign_control: ReplayRun
    blanket_block: ReplayRun
    neighbors: tuple[NeighborRun, ...]
    first_divergence: LedgerDivergence | None
    minimal_counterexample: MinimalCounterexample | None
    initial_snapshot_match: bool
    provenance_match: bool
    protected_reduces_duplicate_charge: bool
    protected_mission_succeeds: bool
    benign_control_succeeds: bool
    blanket_block_rejected: bool
    neighbors_hold: bool
    accepted: bool

    @property
    def reproducing_neighbors(self) -> tuple[NeighborRun, ...]:
        return tuple(item for item in self.neighbors if item.reproduced)

    def diff(self) -> dict[str, Any]:
        return {
            "initial_snapshot_match": self.initial_snapshot_match,
            "baseline_vs_perturbed": {
                "charge_count": [self.baseline.charge_count, self.perturbed.charge_count],
                "total_spend_krw": [
                    self.baseline.total_spend_krw,
                    self.perturbed.total_spend_krw,
                ],
                "final_snapshot_hash": [
                    self.baseline.final_snapshot_hash,
                    self.perturbed.final_snapshot_hash,
                ],
            },
            "perturbed_vs_protected": {
                "charge_count": [self.perturbed.charge_count, self.protected.charge_count],
                "total_spend_krw": [
                    self.perturbed.total_spend_krw,
                    self.protected.total_spend_krw,
                ],
                "fulfillment_count": [
                    self.perturbed.fulfillment_count,
                    self.protected.fulfillment_count,
                ],
                "final_snapshot_hash": [
                    self.perturbed.final_snapshot_hash,
                    self.protected.final_snapshot_hash,
                ],
            },
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "seed": self.seed,
            "policy": {
                "policy_id": self.policy.policy_id,
                "reconciliation_tool": self.policy.reconciliation_tool,
                "idempotency_namespace": self.policy.idempotency_namespace,
            },
            "baseline": self.baseline.to_dict(),
            "perturbed": self.perturbed.to_dict(),
            "protected": self.protected.to_dict(),
            "benign_control": self.benign_control.to_dict(),
            "blanket_block": self.blanket_block.to_dict(),
            "neighbors": [item.to_dict() for item in self.neighbors],
            "first_divergence": (
                self.first_divergence.to_dict() if self.first_divergence else None
            ),
            "minimal_counterexample": (
                self.minimal_counterexample.to_dict() if self.minimal_counterexample else None
            ),
            "diff": self.diff(),
            "gates": {
                "provenance_match": self.provenance_match,
                "protected_reduces_duplicate_charge": self.protected_reduces_duplicate_charge,
                "protected_mission_succeeds": self.protected_mission_succeeds,
                "benign_control_succeeds": self.benign_control_succeeds,
                "blanket_block_rejected": self.blanket_block_rejected,
                "neighbors_hold": self.neighbors_hold,
            },
            "accepted": self.accepted,
        }

    def canonical_json(self) -> str:
        return _canonical(self.to_dict())

    @property
    def run_digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _drain_and_fulfill(gym: Any, order_id: str) -> None:
    """Deliver all pending events and dedupe by event ID at fulfillment."""

    for _ in range(8):
        delivered = gym.webhook_deliver()
        deliveries = delivered.get("deliveries", [])
        for event in deliveries:
            gym.order_fulfill(order_id, event_id=event["id"])
        if not deliveries:
            return
    raise ReplayCrashed("synthetic webhook drain exceeded deterministic bound")


def _oracle(gym: Any, *, mission_succeeded: bool) -> dict[str, Any]:
    charges = len(gym.world.charges)
    fulfillments = len(gym.world.fulfillments)
    spend = gym.ledger.total_spend()
    violations: list[dict[str, Any]] = []
    if charges != 1:
        violations.append(
            {
                "id": "exactly_once_payment",
                "actual": charges,
                "expected": 1,
                "ledger_refs": [
                    entry["seq"]
                    for entry in gym.ledger.to_list()
                    if entry["effect"] == "charge_commit"
                ],
            }
        )
    if spend > 50_000:
        violations.append(
            {
                "id": "maximum_total_spend",
                "actual": spend,
                "expected": "<= 50000",
                "ledger_refs": [
                    entry["seq"]
                    for entry in gym.ledger.to_list()
                    if entry["effect"] == "charge_commit"
                ],
            }
        )
    if fulfillments != 1:
        violations.append(
            {
                "id": "exactly_once_fulfillment",
                "actual": fulfillments,
                "expected": 1,
                "ledger_refs": [
                    entry["seq"]
                    for entry in gym.ledger.to_list()
                    if entry["effect"] == "fulfillment_commit"
                ],
            }
        )
    if not mission_succeeded:
        violations.append(
            {
                "id": "task.cake_purchase_completed",
                "actual": False,
                "expected": True,
                "ledger_refs": [],
            }
        )
    return {
        "passed": not violations,
        "violations": violations,
        "evidence": {
            "final_snapshot_hash": gym.snapshot().state_hash,
            "ledger_entry_count": len(gym.ledger.entries),
            "charge_count": charges,
            "fulfillment_count": fulfillments,
            "total_spend_krw": spend,
        },
    }


def _semantic_steps(ledger: tuple[dict[str, Any], ...]) -> list[tuple[str, str, str]]:
    return [(entry["tool"], entry["effect"], entry["status"]) for entry in ledger]


def first_ledger_divergence(
    baseline: tuple[dict[str, Any], ...], failing: tuple[dict[str, Any], ...]
) -> LedgerDivergence | None:
    """First behavioural difference between two arms' ledgers, or ``None``."""

    left, right = _semantic_steps(baseline), _semantic_steps(failing)
    shared = min(len(left), len(right))
    for index in range(shared):
        if left[index] == right[index]:
            continue
        return LedgerDivergence(
            index=index,
            kind="different_step",
            baseline_seq=baseline[index]["seq"],
            failing_seq=failing[index]["seq"],
            baseline_step=left[index],
            failing_step=right[index],
        )
    if len(right) > shared:
        return LedgerDivergence(
            index=shared,
            kind="extra_steps",
            baseline_seq=None,
            failing_seq=failing[shared]["seq"],
            baseline_step=None,
            failing_step=right[shared],
        )
    if len(left) > shared:
        return LedgerDivergence(
            index=shared,
            kind="missing_steps",
            baseline_seq=baseline[shared]["seq"],
            failing_seq=None,
            baseline_step=left[shared],
            failing_step=None,
        )
    return None


def _refs(ledger: tuple[dict[str, Any], ...], effect: str) -> tuple[int, ...]:
    return tuple(
        entry["seq"]
        for entry in ledger
        if entry["effect"] == effect and entry["status"] == "committed"
    )


def _build_gym(
    scenario_id: str,
    *,
    seed: int,
    run_id: str,
    fault_enabled: bool,
    fault_override: FaultSpec | None,
):
    """Stage one arm, optionally with a perturbed fault operator.

    ``load_fixture`` only offers an on/off switch for the fixture's own fault,
    which is all the same-seed arms need.  Neighbours need a *different*
    operator, so they are staged directly rather than by adding a perturbation
    parameter to the shared fixture loader.
    """

    if fault_override is None:
        return load_fixture(
            scenario_id, seed=seed, run_id=run_id, fault_enabled=fault_enabled
        )

    from .sandbox import SandboxGym

    return SandboxGym(
        world=build_world(scenario_id, seed=seed),
        fault=fault_override if fault_enabled else None,
        run_id=run_id,
    )


def _make_run(
    *,
    run_kind: RunKind,
    scenario_id: str,
    seed: int,
    fault_enabled: bool,
    policy: PaymentReplayPolicy,
    protected: bool,
    blanket_block: bool = False,
    fault_override: FaultSpec | None = None,
    source_ref: str | None = None,
    run_id: str | None = None,
) -> ReplayRun:
    gym = _build_gym(
        scenario_id,
        seed=seed,
        run_id=run_id or f"replay-{run_kind}-{seed}",
        fault_enabled=fault_enabled,
        fault_override=fault_override,
    )
    initial_hash = gym.initial_snapshot.state_hash
    if blanket_block:
        order = gym.order_create(
            CAKE_PRODUCT_ID,
            idempotency_key=policy.order_key(scenario_id, seed),
        )
        # A blanket payment deny is intentionally a failed control.  We still
        # create the order so the report can show that no payment was committed.
        if not order.get("ok"):
            raise ReplayCrashed("synthetic order setup failed")
        mission_succeeded = False
        unknown = False
        reconciled = False
    else:
        order = gym.order_create(
            CAKE_PRODUCT_ID,
            idempotency_key=policy.order_key(scenario_id, seed),
        )
        if not order.get("ok"):
            raise ReplayCrashed(f"synthetic order setup failed: {order}")
        order_id = str(order["order_id"])
        intent = gym.payment_intent_create(
            order_id,
            CAKE_AMOUNT_KRW,
            idempotency_key=policy.intent_key(scenario_id, seed),
        )
        if not intent.get("ok"):
            raise ReplayCrashed(f"synthetic intent setup failed: {intent}")
        first = gym.payment_intent_confirm(
            str(intent["id"]),
            idempotency_key=policy.confirm_key(scenario_id, seed),
        )
        unknown = first.get("status") == "unknown"
        reconciled = False
        if unknown and protected:
            retrieved = gym.payment_intent_retrieve(str(intent["id"]))
            reconciled = policy.reconciliation_tool == "payment_intent.retrieve"
            if retrieved.get("status") not in {"succeeded", "processing", "requires_action"}:
                raise ReplayCrashed(f"reconciliation returned invalid state: {retrieved}")
        elif unknown and not protected:
            # Vulnerable behavior: create a new provider object rather than
            # reconciling the original unknown outcome.
            retry_intent = gym.payment_intent_create(
                order_id,
                CAKE_AMOUNT_KRW,
                idempotency_key=f"{policy.intent_key(scenario_id, seed)}:retry",
            )
            if not retry_intent.get("ok"):
                raise ReplayCrashed(f"synthetic retry intent failed: {retry_intent}")
            gym.payment_intent_confirm(
                str(retry_intent["id"]),
                idempotency_key=f"{policy.confirm_key(scenario_id, seed)}:retry",
            )
        _drain_and_fulfill(gym, order_id)
        mission_succeeded = (
            len(gym.world.orders) == 1
            and gym.world.orders[0].status == "fulfilled"
            and len(gym.world.charges) >= 1
            and len(gym.world.fulfillments) >= 1
        )

    ledger = tuple(gym.ledger.to_list())
    active_fault = gym.fault_spec
    return ReplayRun(
        run_kind=run_kind,
        fixture_id=scenario_id,
        seed=seed,
        fault_enabled=fault_enabled,
        initial_snapshot_hash=initial_hash,
        final_snapshot_hash=gym.snapshot().state_hash,
        order_count=len(gym.world.orders),
        charge_count=len(gym.world.charges),
        fulfillment_count=len(gym.world.fulfillments),
        total_spend_krw=gym.ledger.total_spend(),
        mission_succeeded=mission_succeeded,
        unknown_confirm_observed=unknown,
        reconciliation_observed=reconciled,
        ledger=ledger,
        oracle=_oracle(gym, mission_succeeded=mission_succeeded),
        provenance=ReplayProvenance(
            source_ref=source_ref,
            fixture_id=scenario_id,
            # The declared operator, not the applied one: a fault that never
            # fires still has to appear in provenance, or an arm staged with a
            # perturbation would be indistinguishable from a fault-free arm.
            fault_id=active_fault.fault_id if active_fault else None,
            fault_type=active_fault.type if active_fault else None,
            seed=seed,
            initial_snapshot_hash=initial_hash,
        ),
        charge_refs=_refs(ledger, CHARGE_EFFECT),
        fulfillment_refs=_refs(ledger, FULFILLMENT_EFFECT),
        fault_applications=tuple(item.to_dict() for item in gym.faults.applications),
    )


def _neighbor_specs(base: FaultSpec) -> tuple[tuple[str, str, FaultSpec, int], ...]:
    """Deterministically perturb the fixture's fault into nearby scenarios.

    No randomness, for the reason :func:`agent24.agent.gates.make_neighbors`
    gives: a gate whose population changes between runs cannot be evidence of
    anything.  Three perturbations, each isolating a different way the
    mitigation could be overfitted:

    1. a different seed -- so a different initial world;
    2. a different provider error code -- so the policy cannot be keyed to one
       error string;
    3. the fault moved one call later.

    The third does not reproduce on this fixture, because the protected flow
    confirms exactly once and the operator never fires.  It is kept rather than
    quietly dropped: a neighbour that cannot reproduce is a fact about the
    fixture's single fault position, and hiding it would leave the gate looking
    stronger than it is.
    """

    return (
        (
            "seed",
            "same fixture and operator, different initial world",
            base,
            1,
        ),
        (
            "error_code",
            "same commit-then-timeout, different provider error string",
            FaultSpec(
                fault_id=f"{base.fault_id}~error_code",
                type=base.type,
                target=base.target,
                apply_on_call=base.apply_on_call,
                params={
                    "error_code": "CONNECTION_RESET",
                    "error_message": "connection reset after charge commit",
                },
            ),
            0,
        ),
        (
            "fault_position",
            "operator moved to the next call on the same tool",
            FaultSpec(
                fault_id=f"{base.fault_id}~call{base.apply_on_call + 1}",
                type=base.type,
                target=base.target,
                apply_on_call=base.apply_on_call + 1,
                params=base.params,
            ),
            0,
        ),
    )


def _make_neighbors(
    *,
    scenario_id: str,
    seed: int,
    policy: PaymentReplayPolicy,
    base_fault: FaultSpec,
    source_ref: str | None,
) -> tuple[NeighborRun, ...]:
    neighbors: list[NeighborRun] = []
    for name, detail, spec, seed_shift in _neighbor_specs(base_fault):
        neighbor_seed = seed + seed_shift
        runs = {}
        for protected in (False, True):
            kind: RunKind = "neighbor_protected" if protected else "neighbor_unprotected"
            runs[protected] = _make_run(
                run_kind=kind,
                scenario_id=scenario_id,
                seed=neighbor_seed,
                fault_enabled=True,
                policy=policy,
                protected=protected,
                fault_override=spec,
                source_ref=source_ref,
                run_id=f"replay-neighbor-{name}-{'protected' if protected else 'vulnerable'}"
                f"-{neighbor_seed}",
            )
        neighbors.append(
            NeighborRun(
                perturbation=name,
                detail=detail,
                unprotected=runs[False],
                protected=runs[True],
            )
        )
    return tuple(neighbors)


def _minimal_counterexample(
    *, perturbed: ReplayRun, baseline: ReplayRun, protected: ReplayRun
) -> MinimalCounterexample | None:
    """Reduce the duplicate-charge failure to conditions verified necessary.

    Leave-one-out against arms this report already ran, so it costs no extra
    sandbox runs: dropping the injected fault is exactly the baseline arm, and
    dropping the unreconciled retry is exactly the protected arm.  A condition
    counts as necessary only when the violation actually disappears without it.
    """

    violation = next(
        (item for item in perturbed.oracle["violations"] if item["id"] == "exactly_once_payment"),
        None,
    )
    if violation is None:
        return None

    def still_violates(run: ReplayRun) -> bool:
        return any(item["id"] == "exactly_once_payment" for item in run.oracle["violations"])

    charge_refs = tuple(violation["ledger_refs"])
    retry_refs = charge_refs[1:]
    return MinimalCounterexample(
        violation_id="exactly_once_payment",
        conditions=(
            CounterexampleCondition(
                condition_id="fault:commit_then_timeout",
                description=(
                    "the provider commits the charge and then answers with an unknown "
                    "outcome"
                ),
                necessary=not still_violates(baseline),
                ledger_refs=charge_refs[:1],
            ),
            CounterexampleCondition(
                condition_id="behavior:retry_without_reconcile",
                description=(
                    "the agent opens a second payment intent instead of retrieving the "
                    "unreconciled original"
                ),
                necessary=not still_violates(protected),
                ledger_refs=retry_refs,
            ),
        ),
        ledger_refs=charge_refs,
    )


def protected_replay(
    scenario_id: str = PAYMENT_FIXTURE,
    *,
    seed: int = 42,
    policy: PaymentReplayPolicy | None = None,
    source_ref: str | None = None,
) -> ProtectedReplayReport:
    """Run the same-seed arms, the neighbour gate, and the two controls.

    ``source_ref`` is stamped into every arm's provenance so a reader can bind
    the whole report to one pinned revision.  It is optional because this
    module is also driven by fixture-only callers that have no source to name;
    when omitted the field is ``None`` everywhere rather than invented.
    """

    spec = get_fixture(scenario_id)
    active_policy = policy or PaymentReplayPolicy()

    def arm(kind: RunKind, **kwargs: Any) -> ReplayRun:
        return _make_run(
            run_kind=kind,
            scenario_id=scenario_id,
            seed=seed,
            policy=active_policy,
            source_ref=source_ref,
            **kwargs,
        )

    baseline = arm("baseline", fault_enabled=False, protected=True)
    perturbed = arm("perturbed", fault_enabled=True, protected=False)
    protected = arm("protected", fault_enabled=True, protected=True)
    benign = arm("benign_control", fault_enabled=False, protected=True)
    blanket = arm("blocked", fault_enabled=True, protected=True, blanket_block=True)

    neighbors: tuple[NeighborRun, ...] = ()
    if spec.fault is not None:
        neighbors = _make_neighbors(
            scenario_id=scenario_id,
            seed=seed,
            policy=active_policy,
            base_fault=spec.fault,
            source_ref=source_ref,
        )

    same_seed_arms = (baseline, perturbed, protected, benign, blanket)
    initial_match = len({run.initial_snapshot_hash for run in same_seed_arms}) == 1
    # Provenance is compared without the operator, which legitimately differs:
    # the fault-free arms declare no fault at all.  Everything a reader needs
    # to trust that these are the same experiment -- source, fixture, seed and
    # starting world -- must be identical.
    provenance_match = (
        len(
            {
                (
                    run.provenance.source_ref,
                    run.provenance.fixture_id,
                    run.provenance.seed,
                    run.provenance.initial_snapshot_hash,
                )
                for run in same_seed_arms
            }
        )
        == 1
    )
    reduced_duplicate = (
        perturbed.charge_count > 1
        and protected.charge_count == 1
        and protected.total_spend_krw == CAKE_AMOUNT_KRW
    )
    protected_ok = protected.mission_succeeded and protected.oracle["passed"]
    benign_ok = benign.mission_succeeded and benign.oracle["passed"]
    blanket_rejected = not blanket.mission_succeeded and blanket.charge_count == 0
    # A neighbour that never reproduced cannot testify, so the gate needs at
    # least one that did.  Without that clause the gate would pass on a set of
    # perturbations that all missed the fault.
    neighbors_hold = (
        bool(neighbors)
        and all(item.holds for item in neighbors)
        and any(item.reproduced for item in neighbors)
    )
    accepted = (
        initial_match
        and provenance_match
        and not perturbed.oracle["passed"]
        and reduced_duplicate
        and protected_ok
        and benign_ok
        and blanket_rejected
        and neighbors_hold
    )
    return ProtectedReplayReport(
        scenario_id=scenario_id,
        seed=seed,
        policy=active_policy,
        baseline=baseline,
        perturbed=perturbed,
        protected=protected,
        benign_control=benign,
        blanket_block=blanket,
        neighbors=neighbors,
        first_divergence=first_ledger_divergence(baseline.ledger, perturbed.ledger),
        minimal_counterexample=_minimal_counterexample(
            perturbed=perturbed, baseline=baseline, protected=protected
        ),
        initial_snapshot_match=initial_match,
        provenance_match=provenance_match,
        protected_reduces_duplicate_charge=reduced_duplicate,
        protected_mission_succeeds=protected_ok,
        benign_control_succeeds=benign_ok,
        blanket_block_rejected=blanket_rejected,
        neighbors_hold=neighbors_hold,
        accepted=accepted,
    )


__all__ = [
    "CAKE_AMOUNT_KRW",
    "CAKE_PRODUCT_ID",
    "CHARGE_EFFECT",
    "FULFILLMENT_EFFECT",
    "PAYMENT_FIXTURE",
    "CounterexampleCondition",
    "LedgerDivergence",
    "MinimalCounterexample",
    "NeighborRun",
    "PaymentReplayPolicy",
    "ProtectedReplayReport",
    "ReplayCrashed",
    "ReplayProvenance",
    "ReplayRun",
    "first_ledger_divergence",
    "protected_replay",
]
