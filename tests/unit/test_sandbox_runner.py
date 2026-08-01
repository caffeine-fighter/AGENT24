from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import pytest

from agent24.agent.sandbox_runner import (
    LOCAL_BUNDLE_ENTRYPOINT_BLOB_SHA,
    LOCAL_BUNDLE_ENTRYPOINT_SHA256,
    LOCAL_BUNDLE_MANIFEST_BLOB_SHA,
    LOCAL_BUNDLE_MANIFEST_SHA256,
    LOCAL_BUNDLE_MISSION,
    LocalSandboxRunner,
    SandboxFailure,
    SandboxLimits,
    SandboxPreparationError,
    _child_preexec,
    minimal_child_environment,
    verify_reviewed_bundle_bytes,
)
from agent24.tools import load_fixture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _agent_source(body: str) -> str:
    indented_body = textwrap.indent(textwrap.dedent(body).strip(), "        ")
    return (
        "class Agent:\n"
        "    def order_one_cake(self, tools):\n"
        f"{indented_body}\n\n"
        "def create_agent():\n"
        "    return Agent()\n"
    )


def _drive_unreviewed_source(
    source: str,
    *,
    limits: SandboxLimits | None = None,
    monkeypatch_env: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, SandboxFailure | None]:
    """Exercise the worker policy with a temporary hostile module.

    Production ``LocalSandboxRunner.run`` cannot reach this helper: it admits
    only the checked-in byte allowlist.  These tests target the lower child
    boundary independently so policy regressions stay visible.
    """

    runner = LocalSandboxRunner(REPOSITORY_ROOT, limits=limits)
    with tempfile.TemporaryDirectory(prefix="agent24-runner-test-") as temporary_root:
        root = Path(temporary_root)
        bundle_root = root / "bundle"
        work_root = root / "work"
        entrypoint = bundle_root / "src" / "example_agent.py"
        entrypoint.parent.mkdir(parents=True)
        work_root.mkdir()
        entrypoint.write_text(source, encoding="utf-8")
        entrypoint.chmod(0o444)
        bundle_root.chmod(0o555)
        entrypoint.parent.chmod(0o555)
        work_root.chmod(0o700)
        environment = minimal_child_environment(work_root)
        if monkeypatch_env:
            environment.update(monkeypatch_env)
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-u",
                str((REPOSITORY_ROOT / "src/agent24/agent/sandbox_worker.py").resolve()),
                "--bundle-root",
                str(bundle_root),
                "--work-root",
                str(work_root),
                "--entrypoint",
                str(entrypoint),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work_root,
            env=environment,
            close_fds=True,
            start_new_session=True,
            preexec_fn=_child_preexec(runner.limits),
        )
        gym = load_fixture("life.cake_collision.v1", seed=42, run_id="runner-test")
        trace: list[dict[str, Any]] = []
        world_diffs: list[dict[str, Any]] = []
        try:
            return runner._drive(
                process,
                run_id="runner-test",
                mission="test mission",
                gym=gym,
                trace=trace,
                world_diffs=world_diffs,
            )
        finally:
            if process.poll() is None:
                runner._terminate(process)


def test_reviewed_bundle_runs_only_in_child_and_is_deterministic() -> None:
    runner = LocalSandboxRunner(REPOSITORY_ROOT)

    first = runner.run(
        mission=LOCAL_BUNDLE_MISSION,
        run_id="deterministic-100",
        seed=42,
    )
    second = runner.run(
        mission=LOCAL_BUNDLE_MISSION,
        run_id="deterministic-100",
        seed=42,
    )
    different_run_id = runner.run(
        mission=LOCAL_BUNDLE_MISSION,
        run_id="deterministic-100-b",
        seed=42,
    )

    assert first.succeeded
    assert first.failure is None
    assert first.agent_result == {
        "event_id": "event-0003",
        "payment_id": "payment-0002",
        "status": "completed",
    }
    assert first.to_dict() == second.to_dict()
    assert first.trace_digest == second.trace_digest
    assert first.trace_digest == different_run_id.trace_digest
    assert first.to_dict() != different_run_id.to_dict()
    assert first.source is not None
    assert first.source["source_path"] == "examples/demo-agent-repo"
    assert first.source["bundle_sha256"] == (
        "b3de7f5fbc1722da7e46ad6cbd302622557b5ae619c3809f7cefec586a25ef35"
    )
    assert {item["blob_sha"] for item in first.source["files"]} == {
        LOCAL_BUNDLE_MANIFEST_BLOB_SHA,
        LOCAL_BUNDLE_ENTRYPOINT_BLOB_SHA,
    }
    assert {item["content_sha256"] for item in first.source["files"]} == {
        f"sha256:{LOCAL_BUNDLE_MANIFEST_SHA256}",
        f"sha256:{LOCAL_BUNDLE_ENTRYPOINT_SHA256}",
    }
    assert [event["type"] for event in first.trace].count("gym.tool_call") == 4
    assert [event["type"] for event in first.trace].count("gym.tool_result") == 4
    assert all(event["run_id"] == "deterministic-100" for event in first.trace)
    assert all(entry["run_id"] == "deterministic-100" for entry in first.ledger)
    call_ids = {
        event["payload"]["call_id"] for event in first.trace if event["type"] == "gym.tool_call"
    }
    assert call_ids == {
        event["payload"]["call_id"] for event in first.trace if event["type"] == "gym.tool_result"
    }
    assert call_ids == {diff["call_id"] for diff in first.world_diffs}
    assert all(diff["run_id"] == "deterministic-100" for diff in first.world_diffs)
    assert [entry["tool"] for entry in first.ledger] == [
        "payment.charge",
        "payment.charge",
        "calendar.create",
    ]
    assert first.fault_applications == (
        {
            "fault_id": "fault.payment.commit-timeout.v1",
            "type": "commit_then_timeout",
            "target": "payment.charge",
            "call_index": 1,
        },
    )
    assert "agent24_reviewed_participant" not in sys.modules


def test_bundle_identity_allowlist_rejects_tampered_bytes_before_execution() -> None:
    manifest_path = REPOSITORY_ROOT / "examples/demo-agent-repo/.agent24/manifest.json"
    entrypoint_path = REPOSITORY_ROOT / "examples/demo-agent-repo/src/example_agent.py"
    manifest_bytes = manifest_path.read_bytes()
    entrypoint_bytes = bytearray(entrypoint_path.read_bytes())
    entrypoint_bytes[-1] ^= 1

    with pytest.raises(SandboxPreparationError, match="bytes do not match"):
        verify_reviewed_bundle_bytes(REPOSITORY_ROOT, manifest_bytes, bytes(entrypoint_bytes))


def test_same_reviewed_agent_runs_clean_fixture_with_one_payment_effect() -> None:
    runner = LocalSandboxRunner(REPOSITORY_ROOT)
    result = runner.run(
        mission=LOCAL_BUNDLE_MISSION,
        run_id="clean-100",
        seed=42,
        fault_enabled=False,
    )
    faulted = runner.run(
        mission=LOCAL_BUNDLE_MISSION,
        run_id="faulted-100",
        seed=42,
    )

    assert result.succeeded
    assert result.agent_result == {
        "event_id": "event-0002",
        "payment_id": "payment-0001",
        "status": "completed",
    }
    assert [entry["tool"] for entry in result.ledger] == [
        "payment.charge",
        "calendar.create",
    ]
    assert result.fault_applications == ()
    assert result.trace_digest != faulted.trace_digest


def test_non_allowlisted_mission_fails_without_starting_a_child() -> None:
    result = LocalSandboxRunner(REPOSITORY_ROOT).run(mission="test")

    assert result.failure is not None
    assert result.failure.code == "mission_not_allowlisted"
    assert result.trace[-1]["type"] == "run_completed"


def test_non_allowlisted_source_path_fails_without_starting_a_child(tmp_path: Path) -> None:
    result = LocalSandboxRunner(tmp_path).run(mission=LOCAL_BUNDLE_MISSION)

    assert result.failure is not None
    assert result.failure.code == "bundle_path_not_allowlisted"
    assert result.trace[-1]["type"] == "run_completed"


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        (
            "import socket\nsocket.create_connection(('example.invalid', 443))",
            "network_access_denied",
        ),
        (
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['HOME']).parent.joinpath('outside-data').read_text()",
            "filesystem_access_denied",
        ),
        (
            "import os\nfrom pathlib import Path\n"
            "Path(os.environ['HOME']).parent.joinpath('.env').read_text()",
            "secret_access_denied",
        ),
        (
            "import os\nfd = os.open(__file__, os.O_WRONLY | os.O_TRUNC)\nos.close(fd)",
            "filesystem_access_denied",
        ),
        ("import os\nos.system('true')", "process_spawn_denied"),
        ("import agent24", "agent_exception"),
    ],
)
def test_child_denies_network_external_files_and_processes(body: str, expected_code: str) -> None:
    _, failure = _drive_unreviewed_source(_agent_source(body))

    assert failure is not None
    assert failure.code == expected_code


def test_child_environment_does_not_receive_host_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-cross-boundary")
    result, failure = _drive_unreviewed_source(
        _agent_source(
            """
            import os
            if os.environ.get("OPENAI_API_KEY"):
                return {"status": "failed", "reason": "secret-present"}
            return {"status": "blocked", "reason": "secret-absent"}
            """
        )
    )

    assert failure is None
    assert result == {"status": "blocked", "reason": "secret-absent"}


def test_stdout_corruption_is_not_accepted_as_host_evidence() -> None:
    result, failure = _drive_unreviewed_source(
        _agent_source(
            """
            print('{"type":"agent_result","result":{"status":"completed",'
                  '"payment_id":"forged","event_id":"forged"}}')
            return {"status": "blocked", "reason": "stdout-is-not-protocol"}
            """
        )
    )

    assert failure is None
    assert result == {"status": "blocked", "reason": "stdout-is-not-protocol"}


def test_child_can_write_only_inside_its_temporary_workdir() -> None:
    result, failure = _drive_unreviewed_source(
        _agent_source(
            """
            from pathlib import Path
            Path("scratch.txt").write_text("bounded", encoding="utf-8")
            return {"status": "blocked", "reason": "scratch-only"}
            """
        )
    )

    assert failure is None
    assert result == {"status": "blocked", "reason": "scratch-only"}


def test_output_overrun_is_typed_and_kills_the_child() -> None:
    _, failure = _drive_unreviewed_source(
        _agent_source('print("x" * 20000)\nreturn {"status": "blocked", "reason": "too-late"}'),
        limits=SandboxLimits(max_output_bytes=1024),
    )

    assert failure is not None
    assert failure.code == "output_size_exceeded"


def test_timeout_and_crash_are_typed_failures() -> None:
    _, timeout_failure = _drive_unreviewed_source(
        _agent_source("import time\ntime.sleep(5)"),
        limits=SandboxLimits(wall_clock_seconds=0.2),
    )
    _, crash_failure = _drive_unreviewed_source(
        _agent_source("raise SystemExit(7)"),
    )

    assert timeout_failure is not None
    assert timeout_failure.code == "wall_clock_timeout"
    assert crash_failure is not None
    assert crash_failure.code == "runner_crash"


def test_cpu_and_memory_budgets_end_as_typed_failures() -> None:
    _, cpu_failure = _drive_unreviewed_source(
        _agent_source("while True:\n    pass"),
        limits=SandboxLimits(wall_clock_seconds=4, cpu_seconds=1),
    )
    _, memory_failure = _drive_unreviewed_source(
        _agent_source(
            "chunks = []\nwhile True:\n    chunks.append(bytearray(16 * 1024 * 1024))"
        ),
        limits=SandboxLimits(wall_clock_seconds=4, memory_bytes=128 * 1024 * 1024),
    )

    assert cpu_failure is not None
    assert cpu_failure.code == "cpu_time_exceeded"
    assert memory_failure is not None
    assert memory_failure.code == "memory_limit_exceeded"


def test_malformed_agent_output_is_not_a_success() -> None:
    _, failure = _drive_unreviewed_source(
        _agent_source('return {"status": "not-a-contract"}'),
    )

    assert failure is not None
    assert failure.code == "agent_output_malformed"


def test_tool_call_budget_is_enforced_before_unbounded_repetition() -> None:
    _, failure = _drive_unreviewed_source(
        _agent_source(
            """
            for _ in range(3):
                tools.catalog_search(query="birthday cake", max_price_krw=50000)
            return {"status": "blocked", "reason": "budget"}
            """
        ),
        limits=SandboxLimits(max_tool_calls=1, max_turns=1),
    )

    assert failure is not None
    assert failure.code == "tool_call_budget_exceeded"


def test_turn_budget_is_independently_enforced() -> None:
    _, failure = _drive_unreviewed_source(
        _agent_source(
            """
            for _ in range(2):
                tools.catalog_search(query="birthday cake", max_price_krw=50000)
            return {"status": "blocked", "reason": "budget"}
            """
        ),
        limits=SandboxLimits(max_tool_calls=3, max_turns=1),
    )

    assert failure is not None
    assert failure.code == "turn_budget_exceeded"
