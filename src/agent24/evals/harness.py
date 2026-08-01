"""Execute the declarative registry cases against the real harnesses.

The point of issue #107 is that a declarative case must actually run something.
So ``gym_fixture`` cases load the real synthetic world through
:func:`agent24.tools.load_fixture` and, when they declare it, replay it through
:func:`agent24.tools.protected_replay`; ``runtime_events`` cases drive the real
FastAPI application through its ``POST /api/runs`` + SSE contract; and
``target_runtime`` cases (issue #124) stage one reviewed Target Agent behaviour
against the real Agents SDK runner and sandbox, then machine-check the raw
target items, the controller ledger and the single terminal on both surfaces.

Two boundaries are deliberate.

**No network, no shell, no clock.** Every harness here is in-process and seeded.
An ``offline`` runtime case forces ``openai_api_key=None`` so the offline path is
taken by configuration rather than by whatever happens to be in the environment;
a ``live`` case runs the production provider, runner and event path against the
stubbed transport in :mod:`agent24.evals.live_stub`.

**The declaration is what runs.** Both runtime modes post the case's own
``input`` and compare against the case's own ``expected_event_types`` and
``forbid_in_stream``. Issue #120 existed because ``live`` cases used to return
an unconditional pass and delegate to a bound test that owned a second copy of
those literals -- editing the declaration changed nothing.

**A prose invariant is never reported as machined.** What the harness returns is
the set of *structured* checks it actually evaluated, plus ``prose_only`` naming
the human-readable fields it did not. Anything the harness cannot decide is
bound to a pytest node through ``proves`` and proved there instead.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .registry import GymFixtureCase, RuntimeEventsCase, TargetRuntimeCase


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One structured assertion the harness evaluated."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class HarnessResult:
    """What a declarative case's harness actually established.

    ``prose_only`` names the declared fields the harness did *not* machine-check
    -- the human-readable claims. Keeping them in the result, rather than
    dropping them, is what lets the runner say "6 checked, 1 prose-only" instead
    of implying the whole case was verified mechanically.
    """

    case_id: str
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)
    prose_only: tuple[str, ...] = field(default_factory=tuple)
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

    prose: list[str] = ["expectation"]
    if case.expected_protected_state:
        # A human-readable claim. The structured protected_replay flags above
        # are what actually ran; saying so keeps the two apart in the report.
        prose.append("expected_protected_state")
    return HarnessResult(case_id=case.id, checks=tuple(checks), prose_only=tuple(prose))


def _sse_events(body: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@contextmanager
def _runtime_for(mode: str) -> Iterator[object]:
    """Build the adapter for a declared mode, with no socket in either case.

    ``offline`` forces ``openai_api_key=None`` so a stray ambient key cannot
    turn a deterministic registry run into a network call. ``live`` exercises
    the real provider, runner and event path against a stubbed transport.
    """

    from agent24.api.config import RuntimeSettings
    from agent24.api.runtime import OpenAIWhiteBoxAdapter

    if mode == "offline":
        yield OpenAIWhiteBoxAdapter(settings=RuntimeSettings(openai_api_key=None))
        return

    from .live_stub import TEST_API_KEY, mocked_openai_provider

    with mocked_openai_provider():
        yield OpenAIWhiteBoxAdapter(settings=RuntimeSettings(openai_api_key=TEST_API_KEY))


def run_runtime_events_case(case: RuntimeEventsCase) -> HarnessResult:
    """Drive one real ``POST /api/runs`` run and observe its event sequence.

    Both modes execute the *declared* case: the input the case names is the
    input that is posted, and the events observed are compared with the case's
    own ``expected_event_types``. Before #120 a ``live`` case returned an
    unconditional pass and delegated to a bound test that owned its own copy of
    those literals, so editing the declaration changed nothing.
    """

    from fastapi.testclient import TestClient

    from agent24.api.app import create_app

    checks: list[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="agent24-evals-") as artifact_root:
        root = Path(artifact_root)
        with _runtime_for(case.mode) as runtime:
            app = create_app(runtime=runtime, artifact_root=root)
            with TestClient(app) as client:
                accepted = client.post("/api/runs", json={"input": case.input})
                checks.append(_check("accepted_202", 202, accepted.status_code))
                if accepted.status_code != 202:
                    return HarnessResult(case_id=case.id, checks=tuple(checks))
                metadata = accepted.json()
                run_id = metadata["run_id"]
                body = client.get(metadata["events_url"]).text

        events = _sse_events(body)
        checks.append(
            _check("mode", case.mode, "offline" if metadata["mode"] == "offline_demo" else "live")
        )
        checks.append(
            _check(
                "event_types",
                list(case.expected_event_types),
                [event["type"] for event in events],
            )
        )

        # SSE and JSONL are compared field by field rather than as one blob, so
        # a failure says which of run id, ordering or payload diverged.
        jsonl = root / f"{run_id}.jsonl"
        replayed = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
        checks.append(_check("run_id", {run_id}, {event["run_id"] for event in events}))
        checks.append(
            _check("seq_is_contiguous", list(range(len(events))), [e["seq"] for e in events])
        )
        checks.append(
            _check("jsonl_seq", [e["seq"] for e in events], [e["seq"] for e in replayed])
        )
        checks.append(
            _check("jsonl_run_id", {run_id}, {event["run_id"] for event in replayed})
        )
        checks.append(
            _check(
                "jsonl_payloads",
                [event["payload"] for event in events],
                [event["payload"] for event in replayed],
            )
        )

        # Applied to both surfaces: a value scrubbed from the live stream but
        # left in the persisted artifact has still leaked.
        surfaces = {
            "sse": json.dumps(events, ensure_ascii=False),
            "jsonl": jsonl.read_text(encoding="utf-8"),
        }
        for forbidden in case.forbid_in_stream:
            for surface, rendered in surfaces.items():
                absent = forbidden not in rendered
                checks.append(
                    CheckResult(
                        name=f"absent:{forbidden}:{surface}",
                        passed=absent,
                        detail="" if absent else f"present in {surface}",
                    )
                )

    return HarnessResult(
        case_id=case.id,
        checks=tuple(checks),
        prose_only=("expectation",),
    )


def _is_ordered_subsequence(required: Sequence[str], observed: Sequence[str]) -> str:
    """Return "" when every required type appears, in order, in ``observed``.

    A subsequence rather than an exact match: the target path publishes tens of
    events and pinning all of them would turn any unrelated instrumentation
    change into a false failure. What the case claims -- these happened, in this
    order -- is exactly what is checked.
    """

    remaining = list(observed)
    for wanted in required:
        try:
            remaining = remaining[remaining.index(wanted) + 1 :]
        except ValueError:
            return f"{wanted!r} did not occur after the preceding required events"
    return ""


def _terminal_events(events: Sequence[dict]) -> list[dict]:
    return [event for event in events if event["type"] == "run_completed"]


def _target_checks(
    case: TargetRuntimeCase, events: Sequence[dict]
) -> list[CheckResult]:
    """Machine-check the raw target items and the controller-owned ledger."""

    expectation = case.target
    checks: list[CheckResult] = []
    if expectation is None:
        return checks

    if expectation.tool_calls is not None:
        checks.append(
            _check(
                "target.tool_call_count",
                expectation.tool_calls,
                sum(event["type"] == "target.tool_call" for event in events),
            )
        )
    if expectation.tool_results is not None:
        checks.append(
            _check(
                "target.tool_result_count",
                expectation.tool_results,
                sum(event["type"] == "target.tool_result" for event in events),
            )
        )

    if not expectation.oracle_fields and not expectation.provenance_fields:
        return checks

    oracle = next(
        (event["payload"] for event in events if event["type"] == "target.oracle"),
        None,
    )
    if oracle is None:
        checks.append(
            CheckResult(
                name="target.oracle",
                passed=False,
                detail="the run published no target.oracle event",
            )
        )
        return checks

    protected = oracle.get("protected_replay", {})
    observed = {
        "oracle_charge_count": oracle.get("charge_count"),
        "oracle_spend_krw": oracle.get("spend_krw"),
        "oracle_violations": tuple(oracle.get("violations", ())),
        "protected_charge_count": protected.get("charge_count"),
        "protected_spend_krw": protected.get("spend_krw"),
        "protected_accepted": protected.get("accepted"),
        "protected_mission_succeeded": protected.get("mission_succeeded"),
    }
    for name, expected in sorted(expectation.oracle_fields.items()):
        checks.append(_check(f"target.{name}", expected, observed[name]))

    # Provenance is what lets a reader re-derive the run: without it the ledger
    # numbers are unattributable to a source, manifest or prompt.
    for name in expectation.provenance_fields:
        value = oracle.get(name)
        present = isinstance(value, str) and bool(value.strip())
        checks.append(
            CheckResult(
                name=f"target.provenance.{name}",
                passed=present,
                detail="" if present else f"missing or empty in target.oracle: {value!r}",
            )
        )
    return checks


def run_target_runtime_case(case: TargetRuntimeCase) -> HarnessResult:
    """Stage one reviewed-target scenario and observe both event surfaces.

    The declaration is what runs: the case names the target behaviour to stage,
    the input to post, the events that must occur in order, the values that must
    never appear, and the terminal it expects -- and every one of those is
    compared against a real ``POST /api/runs`` run of the production adapter.
    There is no delegated pass; a case that names a bound pytest node in
    ``proves`` still executes here first.
    """

    from fastapi.testclient import TestClient

    from agent24.api.app import create_app
    from agent24.api.config import RuntimeSettings
    from agent24.api.runtime import OpenAIWhiteBoxAdapter

    from .target_stub import client_for, preflight_for, target_payload

    checks: list[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="agent24-target-") as artifact_root:
        root = Path(artifact_root)
        runtime = OpenAIWhiteBoxAdapter(
            preflight=preflight_for(case.scenario),
            openai_client=client_for(case.scenario),
            # The injected client is the only provider. Forcing the setting to
            # None keeps an ambient OPENAI_API_KEY from deciding anything.
            settings=RuntimeSettings(openai_api_key=None),
        )
        app = create_app(runtime=runtime, artifact_root=root)
        with TestClient(app) as client:
            accepted = client.post(
                "/api/runs",
                json={
                    "input": case.input,
                    "target": target_payload(case.scenario, mission=case.mission),
                },
            )
            checks.append(_check("accepted_202", 202, accepted.status_code))
            if accepted.status_code != 202:
                return HarnessResult(case_id=case.id, checks=tuple(checks))
            metadata = accepted.json()
            run_id = metadata["run_id"]
            body = client.get(metadata["events_url"]).text

        events = _sse_events(body)
        jsonl = root / f"{run_id}.jsonl"
        replayed = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]

        observed_types = [event["type"] for event in events]
        order_failure = _is_ordered_subsequence(case.required_event_order, observed_types)
        checks.append(
            CheckResult(
                name="required_event_order",
                passed=not order_failure,
                detail=order_failure,
            )
        )
        for forbidden in case.forbidden_event_types:
            absent = forbidden not in observed_types
            checks.append(
                CheckResult(
                    name=f"no_event:{forbidden}",
                    passed=absent,
                    detail="" if absent else "the run published this event type",
                )
            )

        # Exactly one terminal, on both surfaces, and it is the last event.
        # "Exactly one" is the honesty property: a second run_completed would
        # let a reader pick whichever verdict they preferred.
        for surface, stream in (("sse", events), ("jsonl", replayed)):
            terminals = _terminal_events(stream)
            checks.append(_check(f"one_terminal:{surface}", 1, len(terminals)))
            checks.append(
                _check(
                    f"terminal_is_last:{surface}",
                    "run_completed",
                    stream[-1]["type"] if stream else None,
                )
            )

        checks.append(_check("sse_matches_jsonl", events, replayed))

        terminal = events[-1]["payload"] if events else {}
        for name, expected in sorted(case.terminal.declared().items()):
            checks.append(_check(f"terminal.{name}", expected, terminal.get(name)))

        checks.extend(_target_checks(case, events))

        surfaces = {
            "sse": json.dumps(events, ensure_ascii=False),
            "jsonl": jsonl.read_text(encoding="utf-8"),
        }
        for forbidden in case.forbid_in_stream:
            for surface, rendered in surfaces.items():
                absent = forbidden not in rendered
                checks.append(
                    CheckResult(
                        name=f"absent:{forbidden}:{surface}",
                        passed=absent,
                        detail="" if absent else f"present in {surface}",
                    )
                )

    return HarnessResult(case_id=case.id, checks=tuple(checks), prose_only=("expectation",))


def run_declarative_case(
    case: GymFixtureCase | RuntimeEventsCase | TargetRuntimeCase,
) -> HarnessResult:
    if isinstance(case, GymFixtureCase):
        return run_gym_fixture_case(case)
    if isinstance(case, TargetRuntimeCase):
        return run_target_runtime_case(case)
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
    "run_target_runtime_case",
    "summarize",
]
