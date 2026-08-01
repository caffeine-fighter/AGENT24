"""The eval registry must reject what used to pass silently (issue #107).

Before this, ``cases.yaml`` was read by nothing, so a duplicate id, a misspelled
field, or a pytest node that had been renamed all stayed green. These tests are
mostly *negative*: each one is a way the old file could rot without complaint.

The positive half is small on purpose -- the real registry is validated by
``python -m agent24.evals --validate-only`` in the canonical gate, and
duplicating those assertions here would just be a second place to update.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent24.evals.registry import (
    DEFAULT_REGISTRY_PATH,
    GymFixtureCase,
    PytestCase,
    Registry,
    RegistryError,
    RuntimeEventsCase,
    SiteCase,
    load_registry,
    parse_registry,
)
from agent24.evals.runner import verify_site_targets_exist, verify_targets_exist

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pytest_case(**overrides) -> dict:
    case = {
        "id": "example-case",
        "runner": "pytest",
        "expectation": "the thing holds",
        "targets": ["tests/evals/test_registry.py::test_the_shipped_registry_is_valid"],
    }
    case.update(overrides)
    return case


def _registry(*cases: dict) -> dict:
    return {"version": "agent24.evals.v1", "cases": list(cases)}


# --------------------------------------------------------------------------
# The shipped file
# --------------------------------------------------------------------------


def test_the_shipped_registry_is_valid() -> None:
    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)

    assert registry.version == "agent24.evals.v1"
    assert len(registry.cases) >= 1
    assert len(registry.by_id()) == len(registry.cases)


def test_every_shipped_pytest_target_exists() -> None:
    """The drift gate, run against the real registry.

    This is the assertion that would have caught a node renamed by an unrelated
    PR -- which is exactly what it caught the first time it ran.
    """

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)

    assert verify_targets_exist(registry, repo_root=REPO_ROOT) == []
    assert verify_site_targets_exist(registry, repo_root=REPO_ROOT) == []


def test_every_shipped_case_executes_something() -> None:
    """No case may be inert: the original complaint about this file."""

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    for case in registry.cases:
        if isinstance(case, PytestCase | SiteCase):
            assert case.targets, f"{case.id} declares no target"
        elif isinstance(case, GymFixtureCase):
            assert case.deterministic_load or case.fault_type or case.protected_replay
        elif isinstance(case, RuntimeEventsCase):
            # Since #120 both modes execute the declaration itself, so a live
            # case no longer needs a bound test to be non-inert.
            assert case.expected_event_types


# --------------------------------------------------------------------------
# Negative: the ways the old file could rot
# --------------------------------------------------------------------------


def test_duplicate_ids_are_rejected() -> None:
    payload = _registry(_pytest_case(), _pytest_case(expectation="a different claim"))

    with pytest.raises(RegistryError, match="duplicate case ids: example-case"):
        parse_registry(payload)


def test_an_unknown_field_is_rejected() -> None:
    """A misspelled key used to be a check that silently never ran."""

    with pytest.raises(RegistryError, match="expectaton|Extra inputs"):
        parse_registry(_registry(_pytest_case(expectaton="typo")))


def test_a_missing_required_field_is_rejected() -> None:
    case = _pytest_case()
    del case["expectation"]

    with pytest.raises(RegistryError, match="expectation"):
        parse_registry(_registry(case))


def test_an_unknown_runner_is_rejected() -> None:
    with pytest.raises(RegistryError, match="runner"):
        parse_registry(_registry(_pytest_case(runner="bash")))


def test_a_field_from_another_runner_is_rejected() -> None:
    """The 'conflicting field' case: a pytest case cannot carry fixture keys."""

    with pytest.raises(RegistryError, match="fixture_id|Extra inputs"):
        parse_registry(_registry(_pytest_case(fixture_id="life.cake_collision.v1")))


def test_an_unknown_schema_version_is_rejected() -> None:
    payload = _registry(_pytest_case())
    payload["version"] = "agent24.evals.v0"

    with pytest.raises(RegistryError, match="is not 'agent24.evals.v1'"):
        parse_registry(payload)


@pytest.mark.parametrize(
    "target",
    [
        "tests/unit/test_packs.py::test_x; rm -rf /",
        "tests/unit/test_packs.py::test_x && curl http://example.com",
        "tests/unit/test_packs.py::test_x | tee /tmp/out",
        "$(printf 'tests/unit/test_packs.py')",
        "`whoami`",
        "--collect-only",
        "-p no:cacheprovider",
        "../../etc/passwd",
        "tests/../../../etc/passwd::test_x",
    ],
)
def test_command_injection_shaped_targets_are_rejected(target: str) -> None:
    """The registry never builds a shell string, and cannot be made to.

    The runner passes a list argv with ``shell=False``, so these could not
    execute anyway; refusing them at load time means a reviewer sees the problem
    in the diff rather than trusting the runner to be careful.
    """

    with pytest.raises(RegistryError):
        parse_registry(_registry(_pytest_case(targets=[target])))


def test_a_flag_cannot_be_smuggled_through_a_proves_list() -> None:
    case = {
        "id": "gym-case",
        "runner": "gym_fixture",
        "expectation": "loads",
        "fixture_id": "life.cake_collision.v1",
        "seed": 42,
        "proves": ["-x"],
    }

    with pytest.raises(RegistryError):
        parse_registry(_registry(case))


def test_a_site_target_may_not_traverse_outside_site() -> None:
    case = {
        "id": "site-case",
        "runner": "site",
        "expectation": "hosted route holds",
        "targets": ["../../etc/passwd.mjs"],
    }

    with pytest.raises(RegistryError):
        parse_registry(_registry(case))


def test_a_missing_pytest_node_is_reported_as_drift() -> None:
    """A renamed test must fail the gate, not collect zero and pass."""

    registry = Registry.model_validate(
        _registry(
            _pytest_case(
                id="renamed-away",
                targets=["tests/evals/test_registry.py::test_this_name_does_not_exist"],
            )
        )
    )

    missing = verify_targets_exist(registry, repo_root=REPO_ROOT)

    assert len(missing) == 1
    assert "test_this_name_does_not_exist" in missing[0]
    assert "renamed-away" in missing[0]


def test_a_deleted_test_file_is_reported_without_running_pytest() -> None:
    registry = Registry.model_validate(
        _registry(
            _pytest_case(
                id="deleted-file",
                targets=["tests/unit/test_file_that_was_deleted.py::test_x"],
            )
        )
    )

    missing = verify_targets_exist(registry, repo_root=REPO_ROOT)

    assert len(missing) == 1
    assert "test_file_that_was_deleted.py" in missing[0]


def test_a_missing_site_target_is_reported_as_drift() -> None:
    registry = Registry.model_validate(
        _registry(
            {
                "id": "site-drift",
                "runner": "site",
                "expectation": "hosted route holds",
                "targets": ["tests/renamed-away.test.mjs"],
            }
        )
    )

    missing = verify_site_targets_exist(registry, repo_root=REPO_ROOT)

    assert len(missing) == 1
    assert "renamed-away" in missing[0]


def test_a_parametrized_target_is_not_reported_as_missing() -> None:
    """``test_x[param]`` collects; a declared bare ``test_x`` still counts."""

    registry = Registry.model_validate(
        _registry(
            _pytest_case(
                id="parametrized",
                targets=[
                    "tests/evals/test_registry.py"
                    "::test_command_injection_shaped_targets_are_rejected"
                ],
            )
        )
    )

    assert verify_targets_exist(registry, repo_root=REPO_ROOT) == []


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------


def test_selecting_an_unknown_case_id_is_an_error() -> None:
    registry = Registry.model_validate(_registry(_pytest_case()))

    with pytest.raises(RegistryError, match="unknown case id"):
        registry.select(["no-such-case"])


def test_selection_replays_in_registry_order() -> None:
    registry = Registry.model_validate(
        _registry(_pytest_case(id="first"), _pytest_case(id="second"))
    )

    assert [case.id for case in registry.select(["second", "first"])] == ["first", "second"]


# --------------------------------------------------------------------------
# Live cases execute their own declaration (issue #120)
# --------------------------------------------------------------------------


def _live_case(**overrides) -> RuntimeEventsCase:
    """The shipped live case, optionally mutated."""

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    shipped = registry.by_id()["live_tool_round_trip"]
    return shipped.model_copy(update=overrides)


def test_a_live_case_runs_the_mocked_app_instead_of_delegating() -> None:
    """The #120 regression: live mode used to return an unconditional pass.

    ``delegated_to_pytest`` was the whole result, so the declared input and
    event sequence never reached anything that ran.
    """

    from agent24.evals.harness import run_runtime_events_case

    result = run_runtime_events_case(_live_case())

    assert result.passed
    names = {check.name for check in result.checks}
    assert "delegated_to_pytest" not in names
    assert {"event_types", "run_id", "jsonl_payloads"} <= names
    assert len(result.checks) >= 8


def test_a_live_case_reports_which_fields_stayed_prose() -> None:
    from agent24.evals.harness import run_runtime_events_case

    result = run_runtime_events_case(_live_case())

    assert result.prose_only == ("expectation",)


@pytest.mark.parametrize(
    "mutation,failing_check",
    [
        ({"expected_event_types": ("run_started", "run_completed")}, "event_types"),
        ({"expected_event_types": ("run_started", "tool_call", "run_completed")}, "event_types"),
        ({"mode": "offline"}, "event_types"),
    ],
    ids=["truncated-sequence", "missing-middle-event", "wrong-mode"],
)
def test_mutating_a_live_declaration_changes_the_result(
    mutation: dict, failing_check: str
) -> None:
    """Drift proof: the declaration is what runs, so editing it must be visible.

    Before #120 every one of these still passed, because the bound test owned
    its own copy of the literals and the harness never read the case.
    """

    from agent24.evals.harness import run_runtime_events_case

    result = run_runtime_events_case(_live_case(**mutation))

    assert not result.passed
    assert failing_check in {check.name for check in result.failures()}


def test_the_declared_input_is_what_reaches_the_model() -> None:
    """The case's own ``input`` drives the run, not a literal in a bound test."""

    from agent24.evals.harness import run_runtime_events_case
    from agent24.evals.live_stub import StubOpenAIClient

    case = _live_case(input="a distinctive declared mission string")
    assert run_runtime_events_case(case).passed

    assert StubOpenAIClient.last is not None
    rendered = json.dumps(StubOpenAIClient.last.requests[0], default=str)
    assert "a distinctive declared mission string" in rendered


def test_a_mutated_expected_event_fails_the_whole_registry_run(tmp_path: Path) -> None:
    """End to end through the CLI, not just the harness function."""

    import yaml

    from agent24.evals.runner import main

    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    case = registry.by_id()["live_tool_round_trip"].model_dump(mode="json", exclude_none=True)
    case["expected_event_types"] = ["run_started", "run_completed"]
    case["proves"] = []
    path = tmp_path / "cases.yaml"
    path.write_text(
        yaml.safe_dump({"version": "agent24.evals.v1", "cases": [case]}, allow_unicode=True),
        encoding="utf-8",
    )

    assert main(["--registry", str(path)]) == 1


def test_forbid_in_stream_is_checked_against_the_persisted_jsonl_too() -> None:
    """A value scrubbed from SSE but left in the artifact has still leaked."""

    from agent24.evals.harness import run_runtime_events_case

    result = run_runtime_events_case(_live_case(forbid_in_stream=("Mock final diagnosis",)))

    failed = {check.name for check in result.failures()}
    assert "absent:Mock final diagnosis:sse" in failed
    assert "absent:Mock final diagnosis:jsonl" in failed


def test_the_live_stub_never_reaches_the_network() -> None:
    """The provider is production; only its transport constructor is replaced."""

    import agents.models.openai_provider as provider_module

    from agent24.evals.live_stub import StubOpenAIClient, mocked_openai_provider

    original = provider_module.AsyncOpenAI
    with mocked_openai_provider():
        assert provider_module.AsyncOpenAI is StubOpenAIClient
    assert provider_module.AsyncOpenAI is original


def test_the_live_stub_restores_the_ambient_environment(monkeypatch) -> None:
    """A pinned test key must not survive the context and leak into later runs."""

    from agent24.evals.live_stub import mocked_openai_provider

    monkeypatch.setenv("OPENAI_API_KEY", "ambient-value")
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    with mocked_openai_provider():
        assert os.environ["OPENAI_API_KEY"] == "test-only-key"

    assert os.environ["OPENAI_API_KEY"] == "ambient-value"
    assert "OPENAI_MODEL" not in os.environ


# --------------------------------------------------------------------------
# Runner exit codes -- the gate's actual contract
# --------------------------------------------------------------------------


def _write_registry(tmp_path: Path, payload: dict) -> Path:
    import yaml

    path = tmp_path / "cases.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_validate_only_succeeds_on_the_shipped_registry(capsys) -> None:
    from agent24.evals.runner import main

    assert main(["--validate-only"]) == 0
    assert "every declared pytest node collects" in capsys.readouterr().out


def test_drift_fails_the_gate_even_with_skip_unavailable(tmp_path: Path, capsys) -> None:
    """The load-bearing rule: drift is never downgradable to a skip."""

    from agent24.evals.runner import main

    registry = _write_registry(
        tmp_path,
        _registry(
            _pytest_case(
                id="drifted",
                targets=["tests/evals/test_registry.py::test_gone_missing"],
            )
        ),
    )

    code = main(["--registry", str(registry), "--validate-only", "--skip-unavailable"])

    assert code == 1
    assert "registry drift" in capsys.readouterr().err


def test_an_invalid_registry_exits_two_rather_than_running_anything(
    tmp_path: Path, capsys
) -> None:
    from agent24.evals.runner import main

    registry = _write_registry(tmp_path, _registry(_pytest_case(), _pytest_case()))

    assert main(["--registry", str(registry)]) == 2
    assert "duplicate case ids" in capsys.readouterr().err


def test_list_prints_every_case_and_its_runner(capsys) -> None:
    from agent24.evals.runner import main

    assert main(["--list"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    registry = load_registry(REPO_ROOT / DEFAULT_REGISTRY_PATH)
    assert len(lines) == len(registry.cases)
    assert lines[0].split("\t")[1] in {"pytest", "site", "gym_fixture", "runtime_events"}


def test_a_declarative_case_cannot_claim_a_protected_state_it_never_checks() -> None:
    """Prose describing a replay outcome must come with the checks that prove it."""

    case = {
        "id": "unbacked-claim",
        "runner": "gym_fixture",
        "expectation": "one charge survives",
        "fixture_id": "life.payment_intent_timeout.v1",
        "seed": 42,
        "expected_protected_state": "one charge, wallet spend 49000 KRW",
    }

    with pytest.raises(RegistryError, match="protected_replay"):
        parse_registry(_registry(case))
