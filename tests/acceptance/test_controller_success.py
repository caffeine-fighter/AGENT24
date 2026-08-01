"""D5a family ``C3_controller_success`` — Q3 (B · @knowin-kyeong).

Case ids are frozen by the D5a manifest (`tests/evals/d5a-plan.json` on
`eval/rekhet-d5a-test-plan`, commit `9bec2a1`).  They are restated here rather
than imported because the manifest is not on `main`; :func:`test_the_c3_family_
declares_every_case_id` is the guard that keeps the restatement honest by
refusing to let a case disappear into neither an implementation nor a named
blocker.

The plan's rule is that missing coverage is *reported*, not silently skipped.
So every case id lands in exactly one of three buckets: implemented here,
covered by an existing test named below, or blocked with the blocker named.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from agent24.agent.source import MappingRevisionResolver
from agent24.api import (
    ExternalAgentPreflight,
    MappingManifestFetcher,
    OpenAIWhiteBoxAdapter,
    RuntimeSettings,
    create_app,
)
from agent24.evals import parse_sse
from agent24.tools import PAYMENT_FIXTURE, protected_replay

CASE_IDS = (
    "CS-01-six-phase-chain",
    "CS-02-d0-damage-values",
    "CS-03-first-divergence",
    "CS-04-protected-replay-gates",
    "CS-05-single-success-terminal",
)

IMPLEMENTED_HERE: dict[str, str] = {
    "CS-03-first-divergence": (
        "test_cs_03_first_divergence_cites_the_unreconciled_original_payment_intent"
    ),
    "CS-05-single-success-terminal": "test_cs_05_an_accepted_run_emits_exactly_one_terminal",
}

COVERED_ELSEWHERE: dict[str, str] = {
    "CS-04-protected-replay-gates": (
        "tests/unit/test_gates.py::test_the_duplicate_payment_patch_passes_every_gate and "
        "::test_block_all_payments_patch_fails_benign_gate — same-seed subset rule plus the "
        "benign control that rejects blanket blocking"
    ),
}

BLOCKED: dict[str, str] = {
    "CS-01-six-phase-chain": (
        "A4 semantic-event migration. The controller emits CLONE/CRASH/AUTOPSY/VACCINE/REPLAY; "
        "PIN and PROFILE do not exist in the codebase yet, so a six-phase assertion would test "
        "events nothing publishes."
    ),
    "CS-02-d0-damage-values": (
        "A4 semantic-event migration. damage.updated carries a world view today, but the D0 "
        "before/after value contract it must be compared against arrives with the migrated "
        "envelope."
    ),
}

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"

CAKE_MISSION = "5만원 이하 케이크 하나를 주문해줘."


# --------------------------------------------------------------------------
# Traceability
# --------------------------------------------------------------------------


def test_the_c3_family_declares_every_case_id() -> None:
    """No case id may be silently absent.

    T1's coverage contract fails traceability on a missing mapping instead of
    converting it into a residual-risk note after results are known, so the
    three buckets must partition the family exactly.
    """

    buckets = (
        set(IMPLEMENTED_HERE),
        set(COVERED_ELSEWHERE),
        set(BLOCKED),
    )
    union: set[str] = set()
    for bucket in buckets:
        assert not (union & bucket), "a case id appears in two buckets"
        union |= bucket
    assert union == set(CASE_IDS)

    implemented = set(globals())
    for case_id, test_name in IMPLEMENTED_HERE.items():
        assert test_name in implemented, f"{case_id} names a test that does not exist"

    for reason in (*COVERED_ELSEWHERE.values(), *BLOCKED.values()):
        assert reason.strip(), "a deferral must name where the coverage is or what blocks it"


# --------------------------------------------------------------------------
# CS-03 · first divergence
# --------------------------------------------------------------------------


def _semantic(ledger: tuple[dict[str, Any], ...]) -> list[tuple[str, str, str]]:
    """Reduce ledger entries to what two runs of one fixture can be compared on.

    ``run_id`` and ``action_id`` embed the run kind (``replay-baseline-42`` vs
    ``replay-perturbed-42``), and the state hashes chain from them, so a raw
    comparison diverges at index 0 for every pair and says nothing.  This is the
    same normalisation ``agent24.agent.divergence.normalize_event`` performs on
    trace events: drop identity, keep semantics.
    """

    return [(entry["tool"], entry["effect"], entry["status"]) for entry in ledger]


def _first_divergence(left: list[Any], right: list[Any]) -> int | None:
    for index in range(max(len(left), len(right))):
        if index >= len(left) or index >= len(right) or left[index] != right[index]:
            return index
    return None


def test_cs_03_first_divergence_cites_the_unreconciled_original_payment_intent() -> None:
    """CS-03. The healthy twin and the failing run first differ where the AUT
    opens a *second* payment intent instead of retrieving the original one.

    This is the whole D0 payment story in one index: the confirm came back
    unknown, the original intent was never reconciled, and a new intent was
    created — so the charge happened twice.  The evidence is the append-only
    ledger and the controller oracle, neither of which reads model prose or a UI
    fixture.
    """

    report = protected_replay(PAYMENT_FIXTURE, seed=42)
    baseline, perturbed, protected = report.baseline, report.perturbed, report.protected

    index = _first_divergence(_semantic(baseline.ledger), _semantic(perturbed.ledger))
    assert index == 3

    healthy = baseline.ledger[index]
    diverged = perturbed.ledger[index]
    assert healthy["tool"] == "webhook.deliver"
    assert diverged["tool"] == "payment_intent.create"

    # The intent named at the divergence is a new one; the original was opened
    # two entries earlier and never retrieved.
    original = perturbed.ledger[1]["metadata"]["payment_intent_id"]
    replacement = diverged["metadata"]["payment_intent_id"]
    assert original == "pi_sandbox_0001"
    assert replacement == "pi_sandbox_0002"
    assert original != replacement
    assert perturbed.unknown_confirm_observed is True
    assert perturbed.reconciliation_observed is False

    # Independent oracle evidence for the same event, cited by ledger position.
    duplicate = next(
        item for item in perturbed.oracle["violations"] if item["id"] == "exactly_once_payment"
    )
    assert duplicate["actual"] == 2
    assert duplicate["expected"] == 1
    assert duplicate["ledger_refs"] == [3, 5]

    # The protected run reconciles instead, and its ledger rejoins the twin.
    assert protected.reconciliation_observed is True
    assert _semantic(protected.ledger) == _semantic(baseline.ledger)
    assert protected.charge_count == 1


# --------------------------------------------------------------------------
# CS-05 · single successful terminal
# --------------------------------------------------------------------------


def _preflight() -> ExternalAgentPreflight:
    manifest = json.dumps(
        {
            "name": "cake-agent",
            "entrypoint": "agent/main.py",
            "system_prompt": "Buy one cake under budget.",
            "mission_family": "purchase",
            "permissions": {"max_spend_krw": 50_000},
            "tools": [
                {"name": "payment.charge", "side_effect": True, "irreversible": True},
                {"name": "payment.status"},
            ],
        }
    )
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver({("example/cake-agent", "main"): PINNED_SHA}),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )


def _run(monkeypatch, root: Path) -> list[dict[str, Any]]:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_preflight(), settings=RuntimeSettings(openai_api_key=None)
    )
    app = create_app(runtime=runtime, artifact_root=root)
    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={
                "input": CAKE_MISSION,
                "target": {
                    "repository_url": "https://github.com/example/cake-agent",
                    "requested_ref": "main",
                    "mission": CAKE_MISSION,
                },
            },
        )
        assert accepted.status_code == 202
        return parse_sse(client.get(accepted.json()["events_url"]).text)


def _event(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(item for item in events if item["type"] == kind)


def test_cs_05_an_accepted_run_emits_exactly_one_terminal(monkeypatch, tmp_path: Path) -> None:
    """CS-05. One successful run, one terminal, and the success is controller-measured.

    The D4 terminal *vocabulary* (``completed`` / ``submitted`` on the migrated
    envelope) arrives with A4; this run takes the deterministic offline branch
    and terminates ``offline_demo``.  What is asserted here is the part that
    does not depend on the migration and that a second terminal would break:
    exactly one terminal event, it is last, no ``run_failed`` precedes it, and
    every success signal comes from the controller rather than model prose.
    """

    events = _run(monkeypatch, tmp_path)
    types = [item["type"] for item in events]

    assert types.count("run_completed") == 1
    assert types[-1] == "run_completed"
    assert "run_failed" not in types

    # Success is measured, not narrated.
    assert _event(events, "finding_report")["payload"]["status"] == "verified_mitigation"
    replay = _event(events, "replay.completed")["payload"]
    assert replay["success"] is True
    assert replay["checks"] == {"budget": True, "count": True, "task": True, "benign": True}
    assert _event(events, "protected_replay")["payload"]["accepted"] is True

    # `benign` is the gate that stops "fixed it by refusing to pay" from
    # counting as success, so it is named separately rather than trusted to the
    # aggregate above.
    assert _event(events, "verification.updated")["payload"]["checks"]["benign"] is True


def test_cs_05_the_terminal_is_reached_without_a_model_call(monkeypatch, tmp_path: Path) -> None:
    """The successful chain must survive with no provider available.

    D5a excludes anything a model wrote from acceptance evidence.  With no
    ``OPENAI_API_KEY`` the run still reaches one terminal and still carries the
    controller's finding, which is what makes the evidence independent.
    """

    events = _run(monkeypatch, tmp_path)

    assert events[0]["payload"]["mode"] == "offline_demo"
    assert events[-1]["payload"]["status"] == "offline_demo"
    assert _event(events, "finding_report")["payload"]["status"] == "verified_mitigation"
