"""The traceability guard, audited.

A guard nobody attacks is a guard nobody has tested.  The first version of this
suite compared a locally declared case tuple against buckets derived from that
same tuple, so deleting a frozen D5a case from both kept the suite green — the
audit could be satisfied by editing the thing being audited.  These tests exist
so that failure mode, and the ones adjacent to it, cannot come back silently.

Every check below drives the real fixture from ``conftest.py`` against the real
ratified manifest.  Nothing is stubbed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

C3 = "C3_controller_success"
C4 = "C4_honest_outcomes"

HERE = Path(__file__).resolve().parent


def _load(filename: str) -> ModuleType:
    """Load a sibling test module by path.

    Not ``import tests.acceptance.<name>``: CI invokes bare ``pytest``, which
    does not put the repository root on ``sys.path``, and this suite must not
    depend on how the runner was started.
    """

    path = HERE / filename
    spec = importlib.util.spec_from_file_location(f"_d5a_audit_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


controller_success = _load("test_controller_success.py")
honest_outcomes = _load("test_honest_outcomes.py")


@pytest.fixture
def c3_module() -> ModuleType:
    return controller_success


@pytest.fixture
def c4_module() -> ModuleType:
    return honest_outcomes


def _c3_buckets():
    return (
        dict(controller_success.IMPLEMENTED_HERE),
        {key: tuple(value) for key, value in controller_success.COVERED_ELSEWHERE.items()},
        dict(controller_success.BLOCKED),
    )


def test_the_declared_partition_is_accepted_as_is(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    """Baseline: the guard is not simply rejecting everything."""

    implemented, covered, blocked = _c3_buckets()
    assert_family_is_fully_accounted_for(
        C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
    )


def test_a_case_dropped_from_every_bucket_is_still_caught(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    """The defect Rekhet found: the audit must not be satisfiable by editing it.

    There is no local case list to delete from any more, so removing a case from
    the buckets leaves it unaccounted for against the ratified manifest.
    """

    implemented, covered, blocked = _c3_buckets()
    blocked.pop("CS-01-six-phase-chain")

    with pytest.raises(AssertionError, match="CS-01-six-phase-chain"):
        assert_family_is_fully_accounted_for(
            C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_a_case_the_plan_does_not_contain_is_rejected(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    """Coverage cannot be manufactured by inventing a case id."""

    implemented, covered, blocked = _c3_buckets()
    blocked["CS-99-invented"] = "a case the ratified plan does not have"

    with pytest.raises(AssertionError, match="CS-99-invented"):
        assert_family_is_fully_accounted_for(
            C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_a_case_claimed_by_two_buckets_is_rejected(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    implemented, covered, blocked = _c3_buckets()
    blocked["CS-03-first-divergence"] = "also claimed as blocked"

    with pytest.raises(AssertionError, match="two buckets"):
        assert_family_is_fully_accounted_for(
            C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_an_implemented_case_naming_a_missing_test_is_rejected(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    implemented, covered, blocked = _c3_buckets()
    implemented["CS-03-first-divergence"] = "test_this_name_does_not_exist"

    with pytest.raises(AssertionError, match="test_this_name_does_not_exist"):
        assert_family_is_fully_accounted_for(
            C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_a_covered_case_naming_an_uncollectable_node_is_rejected(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    """The second defect: ``COVERED_ELSEWHERE`` was prose, so a typo read as covered.

    Node ids are now handed to pytest for collection, which is the only check
    that survives the external test being renamed or deleted.
    """

    implemented, covered, blocked = _c3_buckets()
    covered["CS-04-protected-replay-gates"] = (
        "tests/unit/test_gates.py::test_this_gate_test_was_deleted",
    )

    with pytest.raises(AssertionError, match="does not collect"):
        assert_family_is_fully_accounted_for(
            C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_a_module_cannot_claim_a_family_it_was_not_given(
    assert_family_is_fully_accounted_for, c4_module
) -> None:
    """The manifest assigns families to surfaces; a file cannot adopt one."""

    implemented, covered, blocked = _c3_buckets()

    with pytest.raises(AssertionError, match="claims C3_controller_success"):
        assert_family_is_fully_accounted_for(
            C3, module=c4_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_a_blocked_case_without_a_reason_is_rejected(
    assert_family_is_fully_accounted_for, c3_module
) -> None:
    implemented, covered, blocked = _c3_buckets()
    blocked["CS-01-six-phase-chain"] = "   "

    with pytest.raises(AssertionError, match="without naming what blocks it"):
        assert_family_is_fully_accounted_for(
            C3, module=c3_module, implemented=implemented, covered=covered, blocked=blocked
        )


def test_an_unknown_family_is_rejected(assert_family_is_fully_accounted_for, c3_module) -> None:
    with pytest.raises(AssertionError, match="not a family in the ratified plan"):
        assert_family_is_fully_accounted_for(
            "C9_does_not_exist", module=c3_module, implemented={}, covered={}, blocked={}
        )


def test_the_vendored_manifest_is_the_ratified_one(d5a_plan) -> None:
    """The plan this suite audits against must be the ratified artifact.

    A draft or a substituted schema would let the whole partition be redefined,
    so the two fields that identify it are asserted rather than assumed.
    """

    assert d5a_plan["schema_version"] == "nightmare.d5a.plan.v1"
    assert d5a_plan["status"] == "ratified"

    families = {family["id"] for family in d5a_plan["coverage_families"]}
    assert {C3, C4} <= families

    total_cases = sum(len(family["case_ids"]) for family in d5a_plan["coverage_families"])
    assert total_cases == 44, "the ratified plan freezes 44 visible cases"
