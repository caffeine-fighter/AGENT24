"""Traceability guard for the D5a acceptance families.

The authoritative case set is :data:`D5A_PLAN_PATH` — the ratified manifest,
vendored verbatim from `eval/rekhet-d5a-test-plan` at commit `9bec2a1`. It is
read from disk, never restated in a test module, because a guard that compares a
local list against buckets derived from that same local list cannot detect a case
disappearing from both.

Three properties this enforces, each of which was missing from the first attempt:

* the family's case ids come from the manifest, so deleting a case from a test
  module cannot make the module consistent again;
* ``COVERED_ELSEWHERE`` entries are pytest node ids that are actually collected,
  so a typo or a deleted external test fails here rather than reading as covered;
* the module claiming a family must be one of the surfaces the manifest assigns
  to it, so a file cannot adopt a family it was never given.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
D5A_PLAN_PATH = REPO_ROOT / "tests" / "evals" / "d5a-plan.json"

EXPECTED_SCHEMA_VERSION = "nightmare.d5a.plan.v1"
EXPECTED_STATUS = "ratified"


@pytest.fixture(scope="session")
def d5a_plan() -> Mapping[str, Any]:
    """The ratified plan, refusing a draft or a substituted schema."""

    assert D5A_PLAN_PATH.is_file(), f"canonical D5a manifest is missing at {D5A_PLAN_PATH}"
    plan = json.loads(D5A_PLAN_PATH.read_text(encoding="utf-8"))
    assert plan["schema_version"] == EXPECTED_SCHEMA_VERSION
    assert plan["status"] == EXPECTED_STATUS
    return plan


@pytest.fixture(scope="session")
def d5a_family(d5a_plan: Mapping[str, Any]):
    families = {family["id"]: family for family in d5a_plan["coverage_families"]}

    def _family(family_id: str) -> Mapping[str, Any]:
        assert family_id in families, (
            f"{family_id} is not a family in the ratified plan; known families are "
            f"{sorted(families)}"
        )
        return families[family_id]

    return _family


@pytest.fixture(scope="session")
def collected_node_ids():
    """Ask pytest which of ``node_ids`` it can actually collect.

    A subprocess rather than an in-process ``pytest.main`` call: re-entering the
    running session would share plugin state with the very run doing the asking.
    Only external node ids are ever passed, so this never recurses into the
    acceptance suite.
    """

    cache: dict[tuple[str, ...], set[str]] = {}

    def _collect(node_ids: Sequence[str]) -> set[str]:
        key = tuple(sorted(node_ids))
        if key in cache:
            return cache[key]
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
                *key,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        found = {
            line.strip().replace("\\", "/")
            for line in completed.stdout.splitlines()
            if "::" in line
        }
        cache[key] = found
        return found

    return _collect


@pytest.fixture
def assert_family_is_fully_accounted_for(d5a_family, collected_node_ids):
    """Assert one family's manifest cases are partitioned, and the claims hold.

    ``implemented`` maps a case id to a test function in ``module``.
    ``covered`` maps a case id to pytest node ids that must collect.
    ``blocked`` maps a case id to a non-empty reason.

    T1 fails traceability on a missing mapping rather than turning it into a
    residual-risk note once results are known, so every manifest case must land
    in exactly one of the three.
    """

    def _assert(
        family_id: str,
        *,
        module: ModuleType,
        implemented: Mapping[str, str],
        covered: Mapping[str, Sequence[str]],
        blocked: Mapping[str, str],
    ) -> None:
        family = d5a_family(family_id)
        authoritative = set(family["case_ids"])
        assert authoritative, f"{family_id} declares no case ids"

        # The module may only claim a family the manifest routed to it.
        surfaces = {surface.replace("\\", "/") for surface in family["implementation_surfaces"]}
        module_path = (
            Path(module.__file__).resolve().relative_to(REPO_ROOT).as_posix()  # type: ignore[arg-type]
        )
        assert module_path in surfaces, (
            f"{module_path} claims {family_id}, but the plan assigns that family to "
            f"{sorted(surfaces)}"
        )

        buckets: tuple[tuple[str, Iterable[str]], ...] = (
            ("implemented", implemented),
            ("covered", covered),
            ("blocked", blocked),
        )
        seen: set[str] = set()
        for name, bucket in buckets:
            overlap = seen & set(bucket)
            assert not overlap, f"{sorted(overlap)} appear in two buckets, including {name}"
            seen |= set(bucket)

        missing = authoritative - seen
        invented = seen - authoritative
        assert not missing, (
            f"{family_id} cases {sorted(missing)} are in the ratified plan but are neither "
            "implemented, covered elsewhere, nor blocked with a reason"
        )
        assert not invented, (
            f"{family_id} declares {sorted(invented)}, which the ratified plan does not contain"
        )

        members = vars(module)
        module_tests = {
            name
            for name in members
            if name.startswith("test_") and callable(members[name])
        }
        for case_id, test_name in implemented.items():
            assert test_name in module_tests, (
                f"{case_id} names {test_name!r}, which is not a test in {module_path}"
            )

        wanted = [node for nodes in covered.values() for node in nodes]
        if wanted:
            found = collected_node_ids(wanted)
            for case_id, nodes in covered.items():
                for node in nodes:
                    assert node.replace("\\", "/") in found, (
                        f"{case_id} claims coverage by {node!r}, which pytest does not collect"
                    )

        for case_id, reason in blocked.items():
            assert reason.strip(), f"{case_id} is blocked without naming what blocks it"

    return _assert
