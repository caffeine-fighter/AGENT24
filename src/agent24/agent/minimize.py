"""Delta debugging: shrink a failing scenario to a minimal counterexample.

A failure found with four injected faults and a world override is not yet a
finding -- nobody can tell which of the five conditions actually mattered.
:func:`minimize` removes conditions until removing any more makes the failure
disappear, so the reported counterexample is the smallest set of conditions
that still reproduces.

The unit of removal is an *atom*:

* ``"fault:0"``, ``"fault:1"``, ... -- one per :class:`FaultSpec`, in the order
  the scenario declares them,
* ``"override:shop.item"`` -- one per ``world_overrides`` key, sorted.

Two properties this module leans on, both fixed by the frozen contract:

* :attr:`FaultSpec.at_call_index` counts calls to its own ``target_tool``, so
  dropping an unrelated fault never renumbers a surviving one.  No renumbering
  pass is needed, and none is done.
* :class:`Scenario` is frozen but **not hashable** (``world_overrides`` is a
  dict), so the memo is keyed on a ``frozenset`` of atom ids rather than on the
  scenario.  Rebuilding is cheap; re-running the gym is not.

``scenario_id`` is deliberately preserved across rebuilds.  The scripted AUTs
derive their idempotency key from it, so renaming a reduced scenario would
change the charge payload, hence its digest, hence ledger identity -- perturbing
a variable minimization is not supposed to be touching.
"""

from collections.abc import Awaitable, Callable, Collection, Sequence

from .models import MinimizationResult, Scenario

FAULT_PREFIX = "fault:"
OVERRIDE_PREFIX = "override:"


class _BudgetExhausted(Exception):
    """Raised internally when ``max_runs`` is reached mid-search."""


def atoms_for(scenario: Scenario) -> list[str]:
    """Every removable condition, in canonical order.

    Faults keep declaration order; override keys are sorted so the atom list
    never depends on dict insertion order (``apply_overrides`` sorts too).
    """

    atoms = [f"{FAULT_PREFIX}{index}" for index in range(len(scenario.faults))]
    atoms += [f"{OVERRIDE_PREFIX}{key}" for key in sorted(scenario.world_overrides)]
    return atoms


def rebuild(scenario: Scenario, kept: Collection[str]) -> Scenario:
    """Rebuild ``scenario`` with only ``kept`` atoms present."""

    keep = set(kept)
    faults = tuple(
        fault
        for index, fault in enumerate(scenario.faults)
        if f"{FAULT_PREFIX}{index}" in keep
    )
    overrides = {
        key: value
        for key, value in sorted(scenario.world_overrides.items())
        if f"{OVERRIDE_PREFIX}{key}" in keep
    }
    return scenario.model_copy(update={"faults": faults, "world_overrides": overrides})


def _split(config: Sequence[str], count: int) -> list[list[str]]:
    """Split into ``count`` contiguous chunks of near-equal size."""

    chunks: list[list[str]] = []
    start = 0
    for index in range(count):
        stop = start + (len(config) - start) // (count - index)
        chunks.append(list(config[start:stop]))
        start = stop
    return [chunk for chunk in chunks if chunk]


async def minimize(
    scenario: Scenario,
    reproduces: Callable[[Scenario], Awaitable[bool]],
    *,
    max_runs: int = 24,
) -> MinimizationResult:
    """Shrink ``scenario`` to a minimal set of atoms that still reproduces.

    ``reproduces`` is supplied by the caller and decides what "the same failure"
    means -- normally "run the gym on this scenario and the oracle reports the
    same violated invariant id".  Anchoring on the invariant id rather than on
    the full report is what keeps the predicate monotone.

    Classic ddmin: granularity 2, try each chunk, then each complement, restart
    at granularity 2 on any reduction, double granularity otherwise.  Every
    probe goes through a ``frozenset``-keyed memo, so a configuration is never
    re-run.  ``runs_used`` counts real probes only; memo hits are free.

    ``max_runs`` is a hard cap.  When it is hit the search stops and returns the
    smallest configuration *verified* to reproduce -- never an unverified guess.
    """

    atoms = atoms_for(scenario)
    memo: dict[frozenset[str], bool] = {}
    runs = 0

    async def probe(candidate: Sequence[str]) -> bool:
        nonlocal runs
        key = frozenset(candidate)
        if key in memo:
            return memo[key]
        if runs >= max_runs:
            raise _BudgetExhausted
        runs += 1
        verdict = await reproduces(rebuild(scenario, key))
        memo[key] = verdict
        return verdict

    try:
        if not await probe(atoms):
            # ddmin's contract requires a failing input.  Guessing a smaller one
            # from a scenario that never failed would fabricate a counterexample.
            return MinimizationResult(
                original=scenario,
                minimized=scenario,
                removed_atoms=[],
                runs_used=runs,
                reproduced=False,
            )
    except _BudgetExhausted:
        return MinimizationResult(
            original=scenario,
            minimized=scenario,
            removed_atoms=[],
            runs_used=runs,
            reproduced=False,
        )

    config = list(atoms)
    granularity = 2

    try:
        while len(config) >= 2:
            chunks = _split(config, min(granularity, len(config)))

            reduced = False
            for chunk in chunks:
                if await probe(chunk):
                    config, granularity, reduced = chunk, 2, True
                    break
            if reduced:
                continue

            for chunk in chunks:
                complement = [atom for atom in config if atom not in set(chunk)]
                if complement and await probe(complement):
                    config = complement
                    granularity = max(granularity - 1, 2)
                    reduced = True
                    break
            if reduced:
                continue

            if granularity >= len(config):
                break
            granularity = min(granularity * 2, len(config))
    except _BudgetExhausted:
        # `config` is always a configuration a probe already confirmed.
        pass

    kept = set(config)
    return MinimizationResult(
        original=scenario,
        minimized=rebuild(scenario, kept),
        removed_atoms=[atom for atom in atoms if atom not in kept],
        runs_used=runs,
        reproduced=True,
    )


__all__ = ["FAULT_PREFIX", "OVERRIDE_PREFIX", "atoms_for", "minimize", "rebuild"]
