"""Issue #102 acceptance — stateful SandboxGym diagnosis, mitigation and replay.

This file is the traceability surface for #102: for each of the ten acceptance
checks it names either the test that proves it or the surface it was handed to,
and a guard test refuses any check that is neither.

The check list is read from ``tests/evals/issue-102-acceptance.json``, not
restated here.  That separation is the lesson of PR #113: a module that
declares both the requirement list and its own coverage can be made consistent
by deleting from both, so the audit becomes satisfiable by editing the thing
being audited.

Three of the ten checks are deliberately *not* claimed.  ``AC-01`` and
``AC-08`` live in ``src/agent24/api/runtime.py``, which two unmerged branches
are rewriting at once (PR #127 and ``agent/ucp-adapter-demo``), and the reducer
half of ``AC-10`` is A's surface.  They are recorded with their destination
rather than silently dropped or quietly claimed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agent24.tools import protected_replay
from agent24.tools.replay import ReplayCrashed

REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = REPO_ROOT / "tests" / "evals" / "issue-102-acceptance.json"

FIXTURE = "life.payment_intent_timeout.v1"


class Coverage:
    """How one acceptance check is discharged.

    ``local`` names tests in this module, ``external`` names pytest node ids
    that must actually collect, and ``handed_off`` names the surface that owns
    what is left.  A check with neither evidence nor a destination is a hole,
    and the guard says so.
    """

    def __init__(
        self,
        *,
        local: tuple[str, ...] = (),
        external: tuple[str, ...] = (),
        handed_off: str = "",
    ) -> None:
        self.local = local
        self.external = external
        self.handed_off = handed_off

    @property
    def has_evidence(self) -> bool:
        return bool(self.local or self.external)


COVERAGE: dict[str, Coverage] = {
    "AC-01-aut-runner-evidence": Coverage(
        handed_off=(
            "PR #127 (agent/122-target-agent) runs the reviewed Target Agent against "
            "SandboxGym via src/agent24/agent/target_runtime.py and wires it in "
            "src/agent24/api/runtime.py. That file is being rewritten by two unmerged "
            "branches at once, so this change does not touch it."
        )
    ),
    "AC-02-shared-provenance": Coverage(
        local=("test_ac_02_every_arm_is_bound_to_one_revision_fixture_seed_and_world",),
        external=(
            "tests/integration/test_life_replay.py::"
            "test_the_whole_report_is_bound_to_one_revision_fixture_and_starting_world",
            "tests/unit/test_replay.py::"
            "test_every_same_seed_arm_shares_one_source_fixture_seed_and_starting_world",
        ),
    ),
    "AC-03-ledger-measured-charges": Coverage(
        local=("test_ac_03_two_charges_before_the_policy_and_one_after_read_from_the_ledger",),
        external=(
            "tests/integration/test_life_replay.py::"
            "test_the_duplicate_charge_is_measured_from_the_ledger_before_and_after",
            "tests/unit/test_replay.py::"
            "test_charge_and_fulfillment_counts_agree_with_the_append_only_ledger",
        ),
    ),
    "AC-04-evidence-cites-refs": Coverage(
        local=("test_ac_04_divergence_counterexample_and_violation_cite_ledger_rows",),
        external=(
            "tests/integration/test_life_replay.py::"
            "test_divergence_counterexample_and_violation_all_cite_the_same_rows",
            "tests/unit/test_replay.py::"
            "test_first_divergence_is_the_second_intent_not_the_first_ledger_row",
            "tests/unit/test_replay.py::"
            "test_the_counterexample_keeps_only_conditions_verified_necessary",
        ),
    ),
    "AC-05-three-gates": Coverage(
        local=("test_ac_05_a_mitigation_needs_same_seed_neighbour_and_benign_control",),
        external=(
            "tests/unit/test_replay.py::"
            "test_the_mitigation_holds_on_a_different_seed_and_a_different_error_string",
            "tests/unit/test_replay.py::"
            "test_the_gate_requires_at_least_one_neighbour_that_actually_reproduced",
            "tests/unit/test_gates.py::test_the_duplicate_payment_patch_passes_every_gate",
        ),
    ),
    "AC-06-blanket-block-rejected": Coverage(
        local=("test_ac_06_refusing_to_pay_is_rejected_rather_than_counted_as_a_fix",),
        external=("tests/unit/test_gates.py::test_block_all_payments_patch_fails_benign_gate",),
    ),
    "AC-07-three-identical-digests": Coverage(
        local=("test_ac_07_three_replays_of_one_seed_produce_one_digest",),
        external=(
            "tests/integration/test_life_replay.py::"
            "test_three_replays_of_the_same_seed_produce_one_digest",
        ),
    ),
    "AC-08-raw-stream-binding": Coverage(
        handed_off=(
            "src/agent24/api/runtime.py owns the SSE/JSONL emission that binds run_id to "
            "evidence refs, and PR #127 plus agent/ucp-adapter-demo are both editing it. "
            "The replay report now carries per-arm provenance for that binding to cite."
        )
    ),
    "AC-09-no-false-promotion": Coverage(
        local=("test_ac_09_a_sandbox_crash_produces_no_report_to_promote",),
        external=(
            "tests/unit/test_report.py::test_unsupported_is_valid_and_names_its_scope",
            "tests/unit/test_report.py::test_budget_exhausted_is_valid_and_weaker_than_no_failure",
            "tests/unit/test_report.py::test_no_failure_observed_cannot_carry_a_diagnosis_or_patch",
            "tests/unit/test_report.py::test_a_clean_run_reports_no_failure_rather_than_safety",
        ),
    ),
    "AC-10-python-and-reducer-tests": Coverage(
        local=("test_ac_10_the_python_evidence_path_is_covered_by_executable_cases",),
        external=(
            "tests/evals/test_registry.py::test_every_shipped_pytest_target_exists",
            "tests/integration/test_life_replay.py::"
            "test_a_mitigation_is_verified_only_after_same_seed_neighbour_and_benign",
        ),
        handed_off=(
            "The frontend reducer half stays with A: web/tests/core.test.mjs consumes the "
            "protected_replay payload, and web/ is being reworked on three branches."
        ),
    ),
}


@pytest.fixture(scope="session")
def acceptance_checks() -> tuple[dict[str, Any], ...]:
    assert ACCEPTANCE_PATH.is_file(), f"vendored acceptance checks missing at {ACCEPTANCE_PATH}"
    plan = json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))
    assert plan["schema_version"] == "agent24.issue-acceptance.v1"
    assert plan["issue"] == 102
    return tuple(plan["checks"])


@pytest.fixture(scope="session")
def this_module() -> ModuleType:
    return sys.modules[__name__]


@pytest.fixture(scope="session")
def collects() -> Any:
    """Ask pytest, in a subprocess, which of ``node_ids`` it can collect.

    A subprocess rather than an in-process ``pytest.main``: re-entering the
    running session would share plugin state with the very run doing the
    asking.  Only external node ids are passed, so this never recurses into
    this file.

    This helper is local rather than in a ``conftest.py`` on purpose.  The D5a
    suite (PR #113) has an equivalent one, and putting this in a shared
    conftest would make #102 wait on that review; the two should be merged into
    one fixture once #113 lands.
    """

    # Verdicts are cached per node rather than per batch, so the negative tests
    # below reuse the first batch's answers and only pay a subprocess for the
    # one node id they invented.
    collectable: set[str] = set()
    uncollectable: set[str] = set()

    def _ask(node_ids: tuple[str, ...]) -> set[str]:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter, no shell
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                *node_ids,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return {
            line.strip().replace("\\", "/")
            for line in completed.stdout.splitlines()
            if "::" in line
        }

    def _collect(node_ids: Sequence[str]) -> set[str]:
        wanted = {node.replace("\\", "/") for node in node_ids}
        unknown = wanted - collectable - uncollectable
        if unknown:
            batch = tuple(sorted(unknown))
            found = _ask(batch)
            collectable.update(found)
            # One unresolvable node id makes pytest fail the whole invocation
            # and collect nothing, which would blame every check for one
            # check's typo.  Confirm the stragglers individually so the failure
            # names the node that is actually broken.  A single-node batch is
            # already its own confirmation.
            for node in unknown - found:
                if len(batch) == 1 or node not in _ask((node,)):
                    uncollectable.add(node)
                else:
                    collectable.add(node)
        return wanted & collectable

    return _collect


# --------------------------------------------------------------------------
# Traceability
# --------------------------------------------------------------------------


def _audit(
    coverage: dict[str, Coverage], *, checks, module: ModuleType, collects
) -> None:
    """Assert one coverage mapping accounts for every vendored check.

    Taken as a parameter rather than read from the module global so the guard
    can be driven against deliberately broken mappings below.  A guard nobody
    attacks is a guard nobody has tested.
    """

    declared = {check["id"] for check in checks}
    assert declared, "the vendored acceptance file declares no checks"

    missing = declared - set(coverage)
    invented = set(coverage) - declared
    assert not missing, f"issue #102 checks {sorted(missing)} are neither proved nor handed off"
    assert not invented, f"COVERAGE declares {sorted(invented)}, which issue #102 does not list"

    members = vars(module)
    local_tests = {
        name for name in members if name.startswith("test_") and callable(members[name])
    }

    wanted: list[str] = []
    for check_id, entry in coverage.items():
        assert entry.has_evidence or entry.handed_off.strip(), (
            f"{check_id} claims neither a test nor a destination"
        )
        for name in entry.local:
            assert name in local_tests, f"{check_id} names {name!r}, not a test in this module"
        wanted.extend(entry.external)

    found = collects(wanted)
    for check_id, entry in coverage.items():
        for node in entry.external:
            assert node.replace("\\", "/") in found, (
                f"{check_id} claims coverage by {node!r}, which pytest does not collect"
            )


def test_every_acceptance_check_is_proved_or_handed_off(
    acceptance_checks, this_module, collects
) -> None:
    """No check in the issue may be silently absent.

    The list comes from the vendored artifact, so dropping an entry from
    ``COVERAGE`` leaves it unaccounted for; there is no local list to drop it
    from as well.  External node ids are handed to pytest for collection,
    because a renamed or deleted test that is only named in a string would
    otherwise keep reading as covered.
    """

    _audit(dict(COVERAGE), checks=acceptance_checks, module=this_module, collects=collects)


@pytest.mark.parametrize(
    ("attack", "mutate", "expected"),
    [
        (
            "a check dropped from every bucket",
            lambda c: c.pop("AC-06-blanket-block-rejected"),
            "AC-06-blanket-block-rejected",
        ),
        (
            "a check the issue does not list",
            lambda c: c.update({"AC-99-invented": Coverage(handed_off="somewhere")}),
            "AC-99-invented",
        ),
        (
            "a check with neither evidence nor a destination",
            lambda c: c.update({"AC-06-blanket-block-rejected": Coverage()}),
            "neither a test nor a destination",
        ),
        (
            "a handoff that names no destination",
            lambda c: c.update({"AC-01-aut-runner-evidence": Coverage(handed_off="   ")}),
            "neither a test nor a destination",
        ),
        (
            "a local test that does not exist",
            lambda c: c.update(
                {"AC-07-three-identical-digests": Coverage(local=("test_deleted",))}
            ),
            "test_deleted",
        ),
        (
            "an external node pytest cannot collect",
            lambda c: c.update(
                {
                    "AC-06-blanket-block-rejected": Coverage(
                        external=("tests/unit/test_gates.py::test_this_gate_was_deleted",)
                    )
                }
            ),
            "does not collect",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) and " " in value else "",
)
def test_the_guard_rejects(
    attack, mutate, expected, acceptance_checks, this_module, collects
) -> None:
    """The audit must not be satisfiable by editing the thing being audited.

    Every attack here corresponds to a way coverage could be faked: deleting a
    requirement, inventing one, claiming a test that does not exist, or citing
    an external test that has since been renamed away.
    """

    broken = dict(COVERAGE)
    mutate(broken)

    with pytest.raises(AssertionError, match=expected):
        _audit(broken, checks=acceptance_checks, module=this_module, collects=collects)


def test_the_handed_off_checks_are_the_three_we_expect(acceptance_checks) -> None:
    """Handing work off is a decision, so it is pinned rather than left implicit.

    If a fourth check ever becomes a handoff, that is a scope change someone
    should have to notice and edit this assertion for.
    """

    handed_off = {
        check_id for check_id, coverage in COVERAGE.items() if coverage.handed_off.strip()
    }

    assert handed_off == {
        "AC-01-aut-runner-evidence",
        "AC-08-raw-stream-binding",
        "AC-10-python-and-reducer-tests",
    }
    # AC-10 is the only one carrying both: its Python half is proved here.
    assert COVERAGE["AC-10-python-and-reducer-tests"].has_evidence
    assert not COVERAGE["AC-01-aut-runner-evidence"].has_evidence
    assert not COVERAGE["AC-08-raw-stream-binding"].has_evidence


# --------------------------------------------------------------------------
# AC-02 · shared provenance
# --------------------------------------------------------------------------


def test_ac_02_every_arm_is_bound_to_one_revision_fixture_seed_and_world() -> None:
    """AC-02. The arms are comparable because they are the same experiment."""

    source_ref = "example/cake-agent@0123456789abcdef0123456789abcdef01234567"
    report = protected_replay(FIXTURE, seed=42, source_ref=source_ref)
    arms = (
        report.baseline,
        report.perturbed,
        report.protected,
        report.benign_control,
        report.blanket_block,
    )

    assert report.provenance_match is True
    assert {run.provenance.source_ref for run in arms} == {source_ref}
    assert {run.provenance.fixture_id for run in arms} == {FIXTURE}
    assert {run.provenance.seed for run in arms} == {42}
    assert len({run.provenance.initial_snapshot_hash for run in arms}) == 1


# --------------------------------------------------------------------------
# AC-03 · ledger-measured charges
# --------------------------------------------------------------------------


def test_ac_03_two_charges_before_the_policy_and_one_after_read_from_the_ledger() -> None:
    """AC-03. The before/after numbers are ledger rows, not a summary field.

    The fixture's operator is ``commit_then_timeout`` and it is asserted to
    have actually fired, so the two charges are the consequence of the declared
    fault rather than of something else that happened to go wrong.
    """

    report = protected_replay(FIXTURE, seed=42)

    assert report.perturbed.provenance.fault_type == "commit_then_timeout"
    assert report.perturbed.fault_fired is True
    assert [item["type"] for item in report.perturbed.fault_applications] == [
        "commit_then_timeout"
    ]

    assert len(report.perturbed.charge_refs) == 2
    assert len(report.protected.charge_refs) == 1
    assert len(report.perturbed.fulfillment_refs) == 2
    assert len(report.protected.fulfillment_refs) == 1

    committed = {
        entry["seq"] for entry in report.perturbed.ledger if entry["status"] == "committed"
    }
    assert set(report.perturbed.charge_refs) <= committed


# --------------------------------------------------------------------------
# AC-04 · evidence cites refs
# --------------------------------------------------------------------------


def test_ac_04_divergence_counterexample_and_violation_cite_ledger_rows() -> None:
    """AC-04. Every piece of diagnostic evidence resolves to a ledger row."""

    report = protected_replay(FIXTURE, seed=42)
    divergence = report.first_divergence
    counterexample = report.minimal_counterexample
    assert divergence is not None and counterexample is not None

    violation = next(
        item
        for item in report.perturbed.oracle["violations"]
        if item["id"] == "exactly_once_payment"
    )
    rows = {entry["seq"] for entry in report.perturbed.ledger}

    assert divergence.failing_seq in rows
    assert set(violation["ledger_refs"]) <= rows
    assert set(counterexample.ledger_refs) <= rows
    for condition in counterexample.conditions:
        assert condition.ledger_refs, condition.condition_id
        assert set(condition.ledger_refs) <= rows


# --------------------------------------------------------------------------
# AC-05 · three gates
# --------------------------------------------------------------------------


def test_ac_05_a_mitigation_needs_same_seed_neighbour_and_benign_control() -> None:
    """AC-05. Acceptance is the conjunction, and the neighbour gate is real.

    ``neighbors_hold`` requires that at least one neighbour actually
    reproduced the failure -- otherwise a set of perturbations that all missed
    the fault would satisfy the gate without testing anything.
    """

    report = protected_replay(FIXTURE, seed=42)

    assert report.protected_reduces_duplicate_charge is True
    assert report.neighbors_hold is True
    assert report.benign_control_succeeds is True
    assert report.accepted is True

    assert len(report.neighbors) == 3
    assert len(report.reproducing_neighbors) == 2
    # The seed neighbour starts from a different world, which is what makes it
    # evidence against seed overfitting rather than a second copy of the run.
    seed_neighbor = next(item for item in report.neighbors if item.perturbation == "seed")
    assert seed_neighbor.reproduced is True
    assert (
        seed_neighbor.protected.initial_snapshot_hash != report.protected.initial_snapshot_hash
    )


# --------------------------------------------------------------------------
# AC-06 · blanket blocking rejected
# --------------------------------------------------------------------------


def test_ac_06_refusing_to_pay_is_rejected_rather_than_counted_as_a_fix() -> None:
    """AC-06. The gate that matters most: zero charges is not success."""

    report = protected_replay(FIXTURE, seed=42)

    assert report.blanket_block.charge_count == 0
    assert report.blanket_block.charge_refs == ()
    assert report.blanket_block.mission_succeeded is False
    assert report.blanket_block.oracle["passed"] is False
    assert report.blanket_block_rejected is True
    assert any(
        item["id"] == "task.cake_purchase_completed"
        for item in report.blanket_block.oracle["violations"]
    )


# --------------------------------------------------------------------------
# AC-07 · deterministic digest
# --------------------------------------------------------------------------


def test_ac_07_three_replays_of_one_seed_produce_one_digest() -> None:
    """AC-07. Three, because a pair can agree by accident more easily."""

    digests = {protected_replay(FIXTURE, seed=42).run_digest for _ in range(3)}

    assert len(digests) == 1


# --------------------------------------------------------------------------
# AC-09 · nothing false is promoted to a finding
# --------------------------------------------------------------------------


def test_ac_09_a_sandbox_crash_produces_no_report_to_promote(monkeypatch) -> None:
    """AC-09, crash half. A run that could not be staged returns nothing.

    The unsupported and budget-exhausted halves are proved by the D5a C4 tests
    named in ``COVERAGE``; this covers the third, which had no test before.
    A crash raises rather than returning a partially-populated report, so there
    is no object for a caller to read an ``accepted`` flag off.
    """

    from agent24.tools.sandbox import SandboxGym

    monkeypatch.setattr(
        SandboxGym,
        "order_create",
        lambda self, *args, **kwargs: {"ok": False, "error": "synthetic staging failure"},
    )

    with pytest.raises(ReplayCrashed):
        protected_replay(FIXTURE, seed=42)


# --------------------------------------------------------------------------
# AC-10 · executable coverage
# --------------------------------------------------------------------------


def test_ac_10_the_python_evidence_path_is_covered_by_executable_cases() -> None:
    """AC-10, Python half. The eval registry actually names these tests.

    The registry is executable (issue #107), so a case naming a test that no
    longer exists fails to collect rather than reading as coverage.  Asserting
    the #102 cases are present is what stops this file from being the only
    thing that knows about them.
    """

    from agent24.evals.registry import load_registry

    cases = load_registry().by_id()
    demo5 = {case_id for case_id in cases if case_id.startswith("demo5-")}

    assert demo5, "no demo5 eval case is registered for issue #102"
    for case_id in demo5:
        case = cases[case_id]
        assert case.issue == 102, case_id
        assert case.runner == "pytest", case_id
        assert case.targets, case_id
