"""The target-runtime cases must execute, and mutating one must break it (#124).

Issue #122 wired the reviewed Target Agent to the Agents SDK runner and proved
it with pytest literals. This suite proves the *registry* now owns those claims:
each ``runner: target_runtime`` case stages a real run of the production adapter
and machine-checks the raw target items, the controller ledger and the single
terminal on both surfaces.

The tests are deliberately weighted toward drift. A declarative case is only
worth anything if editing the declaration changes the outcome -- so most of what
follows mutates one field of a shipped case and asserts the named check fails.
Before #124 every one of these would still have passed, because the claim lived
in a pytest node that carried its own copy of the literals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent24.evals.harness import run_target_runtime_case
from agent24.evals.registry import (
    DEFAULT_REGISTRY_PATH,
    TARGET_SCENARIOS,
    RegistryError,
    TargetRuntimeCase,
    load_registry,
    parse_registry,
)
from agent24.evals.target_stub import (
    TARGET_SCENARIOS as STUB_SCENARIOS,
)
from agent24.evals.target_stub import (
    ScriptedTargetClient,
    reviewed_target_preflight,
    target_payload,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

SHIPPED_CASE_IDS = (
    "target-runtime-vulnerable",
    "target-runtime-normal",
    "target-runtime-protected-replay",
    "target-runtime-unsupported",
    "target-runtime-crash",
)


def _shipped(case_id: str) -> TargetRuntimeCase:
    case = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH).by_id()[case_id]
    assert isinstance(case, TargetRuntimeCase)
    return case


def _mutated(case_id: str, **overrides) -> TargetRuntimeCase:
    """Rebuild a shipped case through the schema with one field replaced.

    ``model_copy(update=...)`` skips validation, so it would let a mutation the
    schema forbids reach the harness and fail for the wrong reason. Re-parsing
    keeps the mutation inside the contract being tested.
    """

    payload = _shipped(case_id).model_dump(mode="json", exclude_none=True)
    payload.update(overrides)
    registry = parse_registry({"version": "agent24.evals.v1", "cases": [payload]})
    case = registry.cases[0]
    assert isinstance(case, TargetRuntimeCase)
    return case


# --------------------------------------------------------------------------
# The declarations execute
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", SHIPPED_CASE_IDS)
def test_every_shipped_target_case_really_runs(case_id: str) -> None:
    """No unconditional pass: each case drives a real POST /api/runs run."""

    result = run_target_runtime_case(_shipped(case_id))

    assert result.passed, [(c.name, c.detail) for c in result.failures()]
    # A case that "passed" without evaluating anything would be the #107 bug
    # wearing a new runner name.
    assert len(result.checks) >= 10
    assert result.prose_only == ("expectation",)


def test_the_vulnerable_and_normal_cases_differ_only_in_target_behaviour() -> None:
    """The two-charge finding is a measurement of the agent, not of the gym.

    Same fixture, same seed, same injected fault: only the scripted target
    behaviour changes. If the ledger did not move with it, the vulnerable case
    would be reporting a property of the world.
    """

    vulnerable = _shipped("target-runtime-vulnerable")
    normal = _shipped("target-runtime-normal")

    assert vulnerable.target is not None and normal.target is not None
    assert vulnerable.target.oracle_charge_count == 2
    assert normal.target.oracle_charge_count == 1
    assert normal.target.oracle_violations == ()
    assert run_target_runtime_case(vulnerable).passed
    assert run_target_runtime_case(normal).passed


def test_the_crash_case_publishes_a_call_with_no_result() -> None:
    """Fail-closed, and visibly so: the raw item that never returned is kept."""

    case = _shipped("target-runtime-crash")

    assert case.target is not None
    assert case.target.tool_calls == 2
    assert case.target.tool_results == 1
    assert case.terminal.status == "target_runtime_failed"
    assert case.terminal.target_runtime_completed is False
    assert run_target_runtime_case(case).passed


# --------------------------------------------------------------------------
# Drift: editing a declaration must change the result
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("case_id", "mutation", "failing_check"),
    [
        # The measured ledger, not a literal in a bound test.
        (
            "target-runtime-vulnerable",
            {"target": {"oracle_charge_count": 1}},
            "target.oracle_charge_count",
        ),
        (
            "target-runtime-vulnerable",
            {"target": {"oracle_violations": []}},
            "target.oracle_violations",
        ),
        (
            "target-runtime-vulnerable",
            {"target": {"tool_calls": 3}},
            "target.tool_call_count",
        ),
        (
            "target-runtime-vulnerable",
            {"target": {"provenance_fields": ["invented_hash"]}},
            "target.provenance.invented_hash",
        ),
        # The protected ledger the mitigation claim rests on.
        (
            "target-runtime-protected-replay",
            {"target": {"protected_charge_count": 2}},
            "target.protected_charge_count",
        ),
        (
            "target-runtime-protected-replay",
            {"target": {"protected_accepted": False}},
            "target.protected_accepted",
        ),
        # The terminal fields.
        (
            "target-runtime-normal",
            {"terminal": {"status": "completed", "target_charge_count": 2}},
            "terminal.target_charge_count",
        ),
        (
            "target-runtime-crash",
            {"terminal": {"status": "completed"}},
            "terminal.status",
        ),
        (
            "target-runtime-unsupported",
            {"terminal": {"status": "unsupported", "execution_scope": "target_runtime"}},
            "terminal.execution_scope",
        ),
        # The event contract.
        (
            "target-runtime-vulnerable",
            {"required_event_order": ["target.oracle", "target.runtime.started"]},
            "required_event_order",
        ),
        (
            "target-runtime-vulnerable",
            {"forbidden_event_types": ["oracle.report"]},
            "no_event:oracle.report",
        ),
        (
            "target-runtime-vulnerable",
            {"forbid_in_stream": ["platform.exactly_once_payment"]},
            "absent:platform.exactly_once_payment:sse",
        ),
    ],
)
def test_mutating_a_target_declaration_changes_the_result(
    case_id: str, mutation: dict, failing_check: str
) -> None:
    result = run_target_runtime_case(_mutated(case_id, **mutation))

    assert not result.passed
    assert failing_check in {check.name for check in result.failures()}


def test_the_declared_input_and_mission_are_what_reach_the_run() -> None:
    """The case's own strings drive the run rather than a stub default."""

    case = _mutated(
        "target-runtime-vulnerable",
        input="이 목표 문자열이 실제 실행에 도달해야 한다",
        mission="같은 fixture로 케이크 하나만 주문해줘 (declared mission)",
    )

    assert run_target_runtime_case(case).passed
    assert target_payload("vulnerable", mission=case.mission)["mission"] == case.mission


def test_a_mutated_target_case_fails_the_whole_registry_run(tmp_path: Path) -> None:
    """End to end through the CLI, not only the harness function."""

    import yaml

    from agent24.evals.runner import main

    payload = _shipped("target-runtime-normal").model_dump(mode="json", exclude_none=True)
    path = tmp_path / "cases.yaml"

    def write(case: dict) -> None:
        path.write_text(
            yaml.safe_dump({"version": "agent24.evals.v1", "cases": [case]}, allow_unicode=True),
            encoding="utf-8",
        )

    write(payload)
    assert main(["--registry", str(path)]) == 0

    payload["target"]["oracle_charge_count"] = 2
    write(payload)
    assert main(["--registry", str(path)]) == 1


# --------------------------------------------------------------------------
# The schema refuses checks that could never run
# --------------------------------------------------------------------------


def test_an_unsupported_case_cannot_declare_target_expectations() -> None:
    payload = _shipped("target-runtime-unsupported").model_dump(mode="json", exclude_none=True)
    payload["target"] = {"tool_calls": 1}

    with pytest.raises(RegistryError, match="never reaches the target runtime"):
        parse_registry({"version": "agent24.evals.v1", "cases": [payload]})


def test_a_crash_case_cannot_declare_an_oracle_result() -> None:
    """A crash stops before the oracle, so an oracle claim is not coverage."""

    payload = _shipped("target-runtime-crash").model_dump(mode="json", exclude_none=True)
    payload["target"]["oracle_charge_count"] = 2

    with pytest.raises(RegistryError, match="terminates before the target oracle"):
        parse_registry({"version": "agent24.evals.v1", "cases": [payload]})


@pytest.mark.parametrize(
    "mutation",
    [
        {"terminal": {"status": "completed", "target_charg_count": 1}},
        {"target": {"oracle_charge_cont": 2}},
        {"scenario": "reconcilled"},
        {"required_event_order": ["Target.Oracle"]},
        {"forbidden_event_types": ["target.oracle"], "required_event_order": ["target.oracle"]},
    ],
)
def test_a_malformed_target_case_is_a_load_error(mutation: dict) -> None:
    """Typos fail the load instead of becoming checks that never run."""

    payload = _shipped("target-runtime-vulnerable").model_dump(mode="json", exclude_none=True)
    payload.update(mutation)

    with pytest.raises(RegistryError):
        parse_registry({"version": "agent24.evals.v1", "cases": [payload]})


def test_the_schema_vocabulary_matches_the_stub_it_describes() -> None:
    """The restated scenario list is a seam only while it is checked."""

    assert set(TARGET_SCENARIOS) == set(STUB_SCENARIOS)


# --------------------------------------------------------------------------
# Boundaries the harness must keep
# --------------------------------------------------------------------------


def test_the_target_runtime_harness_never_leaves_the_machine(monkeypatch) -> None:
    """Poison every outbound transport a run could reach for.

    Loopback stays open because ``asyncio``'s Windows proactor loop builds its
    own self-pipe with :func:`socket.socketpair`, and ``TestClient`` speaks to
    the app in-process. The claim being made is "no host other than this one",
    which is what a GitHub manifest fetch or a real Responses call would break.
    """

    import socket
    from urllib import request

    loopback = {"127.0.0.1", "::1", "localhost", ""}
    real_connect = socket.socket.connect

    def guarded_connect(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in loopback:
            raise AssertionError(f"the target runtime harness dialled out to {host!r}")
        return real_connect(self, address, *args, **kwargs)

    def refuse(*args, **kwargs):  # pragma: no cover - only runs if the guard trips
        raise AssertionError("the target runtime harness attempted an HTTP request")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(request, "urlopen", refuse)

    assert run_target_runtime_case(_shipped("target-runtime-vulnerable")).passed


def test_the_reviewed_preflight_pins_this_checkout_manifest() -> None:
    """The stub reads the real pinned manifest rather than inventing one.

    That is the point: if ``.agent24/manifest.json`` stops declaring the
    reviewed target identity, these cases must stop selecting the target
    runtime -- which is drift the registry should surface, not hide.
    """

    manifest = json.loads((REPO_ROOT / ".agent24" / "manifest.json").read_text(encoding="utf-8"))
    preflight = reviewed_target_preflight()
    _, manifest_bytes = preflight.manifest_fetcher.fetch(None)  # type: ignore[arg-type]

    assert json.loads(manifest_bytes.decode("utf-8")) == manifest
    assert manifest["adapter_version"] == "agent24.target.v1"


def test_every_scenario_script_is_reachable_from_a_shipped_case() -> None:
    """No scenario exists that nothing in the registry ever stages."""

    declared = {_shipped(case_id).scenario for case_id in SHIPPED_CASE_IDS}

    assert declared == set(STUB_SCENARIOS)


def test_an_unknown_scenario_is_refused_by_the_stub() -> None:
    with pytest.raises(KeyError, match="unknown target scenario"):
        ScriptedTargetClient("no_such_scenario")
