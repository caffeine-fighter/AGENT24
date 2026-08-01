"""Surprise-mission support gates on the path the real form actually uses.

``test_surprise.py`` covers the legacy one-field ``{"input": ...}`` transport,
where every documented mission passes because the gym routes on keywords.  These
tests cover the structured-``target`` transport that ``web/`` submits, where the
question is different: can D1 honestly stage this mission's domain at all, and
if not, does the run say so instead of returning a payment finding?

Everything here is network-free.  The revision resolver and the manifest fetcher
are both in-memory fixtures, and no ``OPENAI_API_KEY`` is set, so the runtime
takes its deterministic offline branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent24.agent.models import StopDecision
from agent24.agent.packs import DOMAIN_PACKS, LIFE_PACK
from agent24.agent.source import MappingRevisionResolver
from agent24.api import (
    ExternalAgentPreflight,
    MappingManifestFetcher,
    OpenAIWhiteBoxAdapter,
    RuntimeSettings,
    create_app,
)
from agent24.evals import parse_sse
from agent24.evals.surprise_support import (
    DOMAIN_REQUIREMENTS,
    MISSIONS_BY_ID,
    SURPRISE_MISSIONS,
    UNSUPPORTED_REASON,
    SupportVerdict,
    SurpriseDomain,
    classify_matrix,
    classify_support,
    digest_projection,
    evaluate_support_run,
    executable_packs,
    payment_evidence,
    terminal_digest,
)

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"

PAYMENT_SURFACE = (
    {"name": "payment.charge", "side_effect": True, "irreversible": True},
    {"name": "payment.status"},
)
"""A D1 payment agent: exactly the surface the cake demo is built on."""

WEB_SURFACE = (
    {"name": "payment.charge", "side_effect": True, "irreversible": True},
    {"name": "web.fetch"},
    {"name": "email.send", "side_effect": True},
)

RESEARCH_SURFACE = ({"name": "research.search"}, {"name": "paper.fetch"})

MONEY = MISSIONS_BY_ID["money-unverified-transfer"]
COMMUNICATION = MISSIONS_BY_ID["communication-instruction-conflict"]
TIME = MISSIONS_BY_ID["time-repeated-calendar-search"]


def _tool_names(surface) -> set[str]:
    return {tool["name"] for tool in surface}


def _preflight(surface) -> ExternalAgentPreflight:
    manifest = json.dumps(
        {
            "name": "surprise-agent",
            "entrypoint": "agent/main.py",
            "system_prompt": "Do the submitted mission.",
            "mission_family": "purchase",
            "permissions": {"max_spend_krw": 50_000},
            "tools": list(surface),
        }
    )
    return ExternalAgentPreflight(
        source_resolver=MappingRevisionResolver(
            {("example/surprise-agent", "main"): PINNED_SHA}
        ),
        manifest_fetcher=MappingManifestFetcher({".agent24/manifest.json": manifest}),
        retrieved_at="2026-08-01T17:45:00+09:00",
    )


def _run(monkeypatch, root: Path, surface, mission_text: str) -> list[dict]:
    """Submit one mission through the real form shape and collect its stream."""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = OpenAIWhiteBoxAdapter(
        preflight=_preflight(surface),
        settings=RuntimeSettings(openai_api_key=None),
    )
    app = create_app(runtime=runtime, artifact_root=root)
    with TestClient(app) as client:
        accepted = client.post(
            "/api/runs",
            json={
                "input": mission_text,
                "target": {
                    "repository_url": "https://github.com/example/surprise-agent",
                    "requested_ref": "main",
                    "mission": mission_text,
                },
            },
        )
        assert accepted.status_code == 202
        return parse_sse(client.get(accepted.json()["events_url"]).text)


def _event(events, kind: str) -> dict:
    return next(event for event in events if event["type"] == kind)


def _stream(*payloads: tuple[str, dict]) -> list[dict]:
    """A literal event stream, for gate assertions that must not depend on a run."""

    return [
        {"run_id": "r1", "seq": index, "type": kind, "payload": payload}
        for index, (kind, payload) in enumerate(payloads)
    ]


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_the_documented_matrix_is_the_one_the_docs_offer() -> None:
    assert [mission.domain for mission in SURPRISE_MISSIONS] == [
        SurpriseDomain.MONEY,
        SurpriseDomain.COMMUNICATION,
        SurpriseDomain.TIME,
        SurpriseDomain.DATA,
        SurpriseDomain.CROSS_DOMAIN,
    ]
    assert all(mission.text.endswith("진단해줘.") for mission in SURPRISE_MISSIONS)


def test_classification_is_deterministic_and_uses_no_model() -> None:
    first = classify_matrix(tools=_tool_names(PAYMENT_SURFACE))
    second = classify_matrix(tools=_tool_names(PAYMENT_SURFACE))
    assert [support.model_dump_json() for support in first] == [
        support.model_dump_json() for support in second
    ]


def test_a_payment_agent_supports_only_the_money_domain() -> None:
    verdicts = {
        support.domain: support.verdict
        for support in classify_matrix(tools=_tool_names(PAYMENT_SURFACE))
    }
    assert verdicts[SurpriseDomain.MONEY] is SupportVerdict.SUPPORTED
    assert verdicts[SurpriseDomain.COMMUNICATION] is SupportVerdict.UNSUPPORTED
    assert verdicts[SurpriseDomain.TIME] is SupportVerdict.UNSUPPORTED
    assert verdicts[SurpriseDomain.DATA] is SupportVerdict.UNSUPPORTED
    assert verdicts[SurpriseDomain.CROSS_DOMAIN] is SupportVerdict.UNSUPPORTED


def test_support_names_the_pack_and_the_fault_family_that_earns_it() -> None:
    support = classify_support(MONEY, tools=_tool_names(PAYMENT_SURFACE))
    assert support.pack_id == LIFE_PACK.pack_id
    assert support.fault_family == "commit_then_timeout"
    assert support.fault_family in LIFE_PACK.fault_families
    assert support.reason == ""


def test_communication_becomes_supported_only_when_the_surface_is_there() -> None:
    """The domain is stageable; a payment-only agent still cannot demonstrate it."""

    payment_only = classify_support(COMMUNICATION, tools=_tool_names(PAYMENT_SURFACE))
    with_web = classify_support(COMMUNICATION, tools=_tool_names(WEB_SURFACE))
    assert payment_only.verdict is SupportVerdict.UNSUPPORTED
    assert "노출하지 않는다" in payment_only.detail
    assert with_web.verdict is SupportVerdict.SUPPORTED
    assert with_web.fault_family == "malicious_web_content"


def test_an_unsupported_verdict_reuses_the_controller_stop_vocabulary() -> None:
    """No sixth terminal word: the eval and the controller must agree."""

    support = classify_support(TIME, tools=_tool_names(WEB_SURFACE))
    assert support.reason == UNSUPPORTED_REASON
    assert StopDecision(stop=True, reason=UNSUPPORTED_REASON, detail="x").reason == support.reason


def test_a_supported_domain_never_names_a_fault_family_the_registry_lacks() -> None:
    """Drift guard: the requirement table is checked against the real packs."""

    registered = {family for spec in DOMAIN_PACKS for family in spec.fault_families}
    for domain in (SurpriseDomain.MONEY, SurpriseDomain.COMMUNICATION):
        assert DOMAIN_REQUIREMENTS[domain].fault_families <= registered


def test_the_unsupported_domains_are_unsupported_because_no_pack_stages_them() -> None:
    """The reason is structural, not a hand-written verdict.

    If a later pack registers one of these families, this assertion fails and
    the verdict above has to be revisited -- which is the intent.
    """

    registered = {family for spec in DOMAIN_PACKS for family in spec.fault_families}
    for domain in (SurpriseDomain.TIME, SurpriseDomain.DATA, SurpriseDomain.CROSS_DOMAIN):
        assert not (DOMAIN_REQUIREMENTS[domain].fault_families & registered)


def test_life_is_the_only_pack_the_one_input_controller_can_drive_in_d1() -> None:
    assert [spec.pack_id for spec in executable_packs()] == [LIFE_PACK.pack_id]


# --------------------------------------------------------------------------
# Terminal digest
# --------------------------------------------------------------------------


def test_the_digest_ignores_run_id_and_timestamps() -> None:
    base = [
        {
            "run_id": "a",
            "seq": 0,
            "timestamp": "2026-08-01T07:00:00+00:00",
            "type": "run_completed",
            "payload": {"status": "unsupported"},
        }
    ]
    renamed = [{**base[0], "run_id": "b", "timestamp": "2026-08-01T09:30:00+00:00"}]
    assert terminal_digest(base) == terminal_digest(renamed)
    assert digest_projection(base) == [{"seq": 0, "type": "run_completed", "status": "unsupported"}]


def test_the_digest_changes_when_the_terminal_status_changes() -> None:
    unsupported = [{"seq": 0, "type": "run_completed", "payload": {"status": "unsupported"}}]
    completed = [{"seq": 0, "type": "run_completed", "payload": {"status": "completed"}}]
    assert terminal_digest(unsupported) != terminal_digest(completed)


# --------------------------------------------------------------------------
# Stream gates, network-free
# --------------------------------------------------------------------------


def test_an_unsupported_mission_terminates_exactly_once_as_unsupported(
    monkeypatch, tmp_path: Path
) -> None:
    events = _run(monkeypatch, tmp_path, RESEARCH_SURFACE, TIME.text)
    support = classify_support(TIME, tools=_tool_names(RESEARCH_SURFACE))
    report = evaluate_support_run(support, events)

    assert report.passed, report.errors
    assert report.terminal_count == 1
    assert report.terminal_status == "unsupported"
    assert _event(events, "finding_report")["payload"]["status"] == "unsupported"
    assert _event(events, "lab_report")["payload"]["termination"]["reason"] == UNSUPPORTED_REASON


def test_an_unsupported_terminal_carries_no_payment_or_cake_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    """The completion condition, stated directly."""

    events = _run(monkeypatch, tmp_path, RESEARCH_SURFACE, TIME.text)
    report = evaluate_support_run(
        classify_support(TIME, tools=_tool_names(RESEARCH_SURFACE)), events
    )

    assert payment_evidence(events) == ()
    assert report.silent_substitution is False
    assert "protected_replay" not in [event["type"] for event in events]
    assert "experiment_plan" not in [event["type"] for event in events]


def test_the_detector_reports_a_substitution_with_the_events_that_prove_it() -> None:
    """The silent substitution this issue exists for, as a detector test.

    Built from a literal stream rather than a live run so it keeps testing the
    detector no matter how the route below is later fixed: a substituted answer
    is a shape, and the gate must always refuse to call that shape a pass.
    """

    support = classify_support(TIME, tools=_tool_names(PAYMENT_SURFACE))
    events = _stream(
        ("run_started", {"mode": "offline_demo"}),
        ("pack.selected", {"selected": {"pack_id": "life-v0-sandbox.v1"}}),
        ("experiment_plan", {"plan_id": "p-1", "scenario": {"faults": ["payment.charge"]}}),
        ("gym.tool_call", {"tool": "payment.charge"}),
        ("protected_replay", {"accepted": True}),
        ("finding_report", {"status": "reproduced"}),
        ("run_completed", {"status": "offline_demo"}),
    )
    report = evaluate_support_run(support, events)

    assert support.verdict is SupportVerdict.UNSUPPORTED
    assert report.passed is False
    assert report.silent_substitution is True
    assert report.terminal_status == "offline_demo"
    # Citations, not a bare boolean: every claim here can be checked by hand.
    assert any("gym.tool_call:payment.charge" in ref for ref in report.payment_citations)
    assert any("protected_replay" in ref for ref in report.payment_citations)


def test_an_off_domain_mission_against_a_payment_manifest_terminates_unsupported(
    monkeypatch, tmp_path: Path
) -> None:
    """The product requirement, now enforced by the controller.

    This was a strict xfail while the mission text selected nothing: a judge
    asking about a calendar loop, submitting a payment manifest, got a
    duplicate-charge finding.  ``agent/mission_scope.py`` now stops the run
    after routing, so the requirement holds and the marker is gone.
    """

    events = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, TIME.text)
    support = classify_support(TIME, tools=_tool_names(PAYMENT_SURFACE))
    report = evaluate_support_run(support, events)

    assert support.verdict is SupportVerdict.UNSUPPORTED
    assert report.passed, report.errors
    assert report.silent_substitution is False
    assert report.terminal_status == "unsupported"


def test_the_off_domain_run_stops_before_planning_an_experiment(
    monkeypatch, tmp_path: Path
) -> None:
    """Stopping is only honest if nothing ran -- assert the absence directly.

    The absent events are named rather than the whole sequence pinned.  The
    preflight prologue grows as other issues land (``source_snapshot`` and
    ``target_profile`` arrived with #62), and re-pinning their order here would
    make this test fail for reasons that have nothing to do with scope, while
    duplicating what ``test_api.py`` already owns.  What must hold is that no
    experiment was planned, none ran, and nothing was replayed.
    """

    events = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, TIME.text)
    types = [event["type"] for event in events]

    assert "experiment_plan" not in types
    assert "protected_replay" not in types
    assert not [kind for kind in types if kind.startswith("gym.")]
    assert payment_evidence(events) == ()

    # The routing record survives: on this path it is the only statement of
    # which pack was considered and why nothing was run against it.
    assert "pack.selected" in types
    assert types[-1] == "run_completed"
    assert types.count("run_completed") == 1
    lab_report = _event(events, "lab_report")["payload"]
    assert lab_report["termination"]["reason"] == UNSUPPORTED_REASON
    assert lab_report["findings"] == []
    assert "time" in events[-1]["payload"]["message"]


def test_a_benign_purchase_mission_still_runs_the_whole_experiment(
    monkeypatch, tmp_path: Path
) -> None:
    """The gate's real risk is over-blocking, so pin the demo path explicitly.

    ``docs/demo-runbook.md``'s mission, verbatim, against the payment manifest.
    If the marker table ever grows a phrase this sentence contains, this fails
    before the demo does.
    """

    runbook_mission = "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘."
    events = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, runbook_mission)
    types = [event["type"] for event in events]

    assert "experiment_plan" in types
    assert "protected_replay" in types
    assert events[-1]["payload"]["status"] == "offline_demo"


def test_a_supported_money_mission_passes_its_gate(monkeypatch, tmp_path: Path) -> None:
    events = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, MONEY.text)
    report = evaluate_support_run(
        classify_support(MONEY, tools=_tool_names(PAYMENT_SURFACE)), events
    )

    assert report.passed, report.errors
    assert report.verdict is SupportVerdict.SUPPORTED
    assert report.terminal_count == 1
    assert payment_evidence(events)


def test_the_mission_text_does_not_change_the_selected_pack(
    monkeypatch, tmp_path: Path
) -> None:
    """Where the scope gate sits, asserted from both sides.

    Routing still reads only the submitted repository: ``preflight.py`` builds
    the Mission with ``family=manifest.mission_family``, and two different
    missions against one manifest make the *same* routing decision -- identical
    selection digest, same pack, still executable.

    What changed is what happens next.  ``mission_scope_stop`` runs after the
    router has chosen and turns an out-of-scope mission into an unsupported
    terminal without rewriting the selection.  Asserting both halves is what
    keeps the two concerns from being confused later: the pack is fine, the
    mission is not in its scope.
    """

    money = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, MONEY.text)
    time_domain = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, TIME.text)

    money_pack = _event(money, "pack.selected")["payload"]
    time_pack = _event(time_domain, "pack.selected")["payload"]

    assert money_pack["selection_digest"] == time_pack["selection_digest"]
    assert money_pack["stop"] is None
    assert time_pack["selected"]["executable"] is True

    assert money[-1]["payload"]["status"] == "offline_demo"
    assert time_domain[-1]["payload"]["status"] == "unsupported"


def test_the_same_fixture_and_seed_reproduce_the_same_terminal_digest(
    monkeypatch, tmp_path: Path
) -> None:
    first = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, MONEY.text)
    second = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, MONEY.text)

    assert terminal_digest(first) == terminal_digest(second)
    # Two runs really are two runs; only the volatile identity differs.
    assert first[0]["run_id"] != second[0]["run_id"]


def test_an_unsupported_route_has_its_own_stable_digest(
    monkeypatch, tmp_path: Path
) -> None:
    """A route that stops before planning is reproducible and distinguishable.

    Compared against the *supported* route rather than against the substituted
    one: once the off-domain mission also terminates unsupported, its skeleton
    converges with this one by design, and a digest that had to stay different
    would be asserting the bug rather than determinism.
    """

    first = _run(monkeypatch, tmp_path, RESEARCH_SURFACE, TIME.text)
    second = _run(monkeypatch, tmp_path, RESEARCH_SURFACE, TIME.text)
    supported = _run(monkeypatch, tmp_path, PAYMENT_SURFACE, MONEY.text)

    assert terminal_digest(first) == terminal_digest(second)
    assert terminal_digest(first) != terminal_digest(supported)


# --------------------------------------------------------------------------
# Gate negatives
# --------------------------------------------------------------------------


@pytest.fixture
def unsupported_support():
    return classify_support(TIME, tools=_tool_names(RESEARCH_SURFACE))


def test_two_terminal_events_fail_the_gate(unsupported_support) -> None:
    events = _stream(
        ("finding_report", {"status": "unsupported"}),
        ("run_completed", {"status": "unsupported"}),
        ("run_completed", {"status": "unsupported"}),
    )
    report = evaluate_support_run(unsupported_support, events)
    assert report.passed is False
    assert report.terminal_count == 2
    assert "stream has 2 terminal events, expected exactly one" in report.errors


def test_a_terminal_that_is_not_last_fails_the_gate(unsupported_support) -> None:
    events = _stream(
        ("finding_report", {"status": "unsupported"}),
        ("run_completed", {"status": "unsupported"}),
        ("final_output", {"text": "그래도 결제는 해봤습니다"}),
    )
    report = evaluate_support_run(unsupported_support, events)
    assert report.passed is False
    assert "terminal event is not the last event" in report.errors


def test_an_unsupported_terminal_without_its_finding_report_fails(unsupported_support) -> None:
    events = _stream(("run_completed", {"status": "unsupported"}))
    report = evaluate_support_run(unsupported_support, events)
    assert report.passed is False
    assert "no finding_report carries the unsupported status" in report.errors


def test_a_supported_mission_may_not_end_as_unsupported() -> None:
    support = classify_support(MONEY, tools=_tool_names(PAYMENT_SURFACE))
    events = _stream(("run_completed", {"status": "unsupported"}))
    report = evaluate_support_run(support, events)
    assert report.passed is False
    assert any("expected one of" in error for error in report.errors)
