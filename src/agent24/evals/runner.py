"""Run the eval registry: validate, verify targets exist, execute, summarize.

Usage::

    python -m agent24.evals            # whole registry
    python -m agent24.evals --case id  # one or more selected cases
    python -m agent24.evals --validate-only
    python -m agent24.evals --list

Three properties this runner is built around.

**No shell, ever.** pytest is invoked as ``[sys.executable, "-m", "pytest", ...]``
with node ids passed as separate argv entries and ``shell=False``. There is no
string to interpolate, so a case cannot inject a second command; the node-id
pattern in :mod:`agent24.evals.registry` additionally refuses anything that is
not a bare node id, so it cannot inject a pytest *flag* either.

**A missing target is a failure, not a skip.** Every node id in the registry is
put through one ``--collect-only`` pass first. A renamed or deleted test is the
exact drift this registry exists to catch, and reporting it as "0 collected, ok"
would reproduce the original problem.

**Bounded output.** The runner prints one line per case plus a summary. pytest's
own output is captured and only a short tail is shown for failures, so a run
never emits a raw log, a secret, or generated media as an artifact.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .harness import HarnessResult, run_declarative_case
from .registry import (
    DEFAULT_REGISTRY_PATH,
    Case,
    PytestCase,
    Registry,
    RegistryError,
    SiteCase,
    load_registry,
)

SITE_BUILD_ARTIFACT = Path("site/dist/server/index.js")
"""What ``site/tests/*.mjs`` imports. Produced by the canonical site gate."""

FAILURE_TAIL_LINES = 12
"""How much captured pytest output a failure may print. Bounded on purpose."""


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    runner: str
    passed: bool
    detail: str = ""
    checked: int = 0
    """How many structured assertions actually ran."""
    prose_only: tuple[str, ...] = ()
    """Declared fields that stayed human-readable and were not machine-checked."""
    unavailable: bool = False
    """Prerequisite absent (no node, no site build). Never silent: it prints,
    and it fails the run unless the caller passed --skip-unavailable."""

    def line(self) -> str:
        if self.unavailable:
            return f"  [SKIP] {self.case_id} ({self.runner}) -- {self.detail}"
        head = f"  [{'PASS' if self.passed else 'FAIL'}] {self.case_id} ({self.runner})"
        if not self.checked and not self.prose_only:
            return head
        # Say plainly how much was machined and what stayed prose, so a green
        # line is never read as "every word of this case was verified".
        note = f"{self.checked} checked"
        if self.prose_only:
            note += f", prose-only: {', '.join(self.prose_only)}"
        return f"{head} -- {note}"


def _pytest(args: Sequence[str], *, repo_root: Path) -> subprocess.CompletedProcess[str]:
    # shell=False and a list argv: the node ids are arguments, never a command.
    return subprocess.run(  # noqa: S603 - fixed interpreter, validated list argv
        [sys.executable, "-m", "pytest", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )


def verify_site_targets_exist(registry: Registry, *, repo_root: Path) -> list[str]:
    """Return site targets that do not exist on disk.

    This half of the drift check never depends on node being installed: a
    renamed or deleted site test is drift whether or not this machine can run
    it, so it is always an error.
    """

    missing: list[str] = []
    for case_id, target in registry.site_targets():
        if not (repo_root / "site" / target).is_file():
            missing.append(f"site/{target}  (declared by: {case_id})")
    return missing


def _is_collected(node: str, collected: set[str]) -> bool:
    """Whether a declared node id appears in a collect-only listing.

    A parametrized test collects as ``...::test_name[param]``, so a declared
    bare name is satisfied by any parametrization of it. Matching exactly would
    report every parametrized target as missing.
    """

    if "::" not in node:
        return True
    return node in collected or any(item.startswith(f"{node}[") for item in collected)


def verify_targets_exist(registry: Registry, *, repo_root: Path) -> list[str]:
    """Return declared pytest targets that do not exist.

    Two cheap passes instead of one subprocess per node. Files are checked on
    disk first, then a *single* ``--collect-only`` over the distinct files gives
    the full node inventory to diff against. Collecting per node id would mean
    150 interpreter startups for the same answer.
    """

    pairs = list(registry.pytest_targets())
    if not pairs:
        return []

    owners: dict[str, list[str]] = {}
    for case_id, node in pairs:
        owners.setdefault(node, []).append(case_id)

    def described(node: str) -> str:
        return f"{node}  (declared by: {', '.join(sorted(set(owners[node])))})"

    nodes = sorted(owners)
    missing = [n for n in nodes if not (repo_root / n.split("::", 1)[0]).is_file()]
    checkable = [n for n in nodes if n not in set(missing)]
    if not checkable:
        return [described(n) for n in missing]

    files = sorted({node.split("::", 1)[0] for node in checkable})
    completed = _pytest(["--collect-only", "-q", "--no-header", *files], repo_root=repo_root)
    if completed.returncode != 0:
        tail = "\n".join(completed.stdout.strip().splitlines()[-FAILURE_TAIL_LINES:])
        return [*(described(n) for n in missing), f"pytest could not collect:\n{tail}"]

    collected = {line.strip() for line in completed.stdout.splitlines() if "::" in line}
    missing.extend(n for n in checkable if not _is_collected(n, collected))
    return [described(node) for node in sorted(missing)]


def _run_pytest_case(case: PytestCase, *, repo_root: Path) -> CaseOutcome:
    completed = _pytest(["-q", "--no-header", *case.targets], repo_root=repo_root)
    if completed.returncode == 0:
        return CaseOutcome(case.id, case.runner, True)
    tail = "\n".join(completed.stdout.strip().splitlines()[-FAILURE_TAIL_LINES:])
    return CaseOutcome(case.id, case.runner, False, tail)


def _run_site_case(case: SiteCase, *, repo_root: Path) -> CaseOutcome:
    node = shutil.which("node")
    if node is None:
        return CaseOutcome(case.id, case.runner, False, "node is not installed", True)
    if case.requires_build and not (repo_root / SITE_BUILD_ARTIFACT).is_file():
        return CaseOutcome(
            case.id,
            case.runner,
            False,
            f"{SITE_BUILD_ARTIFACT} absent; run the canonical site gate first",
            True,
        )
    completed = subprocess.run(  # noqa: S603 - resolved executable, list argv
        [node, "--test", *case.targets],
        cwd=repo_root / "site",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    if completed.returncode == 0:
        return CaseOutcome(case.id, case.runner, True)
    tail = "\n".join(completed.stdout.strip().splitlines()[-FAILURE_TAIL_LINES:])
    return CaseOutcome(case.id, case.runner, False, tail)


def _run_declarative(case: Case, *, repo_root: Path) -> CaseOutcome:
    result: HarnessResult = run_declarative_case(case)  # type: ignore[arg-type]
    proves = getattr(case, "proves", ()) or ()
    if result.passed and proves:
        completed = _pytest(["-q", "--no-header", *proves], repo_root=repo_root)
        if completed.returncode != 0:
            tail = "\n".join(completed.stdout.strip().splitlines()[-FAILURE_TAIL_LINES:])
            return CaseOutcome(case.id, case.runner, False, f"bound test failed:\n{tail}")
    checked, prose = len(result.checks), result.prose_only
    if result.passed:
        return CaseOutcome(case.id, case.runner, True, "", checked, prose)
    if result.error:
        return CaseOutcome(case.id, case.runner, False, result.error, checked, prose)
    detail = "; ".join(f"{c.name}: {c.detail}" for c in result.failures())
    return CaseOutcome(case.id, case.runner, False, detail, checked, prose)


def run_cases(cases: Sequence[Case], *, repo_root: Path) -> list[CaseOutcome]:
    outcomes: list[CaseOutcome] = []
    for case in cases:
        if isinstance(case, PytestCase):
            outcomes.append(_run_pytest_case(case, repo_root=repo_root))
        elif isinstance(case, SiteCase):
            outcomes.append(_run_site_case(case, repo_root=repo_root))
        else:
            outcomes.append(_run_declarative(case, repo_root=repo_root))
    return outcomes


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent24.evals",
        description="Validate and execute the eval registry.",
    )
    parser.add_argument("--registry", default=None, help="path to cases.yaml")
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="ID",
        help="run only this case id (repeatable)",
    )
    parser.add_argument("--list", action="store_true", help="list case ids and exit")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="validate the schema and target existence without executing",
    )
    parser.add_argument(
        "--skip-unavailable",
        action="store_true",
        help=(
            "treat a missing prerequisite (no node, no site build) as a reported "
            "skip instead of a failure; schema and target drift still fail"
        ),
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    registry_path = Path(args.registry) if args.registry else repo_root / DEFAULT_REGISTRY_PATH

    try:
        registry = load_registry(registry_path)
    except RegistryError as error:
        print(f"registry invalid: {error}", file=sys.stderr)
        return 2

    if args.list:
        for case in registry.cases:
            print(f"{case.id}\t{case.runner}")
        return 0

    try:
        selected = registry.select(args.case) if args.case else registry.cases
    except RegistryError as error:
        print(f"{error}", file=sys.stderr)
        return 2

    print(f"registry {registry.version}: {len(registry.cases)} cases, running {len(selected)}")

    drift = verify_site_targets_exist(registry, repo_root=repo_root)
    drift += verify_targets_exist(registry, repo_root=repo_root)
    if drift:
        # Never a skip: a declared target that no longer exists is precisely the
        # rot this registry was inert against.
        print("registry drift -- declared targets do not exist:", file=sys.stderr)
        for entry in drift:
            print(f"  {entry}", file=sys.stderr)
        return 1

    if args.validate_only:
        print("schema valid; every declared pytest node collects")
        return 0

    outcomes = run_cases(selected, repo_root=repo_root)
    for outcome in outcomes:
        print(outcome.line())

    skipped = [o for o in outcomes if o.unavailable]
    # An unavailable prerequisite is always printed above. Whether it also
    # fails the run is the caller's call; schema and target drift already
    # returned before this point and are never downgradable.
    failed = [
        o
        for o in outcomes
        if not o.passed and not (o.unavailable and args.skip_unavailable)
    ]
    for outcome in failed:
        print(f"\n--- {outcome.case_id} ---", file=sys.stderr)
        print(outcome.detail, file=sys.stderr)

    summary = f"{sum(1 for o in outcomes if o.passed)}/{len(outcomes)} cases passed"
    if skipped:
        summary += f", {len(skipped)} skipped (prerequisite absent)"
    print(summary)
    return 1 if failed else 0


__all__ = ["CaseOutcome",
    "main",
    "run_cases",
    "verify_site_targets_exist",
    "verify_targets_exist",]
