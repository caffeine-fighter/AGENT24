"""Unit tests for delta-debugging minimization.

The canned duplicate-payment scenario is built for exactly this: one fault that
matters (``commit_then_timeout``), three decoy faults, and one inert world
override.  Five atoms must collapse to one.
"""

from agent24.agent import oracle
from agent24.agent.fakes import (
    PURCHASE_MISSION,
    FakeGym,
    _scenario_for,
    mission_completed_invariant,
)
from agent24.agent.minimize import atoms_for, minimize, rebuild
from agent24.agent.models import (
    INV_PURCHASE_COUNT,
    Comparator,
    ComparisonCheck,
    FailureCategory,
    FaultKind,
    FaultSpec,
    Invariant,
    LedgerAggSelector,
    LiteralSelector,
    Mission,
    Scenario,
)

PURCHASE_COUNT = Invariant(
    id=INV_PURCHASE_COUNT,
    kind="task",
    description="결제는 정확히 한 번만 발생해야 한다.",
    check=ComparisonCheck(
        lhs=LedgerAggSelector(tool="payment.charge", agg="count"),
        op=Comparator.EQ,
        rhs=LiteralSelector(value=1),
    ),
)

DUPLICATE = _scenario_for(FailureCategory.DUPLICATE_SIDE_EFFECT, PURCHASE_MISSION)


def counting_predicate(target: str = INV_PURCHASE_COUNT):
    """A ``reproduces`` callable plus the list of scenarios it was asked about.

    Anchoring on one invariant id -- not on the whole report -- is what keeps
    the predicate monotone as decoys are removed.
    """

    seen: list[Scenario] = []

    async def reproduces(scenario: Scenario) -> bool:
        seen.append(scenario)
        run = await FakeGym().run(scenario, None)
        return target in oracle.evaluate([PURCHASE_COUNT], run).violated_ids()

    return reproduces, seen


# --------------------------------------------------------------------------
# Atoms and rebuilding
# --------------------------------------------------------------------------


def test_atoms_are_faults_in_order_then_sorted_override_keys():
    assert atoms_for(DUPLICATE) == [
        "fault:0",
        "fault:1",
        "fault:2",
        "fault:3",
        "override:shop.item",
    ]


def test_atoms_of_a_bare_scenario_are_empty():
    assert atoms_for(Scenario(scenario_id="sc", seed=1, mission=Mission(text="t"))) == []


def test_override_atoms_do_not_depend_on_dict_insertion_order():
    first = Scenario(
        scenario_id="sc",
        seed=1,
        mission=Mission(text="t"),
        world_overrides={"b": 1, "a": 2},
    )
    second = first.model_copy(update={"world_overrides": {"a": 2, "b": 1}})
    assert atoms_for(first) == atoms_for(second) == ["override:a", "override:b"]


def test_rebuilding_with_every_atom_reproduces_the_original():
    assert rebuild(DUPLICATE, atoms_for(DUPLICATE)) == DUPLICATE


def test_rebuilding_keeps_declaration_order_and_at_call_index():
    """``at_call_index`` counts calls to the fault's own target tool, so dropping
    an unrelated fault must not renumber a surviving one."""

    scenario = DUPLICATE.model_copy(
        update={
            "faults": (
                FaultSpec(
                    fault=FaultKind.MALICIOUS_WEB_CONTENT, target_tool="web.read", at_call_index=2
                ),
                FaultSpec(fault=FaultKind.COMMIT_THEN_TIMEOUT, target_tool="payment.charge"),
                FaultSpec(
                    fault=FaultKind.EMPTY_RESULT, target_tool="file.read", at_call_index=3
                ),
            )
        }
    )
    reduced = rebuild(scenario, {"fault:0", "fault:2"})
    assert [fault.target_tool for fault in reduced.faults] == ["web.read", "file.read"]
    assert [fault.at_call_index for fault in reduced.faults] == [2, 3]


def test_rebuilding_preserves_scenario_id():
    """The scripted AUTs derive their idempotency key from ``scenario_id``.
    Renaming a reduced scenario would change the charge payload -- and therefore
    ledger identity -- which is a variable minimization must not touch."""

    reduced = rebuild(DUPLICATE, {"fault:0"})
    assert reduced.scenario_id == DUPLICATE.scenario_id
    assert reduced.seed == DUPLICATE.seed
    assert reduced.aut_profile == DUPLICATE.aut_profile


# --------------------------------------------------------------------------
# Reduction
# --------------------------------------------------------------------------


async def test_the_duplicate_scenario_reduces_to_the_single_fault_that_matters():
    reproduces, _ = counting_predicate()
    result = await minimize(DUPLICATE, reproduces)

    assert result.reproduced
    assert len(result.minimized.faults) == 1
    assert result.minimized.faults[0].fault is FaultKind.COMMIT_THEN_TIMEOUT
    assert result.minimized.world_overrides == {}
    assert result.removed_atoms == [
        "fault:1",
        "fault:2",
        "fault:3",
        "override:shop.item",
    ]
    assert result.original == DUPLICATE


async def test_the_minimized_scenario_still_reproduces_the_failure():
    reproduces, _ = counting_predicate()
    result = await minimize(DUPLICATE, reproduces)
    fresh, _ = counting_predicate()
    assert await fresh(result.minimized)


async def test_an_irreducible_scenario_is_returned_unchanged():
    """The injection scenario has exactly one fault and it is load bearing."""

    scenario = _scenario_for(FailureCategory.INDIRECT_INJECTION, PURCHASE_MISSION)

    async def reproduces(candidate: Scenario) -> bool:
        run = await FakeGym().run(candidate, None)
        return bool(run.ledger) and any(entry.tool == "email.send" for entry in run.ledger)

    result = await minimize(scenario, reproduces)
    assert result.reproduced
    assert result.removed_atoms == []
    assert result.minimized == scenario


async def test_a_scenario_that_never_reproduces_short_circuits():
    """ddmin's contract requires a failing input.  Shrinking one that never
    failed would fabricate a counterexample."""

    calls = 0

    async def never(_: Scenario) -> bool:
        nonlocal calls
        calls += 1
        return False

    result = await minimize(DUPLICATE, never)
    assert not result.reproduced
    assert result.minimized == DUPLICATE
    assert result.removed_atoms == []
    assert result.runs_used == calls == 1


async def test_a_scenario_without_atoms_is_handled():
    bare = Scenario(scenario_id="sc-bare", seed=1, mission=Mission(text="t"))

    async def always(_: Scenario) -> bool:
        return True

    result = await minimize(bare, always)
    assert result.reproduced
    assert result.minimized == bare
    assert result.removed_atoms == []


# --------------------------------------------------------------------------
# Budget, memoization, determinism
# --------------------------------------------------------------------------


async def test_runs_used_never_exceeds_max_runs():
    reproduces, seen = counting_predicate()
    result = await minimize(DUPLICATE, reproduces, max_runs=3)
    assert result.runs_used <= 3
    assert len(seen) == result.runs_used


async def test_a_truncated_search_still_returns_a_verified_configuration():
    """Hitting the budget must never invent a smaller scenario that was not
    actually probed."""

    reproduces, _ = counting_predicate()
    result = await minimize(DUPLICATE, reproduces, max_runs=2)
    assert result.reproduced
    fresh, _ = counting_predicate()
    assert await fresh(result.minimized)


async def test_a_zero_budget_reports_no_reproduction_rather_than_guessing():
    async def unreachable(_: Scenario) -> bool:  # pragma: no cover - must not run
        raise AssertionError("predicate called despite an exhausted budget")

    result = await minimize(DUPLICATE, unreachable, max_runs=0)
    assert not result.reproduced
    assert result.runs_used == 0
    assert result.minimized == DUPLICATE


async def test_no_configuration_is_probed_twice():
    reproduces, seen = counting_predicate()
    await minimize(DUPLICATE, reproduces)
    shapes = [scenario.model_dump_json() for scenario in seen]
    assert len(shapes) == len(set(shapes))


async def test_minimization_is_deterministic():
    first_reproduces, first_seen = counting_predicate()
    second_reproduces, second_seen = counting_predicate()
    first = await minimize(DUPLICATE, first_reproduces)
    second = await minimize(DUPLICATE, second_reproduces)

    assert first.model_dump_json() == second.model_dump_json()
    # The search path itself is deterministic, not merely its answer.
    assert [s.model_dump_json() for s in first_seen] == [
        s.model_dump_json() for s in second_seen
    ]


async def test_minimization_reduces_below_the_original_atom_count():
    """Issue #4's completion condition, stated directly: when a removable fault
    exists, the minimal counterexample is smaller than the original."""

    reproduces, _ = counting_predicate()
    result = await minimize(DUPLICATE, reproduces)
    assert len(atoms_for(result.minimized)) < len(atoms_for(result.original))


async def test_mission_completion_invariant_is_untouched_by_minimization():
    """Sanity check that the fixture used above really is the purchase scenario
    the mission-completion invariant is written against."""

    run = await FakeGym().run(DUPLICATE, None)
    report = oracle.evaluate([mission_completed_invariant()], run)
    assert report.passed
