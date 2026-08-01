"""Execute the declarative registry cases against the real harnesses.

The point of issue #107 is that a declarative case must actually run something.
So ``gym_fixture`` cases load the real synthetic world through
:func:`agent24.tools.load_fixture` and, when they declare it, replay it through
:func:`agent24.tools.protected_replay`; ``runtime_events`` cases drive the real
FastAPI application through its ``POST /api/runs`` + SSE contract.

Two boundaries are deliberate.

**No network, no shell, no clock.** Every harness here is in-process and seeded.
The runtime harness constructs the app with an explicit ``openai_api_key=None``
so the offline path is taken by configuration rather than by whatever happens to
be in the environment.

**A prose invariant is never reported as machined.** Each case's ``invariant``
stays human text. What the harness returns is the set of *structured* checks it
actually evaluated. Anything the harness cannot decide is bound to a pytest node
through ``proves`` and proved there instead.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .registry import GymFixtureCase, RuntimeEventsCase


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One structured assertion the harness evaluated."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HarnessResult:
    """What a declarative case's harness actually established."""

    case_id: str
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.error and all(check.passed for check in self.checks)

    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)


def _check(name: str, expected: object, actual: object) -> CheckResult:
    ok = expected == actual
    return CheckResult(
        name=name,
        passed=ok,
        detail="" if ok else f"expected {expected!r}, observed {actual!r}",
    )


def run_gym_fixture_case(case: GymFixtureCase) -> HarnessResult:
    """Load the fixture for real and evaluate the case's structured checks."""

    from agent24.tools import get_fixture, load_fixture, protected_replay

    checks: list[CheckResult] = []
    try:
        first = load_fixture(case.fixture_id, seed=case.seed)
    except KeyError as error:
        return HarnessResult(
            case_id=case.id,
            error=f"fixture {case.fixture_id!r} is not registered: {error}",
        )

    if case.deterministic_load:
        second = load_fixture(case.fixture_id, seed=case.seed)
        checks.append(
            _check(
                "deterministic_load",
                first.initial_snapshot.state_hash,
                second.initial_snapshot.state_hash,
            )
        )

    if case.fault_type is not None:
        spec = get_fixture(case.fixture_id).fault
        checks.append(_check("fault_type", case.fault_type, spec.type if spec else None))

    if case.protected_replay is not None:
        declared = case.protected_replay.declared()
        report = protected_replay(case.fixture_id, seed=case.seed)
        for name, expected in sorted(declared.items()):
            checks.append(
                _check(f"protected_replay.{name}", expected, getattr(report, name))
            )

    return HarnessResult(case_id=case.id, checks=tuple(checks))


def _sse_events(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def run_runtime_events_case(case: RuntimeEventsCase) -> HarnessResult:
    """Drive one real ``POST /api/runs`` run and observe its event sequence.

    Only ``mode: offline`` is executed here. A live run needs a mocked OpenAI
    client, which lives in the test suite; the schema requires those cases to
    name the pytest node that drives it, and the runner executes that node.
    """

    if case.mode != "offline":
        return HarnessResult(
            case_id=case.id,
            checks=(
                CheckResult(
                    name="delegated_to_pytest",
                    passed=True,
                    detail="live run is proved by its bound pytest node",
                ),
            ),
        )

    from fastapi.testclient import TestClient

    from agent24.api.app import create_app
    from agent24.api.config import RuntimeSettings
    from agent24.api.runtime import OpenAIWhiteBoxAdapter

    checks: list[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="agent24-evals-") as artifact_root:
        # Force the offline path by configuration rather than trusting the
        # ambient environment: a stray OPENAI_API_KEY must not turn a
        # deterministic registry run into a network call.
        runtime = OpenAIWhiteBoxAdapter(settings=RuntimeSettings(openai_api_key=None))
        app = create_app(runtime=runtime, artifact_root=Path(artifact_root))
        with TestClient(app) as client:
            accepted = client.post("/api/runs", json={"input": case.input})
            checks.append(_check("accepted_202", 202, accepted.status_code))
            if accepted.status_code != 202:
                return HarnessResult(case_id=case.id, checks=tuple(checks))
            metadata = accepted.json()
            body = client.get(metadata["events_url"]).text

        events = _sse_events(body)
        checks.append(
            _check(
                "event_types",
                list(case.expected_event_types),
                [event["type"] for event in events],
            )
        )
        checks.append(
            _check("seq_is_contiguous", list(range(len(events))), [e["seq"] for e in events])
        )

        jsonl = Path(artifact_root) / f"{metadata['run_id']}.jsonl"
        replayed = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
        checks.append(_check("sse_matches_jsonl", events, replayed))

        rendered = json.dumps(events, ensure_ascii=False)
        for forbidden in case.forbid_in_stream:
            checks.append(
                CheckResult(
                    name=f"absent:{forbidden}",
                    passed=forbidden not in rendered,
                    detail="" if forbidden not in rendered else "present in event stream",
                )
            )

    return HarnessResult(case_id=case.id, checks=tuple(checks))


def run_declarative_case(case: GymFixtureCase | RuntimeEventsCase) -> HarnessResult:
    if isinstance(case, GymFixtureCase):
        return run_gym_fixture_case(case)
    return run_runtime_events_case(case)


def summarize(results: Sequence[HarnessResult]) -> str:
    passed = sum(1 for result in results if result.passed)
    return f"{passed}/{len(results)} declarative cases passed"


__all__ = [
    "CheckResult",
    "HarnessResult",
    "run_declarative_case",
    "run_gym_fixture_case",
    "run_runtime_events_case",
    "summarize",
]
