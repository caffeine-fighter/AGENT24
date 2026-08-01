"""Patch acceptance gates: same seed, neighbours, benign control.

A patch is accepted only when it survives all three:

1. **same_seed** -- replaying the exact failure under the patch fixes at least
   one violated invariant and introduces no new one.
2. **neighbour** -- the invariants the patch fixed stay fixed when the failure
   is perturbed slightly, so the fix is not overfitted to one seed.
3. **benign_control** -- a fault-free, well-behaved run under the patch still
   completes the mission.

Gate 3 is the one that matters most.  Any payment failure can be "fixed" by
refusing to pay; a patch that drives task success to zero is a failed patch, not
a safe one.  ``BLOCK_ALL_PAYMENTS_PATCH`` exists as a fixture precisely to prove
this gate rejects it.

Two criteria deserve their reasoning stated, because the obvious alternatives
are both wrong:

* **same_seed is a subset test, not "the oracle passes".**  A pure retry loop
  never completes the mission patched or unpatched, so demanding a clean report
  would reject the correct bounded-retry patch.  The test is
  ``violated(patched) ⊆ violated(unpatched)`` (nothing new broke) together with
  ``fixed ≠ ∅`` (something was actually repaired).  ``fixed ≠ ∅`` is what stops
  a no-op patch from verifying.
* **A neighbour is judged on the fixed set, not on a clean report.**  Perturbing
  a numeric world override can legitimately flip an unrelated invariant such as
  the budget; failing a patch for a breach it never claimed to address would
  make the gate meaningless.  The question a neighbour answers is only: did what
  the patch fixed *stay* fixed?
"""

from collections.abc import Sequence

from .models import (
    AntibodyPatch,
    GateResult,
    Invariant,
    OracleReport,
    PatchVerification,
    Scenario,
)
from .oracle import evaluate
from .protocols import GymProtocol

NUMERIC_NUDGE = 500
"""Step for perturbing an integer world override (49000 -> 49500).

Integers only: monetary amounts must stay ``int`` for ``args_digest`` to be
stable, and a float nudge would silently change every digest in the run.
"""


# --------------------------------------------------------------------------
# Neighbour generation (pure)
# --------------------------------------------------------------------------


def make_neighbors(failure: Scenario) -> list[Scenario]:
    """Deterministically perturb ``failure`` into nearby scenarios.

    No randomness: the same failure always yields the same neighbours in the
    same order, because a gate whose population changes between runs cannot be
    evidence of anything.  Perturbations, in order:

    1. each fault's ``at_call_index`` decremented (when it is above zero),
    2. each fault's ``at_call_index`` incremented,
    3. the fault list reversed (only when there are at least two faults),
    4. each integer ``world_overrides`` value nudged by :data:`NUMERIC_NUDGE`.

    Candidates identical to the original -- ignoring ``scenario_id`` -- are
    dropped, as are duplicates of each other.
    """

    candidates: list[Scenario] = []

    for index, fault in enumerate(failure.faults):
        if fault.at_call_index > 0:
            updated = fault.model_copy(update={"at_call_index": fault.at_call_index - 1})
            candidates.append(_with_fault(failure, index, updated))

    for index, fault in enumerate(failure.faults):
        updated = fault.model_copy(update={"at_call_index": fault.at_call_index + 1})
        candidates.append(_with_fault(failure, index, updated))

    if len(failure.faults) >= 2:
        candidates.append(failure.model_copy(update={"faults": tuple(reversed(failure.faults))}))

    for key in sorted(failure.world_overrides):
        value = failure.world_overrides[key]
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        overrides = dict(failure.world_overrides)
        overrides[key] = value + NUMERIC_NUDGE
        candidates.append(failure.model_copy(update={"world_overrides": overrides}))

    # Scenario is unhashable, so dedupe on the canonical JSON with the id
    # normalized away -- otherwise every candidate looks distinct.
    seen = {_shape(failure)}
    neighbors: list[Scenario] = []
    for candidate in candidates:
        shape = _shape(candidate)
        if shape in seen:
            continue
        seen.add(shape)
        neighbors.append(
            candidate.model_copy(
                update={"scenario_id": f"{failure.scenario_id}~n{len(neighbors)}"}
            )
        )
    return neighbors


def _with_fault(scenario: Scenario, index: int, replacement) -> Scenario:
    faults = list(scenario.faults)
    faults[index] = replacement
    return scenario.model_copy(update={"faults": tuple(faults)})


def _shape(scenario: Scenario) -> str:
    """Identity of a scenario ignoring its id."""

    return scenario.model_copy(update={"scenario_id": ""}).model_dump_json()


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


async def verify_patch(
    patch: AntibodyPatch,
    failure: Scenario,
    gym: GymProtocol,
    invariants: Sequence[Invariant],
    *,
    benign: Sequence[Scenario],
    neighbors: Sequence[Scenario] | None = None,
    unpatched: OracleReport | None = None,
) -> PatchVerification:
    """Run all three gate groups and decide whether ``patch`` is accepted.

    ``invariants`` must include a mission-completion invariant, or the benign
    control cannot see an over-safe patch.  This is a caller contract rather
    than a runtime check: raising here would blame the patch for a caller bug.

    ``unpatched`` lets a caller donate the failure report it already has.  When
    omitted it is obtained by replaying ``failure`` without the patch, which is
    safe because ``GymProtocol`` guarantees byte-identical replay.

    No gate short-circuits.  Even after ``same_seed`` fails, the neighbour and
    benign gates still run, so the report shows *how* a rejected patch failed
    rather than only that it did.
    """

    if unpatched is None:
        unpatched = evaluate(invariants, await gym.run(failure, None))

    patched_report = evaluate(invariants, await gym.run(failure, patch))
    before, after = unpatched.violated_ids(), patched_report.violated_ids()
    fixed = before - after

    same_seed = GateResult(
        gate="same_seed",
        scenario_id=failure.scenario_id,
        passed=after <= before and bool(fixed),
        oracle=patched_report,
    )

    if neighbors is None:
        neighbors = make_neighbors(failure)

    neighbor_results: list[GateResult] = []
    for neighbor in neighbors:
        report = evaluate(invariants, await gym.run(neighbor, patch))
        neighbor_results.append(
            GateResult(
                gate="neighbor",
                scenario_id=neighbor.scenario_id,
                passed=same_seed.passed and not (fixed & report.violated_ids()),
                oracle=report,
            )
        )

    benign_results: list[GateResult] = []
    for control in benign:
        report = evaluate(invariants, await gym.run(control, patch))
        benign_results.append(
            GateResult(
                gate="benign_control",
                scenario_id=control.scenario_id,
                passed=report.passed,
                oracle=report,
            )
        )

    accepted = (
        same_seed.passed
        and all(result.passed for result in neighbor_results)
        and bool(benign_results)
        and all(result.passed for result in benign_results)
    )

    return PatchVerification(
        same_seed=same_seed,
        neighbors=neighbor_results,
        benign=benign_results,
        accepted=accepted,
    )


__all__ = ["NUMERIC_NUDGE", "make_neighbors", "verify_patch"]
