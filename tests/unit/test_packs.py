"""Domain pack registry and the evidence-driven router (issue #57).

Two kinds of test live here.

**Routing behaviour** — normal, tie, unsupported, budget-exhausted and
pack-error, plus the determinism the epic requires (same input three times,
same selection digest).

**Drift guards** — ``packs.py`` may not import the pack gyms, because every one
of them imports ``agents.function_tool`` and ``agent/`` is barred from that
dependency. So the specs restate each pack's tool surface, fixture ids and
fault families as data. These tests import the real packs and assert the
restatement is exact. Duplication that is checked is a seam; duplication that
is trusted is a bug waiting to happen.
"""

from __future__ import annotations

import pytest

from agent24.agent.models import (
    LoopState,
    Mission,
    MissionFamily,
    StopDecision,
    ToolSpec,
)
from agent24.agent.packs import (
    ADHOC_PACK,
    DOMAIN_PACKS,
    LIFE_PACK,
    PACKS_BY_ID,
    REGISTRY_VERSION,
    RESEARCH_PACK,
    STOCK_PACK,
    TICKET_PACK,
    DomainKind,
    PackExecution,
    pack_failure_stop,
    score_packs,
    select_domain_pack,
    selection_digest,
)
from agent24.agent.profile import (
    AgentManifest,
    EvidenceRef,
    build_behavior_profile,
    determined,
    unknown,
)
from agent24.agent.prompts import should_stop
from agent24.agent.report import FindingReportStatus, report_from_stop_decision

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"

CAKE_TOOLS = ("payment.charge", "payment.status")
RESEARCH_TOOLS = ("research.search", "paper.fetch", "pdf.page.read", "citation.resolve")
STOCK_TOOLS = ("ticker.resolve", "market.disclosures", "market.news", "analyst_note.read")
TICKET_TOOLS = (
    "ticket.event.search",
    "ticket.inventory.read",
    "ticket.hold.create",
    "ticket.purchase.confirm",
)

# One paper tool and one market tool, scoring 9 apiece. Not a contrived input:
# an agent that reads both papers and disclosures genuinely could be either a
# research assistant or a market analyst, and nothing observable says which.
AMBIGUOUS_TOOLS = ("paper.fetch", "market.disclosures")


def manifest_with(
    *tools: str,
    name: str = "external-agent",
    family: MissionFamily = MissionFamily.UNKNOWN,
) -> AgentManifest:
    return AgentManifest(
        name=name,
        source_ref=f"example/{name}@{PINNED_SHA}",
        tools=[ToolSpec(name=tool) for tool in tools],
        mission_family=family,
    )


def route(
    *tools: str,
    family: MissionFamily = MissionFamily.UNKNOWN,
    text: str = "이 에이전트를 검사해 줘",
):
    manifest = manifest_with(*tools, family=family)
    profile = build_behavior_profile(manifest, baseline=None)
    mission = Mission(text=text, family=family)
    return select_domain_pack(manifest=manifest, profile=profile, mission=mission)


# --------------------------------------------------------------------------
# Registry shape
# --------------------------------------------------------------------------


def test_every_domain_kind_is_registered_exactly_once() -> None:
    kinds = [spec.domain_kind for spec in DOMAIN_PACKS]
    assert sorted(kinds) == sorted(DomainKind)
    assert len(set(kinds)) == len(kinds)
    assert len(PACKS_BY_ID) == len(DOMAIN_PACKS)


def test_only_the_life_pack_is_wired_into_the_one_input_controller() -> None:
    """Everything else is a real selection with a named owner, not a silent gap."""

    executable = [spec for spec in DOMAIN_PACKS if spec.executable]
    assert [spec.pack_id for spec in executable] == [LIFE_PACK.pack_id]
    for spec in DOMAIN_PACKS:
        if spec.execution is PackExecution.REGISTERED:
            assert spec.deferred_to, f"{spec.pack_id} must name the issue that runs it"


def test_exactly_one_fallback_and_it_declares_no_anchor() -> None:
    fallbacks = [spec for spec in DOMAIN_PACKS if spec.is_fallback]
    assert [spec.pack_id for spec in fallbacks] == [ADHOC_PACK.pack_id]
    assert not ADHOC_PACK.anchor_tools


def test_domain_packs_do_not_share_anchor_tools() -> None:
    """Overlapping anchors would make routing a coin flip on every manifest."""

    seen: dict[str, str] = {}
    for spec in DOMAIN_PACKS:
        for tool in spec.anchor_tools:
            assert tool not in seen, f"{tool} anchors both {seen.get(tool)} and {spec.pack_id}"
            seen[tool] = spec.pack_id


def test_every_gated_capability_exists_on_the_profile() -> None:
    fields = set(build_behavior_profile(manifest_with(*CAKE_TOOLS)).assessments())
    for spec in DOMAIN_PACKS:
        for name in spec.gated_capabilities:
            assert name in fields, f"{spec.pack_id} gates on unknown field {name}"


def test_a_pack_without_fixtures_declares_no_fault_families() -> None:
    """A registered fixture is a promise that the gym can stage it."""

    for spec in DOMAIN_PACKS:
        if not spec.fixture_ids:
            assert not spec.fault_families, f"{spec.pack_id} claims faults it cannot run"


# --------------------------------------------------------------------------
# Drift guards — the specs vs the real packs
# --------------------------------------------------------------------------


def test_life_spec_matches_the_sandbox_pack() -> None:
    from agent24.tools.faults import FAULT_TYPES
    from agent24.tools.fixtures import FIXTURES
    from agent24.tools.sandbox import TOOL_MANIFEST

    assert LIFE_PACK.all_tools == {item["name"] for item in TOOL_MANIFEST}
    assert set(LIFE_PACK.fixture_ids) == set(FIXTURES)
    assert LIFE_PACK.fault_families == FAULT_TYPES


def test_research_spec_matches_the_research_pack() -> None:
    from agent24.tools.research_pack import (
        RESEARCH_FAILURE_FIXTURES,
        RESEARCH_FIXTURE_IDS,
        RESEARCH_TOOL_MANIFEST,
        ResearchGym,
    )

    assert RESEARCH_PACK.pack_id == ResearchGym.pack_id
    assert RESEARCH_PACK.all_tools == {item["name"] for item in RESEARCH_TOOL_MANIFEST}
    assert set(RESEARCH_PACK.fixture_ids) == set(RESEARCH_FIXTURE_IDS)
    atoms = frozenset().union(*RESEARCH_FAILURE_FIXTURES.values())
    assert RESEARCH_PACK.fault_families == atoms


def test_stock_spec_matches_the_stock_pack() -> None:
    from agent24.tools.stock_pack import (
        STOCK_FAILURE_FIXTURES,
        STOCK_FIXTURE_IDS,
        STOCK_TOOL_MANIFEST,
        StockGym,
    )

    assert STOCK_PACK.pack_id == StockGym.pack_id
    assert STOCK_PACK.all_tools == {item["name"] for item in STOCK_TOOL_MANIFEST}
    assert set(STOCK_PACK.fixture_ids) == set(STOCK_FIXTURE_IDS)
    atoms = frozenset().union(*STOCK_FAILURE_FIXTURES.values())
    assert STOCK_PACK.fault_families == atoms


def test_adhoc_spec_matches_the_adhoc_registry() -> None:
    from agent24.tools.adhoc import (
        ADHOC_OPERATORS,
        ADHOC_SCENARIO_IDS,
        MAX_ADHOC_TOOL_CALL_BUDGET,
        AdhocGym,
    )

    assert ADHOC_PACK.pack_id == AdhocGym.pack_id
    assert set(ADHOC_PACK.fixture_ids) == set(ADHOC_SCENARIO_IDS)
    assert ADHOC_PACK.fault_families == ADHOC_OPERATORS
    assert ADHOC_PACK.budget.max_tool_calls == MAX_ADHOC_TOOL_CALL_BUDGET


def test_every_routable_tool_is_loadable_from_a_manifest() -> None:
    """A tool the manifest loader rejects can never reach the router."""

    from agent24.agent.manifest import SUPPORTED_TOOL_NAMES

    for spec in DOMAIN_PACKS:
        unknown = spec.all_tools - SUPPORTED_TOOL_NAMES
        assert not unknown, f"{spec.pack_id} routes on tools the loader drops: {sorted(unknown)}"


def test_ticket_spec_matches_the_ticket_pack() -> None:
    """The guard that was missing when #57 shipped, and that #60 slipped past.

    #57 registered Ticket as a placeholder with an invented vocabulary because
    there was no ``tools/ticket_pack.py`` yet to compare against. #60 then
    shipped a gym whose tool names shared *nothing* with that placeholder, and
    no test noticed: the placeholder assertions still held. A real ticket agent
    routed to the Adhoc fallback instead of the Ticket pack.
    """

    from agent24.tools.ticket_pack import (
        MAX_TICKET_TOOL_CALL_BUDGET,
        TICKET_FAILURE_FIXTURES,
        TICKET_FIXTURE_IDS,
        TICKET_PACK_METADATA,
        TICKET_TOOL_MANIFEST,
        TicketGym,
    )

    assert TICKET_PACK.pack_id == TicketGym.pack_id == TICKET_PACK_METADATA["pack_id"]
    assert TICKET_PACK.all_tools == {item["name"] for item in TICKET_TOOL_MANIFEST}
    assert set(TICKET_PACK.fixture_ids) == set(TICKET_FIXTURE_IDS)
    atoms = frozenset().union(*TICKET_FAILURE_FIXTURES.values())
    assert TICKET_PACK.fault_families == atoms
    assert TICKET_PACK.budget.max_tool_calls == MAX_TICKET_TOOL_CALL_BUDGET


def _packs_publishing_metadata() -> list[tuple[str, object, dict]]:
    from agent24.tools.research_pack import RESEARCH_PACK_METADATA
    from agent24.tools.stock_pack import STOCK_PACK_METADATA
    from agent24.tools.ticket_pack import TICKET_PACK_METADATA

    return [
        ("research", RESEARCH_PACK, RESEARCH_PACK_METADATA),
        ("stock", STOCK_PACK, STOCK_PACK_METADATA),
        ("ticket", TICKET_PACK, TICKET_PACK_METADATA),
    ]


@pytest.mark.parametrize("name", ["research", "stock", "ticket"])
def test_the_registry_agrees_with_the_metadata_each_pack_publishes(name: str) -> None:
    """Three packs now publish a ``*_PACK_METADATA`` dict describing themselves.

    Comparing every shared key is what makes the registry's restatement
    checkable rather than hopeful. ``anchor_tool_capabilities`` is compared only
    when the pack publishes it -- Ticket does not.
    """

    spec, meta = next((s, m) for label, s, m in _packs_publishing_metadata() if label == name)

    assert spec.pack_id == meta["pack_id"]
    assert spec.version == meta["version"]
    assert spec.domain_kind.value == meta["domain_kind"]
    assert {family.value for family in spec.mission_families} == set(meta["mission_families"])
    assert spec.required_tools == frozenset(meta["required_tool_capabilities"])
    assert spec.optional_tools == frozenset(meta["optional_tool_capabilities"])
    assert spec.budget.max_tool_calls == meta["max_tool_calls"]
    assert spec.supports_benign_control is meta["supports_benign_control"]
    assert spec.supports_protected_replay is meta["supports_protected_replay"]
    if "anchor_tool_capabilities" in meta:
        assert spec.anchor_tools == frozenset(meta["anchor_tool_capabilities"])


def test_a_pack_that_ships_protected_replay_declares_it() -> None:
    """The flag describes the pack, not the controller wiring.

    ``supports_benign_control`` has been ``True`` for Research and Stock since
    they were registered, while ``execution`` stayed ``REGISTERED`` -- so the
    adjacent ``supports_protected_replay`` has to mean the same thing. Reading
    it as "the controller drives this" would duplicate ``execution`` and make
    one of the two fields dead.
    """

    from agent24.tools import (
        protected_replay,
        research_protected_replay,
        stock_protected_replay,
        ticket_protected_replay,
    )

    shipped = {
        LIFE_PACK.pack_id: protected_replay,
        RESEARCH_PACK.pack_id: research_protected_replay,
        STOCK_PACK.pack_id: stock_protected_replay,
        TICKET_PACK.pack_id: ticket_protected_replay,
    }
    for spec in DOMAIN_PACKS:
        expected = spec.pack_id in shipped
        assert spec.supports_protected_replay is expected, (
            f"{spec.pack_id} declares supports_protected_replay="
            f"{spec.supports_protected_replay} but "
            f"{'ships' if expected else 'has no'} a protected replay entry point"
        )


def test_a_real_ticket_agent_routes_to_the_ticket_pack() -> None:
    """The regression itself: this surface used to land on the Adhoc fallback."""

    from agent24.tools.ticket_pack import TICKET_PACK_METADATA

    selection = route(
        *TICKET_PACK_METADATA["required_tool_capabilities"],
        family=MissionFamily.PURCHASE,
    )

    assert selection.selected is not None
    assert selection.selected.domain_kind is DomainKind.TICKET
    assert selection.selected.is_fallback is False


def test_the_ticket_pack_is_shipped_but_not_yet_controller_wired() -> None:
    """#60 delivered the gym; the one-input wiring is still the epic's."""

    assert TICKET_PACK.execution is PackExecution.REGISTERED
    assert TICKET_PACK.deferred_to == "#55"
    assert TICKET_PACK.fixture_ids, "the shipped pack has fixtures; the registry must say so"


# --------------------------------------------------------------------------
# eval: normal routing
# --------------------------------------------------------------------------


def test_normal_a_payment_manifest_routes_to_life_and_stays_executable() -> None:
    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)

    assert selection.pack_id == LIFE_PACK.pack_id
    assert selection.stop is None
    assert selection.executed_by_controller is True
    assert selection.budget == LIFE_PACK.budget


def test_normal_a_research_manifest_routes_to_the_research_pack() -> None:
    selection = route(*RESEARCH_TOOLS, family=MissionFamily.RESEARCH)

    assert selection.selected.domain_kind is DomainKind.RESEARCH
    assert "#58" in selection.stop.detail


def test_normal_a_market_manifest_routes_to_the_stock_pack() -> None:
    """Stock has no MissionFamily, so its tool surface has to carry the decision."""

    selection = route(*STOCK_TOOLS)

    assert selection.selected.domain_kind is DomainKind.STOCK
    assert selection.selected.family_match is False
    assert "#59" in selection.stop.detail


def test_normal_a_ticket_manifest_routes_to_the_ticket_pack() -> None:
    selection = route(*TICKET_TOOLS, family=MissionFamily.PURCHASE)

    assert selection.selected.domain_kind is DomainKind.TICKET
    # #60 shipped the gym, so the remaining gap is controller wiring, not the pack.
    assert "#55" in selection.stop.detail


def test_normal_an_unknown_tool_surface_falls_back_to_adhoc() -> None:
    selection = route("slack.post", "jira.create")

    assert selection.selected.domain_kind is DomainKind.ADHOC
    assert selection.selected.is_fallback is True


def test_normal_a_domain_pack_always_outranks_the_fallback() -> None:
    """Even when Adhoc scores higher, a named domain beats a generic probe."""

    candidates = score_packs(
        manifest=manifest_with(*RESEARCH_TOOLS),
        profile=build_behavior_profile(manifest_with(*RESEARCH_TOOLS)),
        mission=Mission(text="검사", family=MissionFamily.RESEARCH),
    )
    assert [c.is_fallback for c in candidates] == sorted(c.is_fallback for c in candidates)
    assert candidates[0].domain_kind is DomainKind.RESEARCH


def test_normal_the_selection_records_why_expect_evidence_budget_and_fallback() -> None:
    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)

    assert selection.why and selection.expect
    assert selection.evidence, "a routing decision must cite where it came from"
    assert selection.budget is not None
    assert selection.fallback
    assert selection.registry_version == REGISTRY_VERSION
    # The expectation has to be falsifiable, so it names the fault families.
    assert "commit_then_timeout" in selection.expect


def test_normal_a_measured_weakness_scores_above_an_unobserved_gap() -> None:
    """Mirrors the #22 rule: an observed defect outweighs one we never measured."""

    manifest = manifest_with(*CAKE_TOOLS, family=MissionFamily.PURCHASE)
    mission = Mission(text="검사", family=MissionFamily.PURCHASE)
    profile = build_behavior_profile(manifest, baseline=None)

    as_unknown = profile.model_copy(
        update={"idempotency_usage": unknown("baseline을 아직 관찰하지 않았다")}
    )
    as_absent = profile.model_copy(
        update={
            "idempotency_usage": determined(
                "absent",
                EvidenceRef(kind="trace", trace_index=2, detail="재청구 전 상태 조회 없음"),
            )
        }
    )

    gap = score_packs(manifest=manifest, profile=as_unknown, mission=mission)[0]
    measured = score_packs(manifest=manifest, profile=as_absent, mission=mission)[0]

    assert "idempotency_usage" in gap.unknown_gates
    assert "idempotency_usage" in measured.measured_gates
    assert measured.score > gap.score


# --------------------------------------------------------------------------
# eval: tie -> ambiguous
# --------------------------------------------------------------------------


def test_tie_between_two_domain_packs_terminates_instead_of_guessing() -> None:
    selection = route(*AMBIGUOUS_TOOLS)

    assert selection.selected is None
    assert selection.stop is not None
    assert selection.stop.reason == "insufficient_evidence"
    assert "동점" in selection.stop.detail


def test_tie_names_every_pack_that_tied() -> None:
    selection = route(*AMBIGUOUS_TOOLS)

    assert RESEARCH_PACK.pack_id in selection.stop.detail
    assert STOCK_PACK.pack_id in selection.stop.detail


def test_an_ambiguous_tie_becomes_an_unsupported_report_not_a_sixth_status() -> None:
    selection = route(*AMBIGUOUS_TOOLS)
    report = report_from_stop_decision(
        "f-tie", selection.stop, unsupported_scope=[selection.stop.detail]
    )

    assert report.status is FindingReportStatus.UNSUPPORTED
    assert report.unsupported_scope


def test_the_fallback_never_participates_in_a_tie() -> None:
    """Adhoc tying with a domain pack must not block a real routing decision."""

    selection = route(*RESEARCH_TOOLS, family=MissionFamily.RESEARCH)
    assert selection.selected is not None


# --------------------------------------------------------------------------
# eval: unsupported
# --------------------------------------------------------------------------


def test_unsupported_a_manifest_with_no_tools_routes_nowhere() -> None:
    selection = route()

    assert selection.selected is None
    assert selection.candidates == ()
    assert selection.stop.reason == "unsupported_input"
    assert "도구가 없어" in selection.stop.detail


def test_unsupported_never_substitutes_a_pack_it_cannot_run() -> None:
    """The whole point: no silent downgrade to a pack whose surface is absent."""

    selection = route(*RESEARCH_TOOLS, family=MissionFamily.RESEARCH)

    assert selection.selected.domain_kind is DomainKind.RESEARCH
    life = [c for c in selection.candidates if c.pack_id == LIFE_PACK.pack_id]
    assert life == [], "Life must not be a candidate for a research tool surface"


def test_unsupported_stops_are_honest_about_which_pack_was_chosen() -> None:
    selection = route(*STOCK_TOOLS)

    assert selection.pack_id == STOCK_PACK.pack_id
    assert STOCK_PACK.pack_id in selection.stop.detail


# --------------------------------------------------------------------------
# eval: budget
# --------------------------------------------------------------------------


def test_every_pack_declares_a_bounded_budget() -> None:
    for spec in DOMAIN_PACKS:
        assert spec.budget.max_tool_calls >= 1
        assert spec.budget.max_experiments >= 1
        assert spec.budget.max_cost_units >= 1


def test_budget_exhausted_uses_the_selected_packs_own_bound() -> None:
    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)
    budget = selection.budget.loop_budget()

    spent = LoopState(experiments_run=budget.max_experiments)
    decision = should_stop(spent, budget)

    assert decision.reason == "budget_exhausted"
    assert should_stop(LoopState(experiments_run=0), budget).reason == "continue"


def test_a_pack_budget_bridges_to_the_loop_budget_without_a_second_source() -> None:
    loop_budget = ADHOC_PACK.budget.loop_budget()

    assert loop_budget.max_experiments == ADHOC_PACK.budget.max_experiments
    assert loop_budget.max_cost_units == ADHOC_PACK.budget.max_cost_units


# --------------------------------------------------------------------------
# eval: pack error
# --------------------------------------------------------------------------


def test_a_pack_error_names_the_pack_and_does_not_reroute() -> None:
    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)
    stop = pack_failure_stop(selection, "TimeoutError")

    assert stop.stop is True
    assert LIFE_PACK.pack_id in stop.detail
    assert "TimeoutError" in stop.detail
    # No other pack may appear: that is what "hidden behind a success" means.
    for spec in DOMAIN_PACKS:
        if spec.pack_id != LIFE_PACK.pack_id:
            assert spec.pack_id not in stop.detail


def test_a_pack_error_never_reports_a_measured_failure() -> None:
    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)
    stop = pack_failure_stop(selection, "RuntimeError")
    report = report_from_stop_decision("f-err", stop, unsupported_scope=[stop.detail])

    assert report.status is FindingReportStatus.UNSUPPORTED
    assert report.observed is None
    assert report.claims_a_failure is False


def test_a_pack_error_before_any_selection_is_still_honest() -> None:
    empty = route()
    stop = pack_failure_stop(empty, "ValueError")

    assert "unknown-pack" in stop.detail


# --------------------------------------------------------------------------
# Determinism — the epic's "3 runs, same digest" requirement
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tools,family",
    [
        (CAKE_TOOLS, MissionFamily.PURCHASE),
        (RESEARCH_TOOLS, MissionFamily.RESEARCH),
        (STOCK_TOOLS, MissionFamily.UNKNOWN),
        (TICKET_TOOLS, MissionFamily.PURCHASE),
        (("slack.post",), MissionFamily.UNKNOWN),
    ],
)
def test_three_runs_of_the_same_input_produce_the_same_selection_digest(
    tools: tuple[str, ...], family: MissionFamily
) -> None:
    digests = {selection_digest(route(*tools, family=family)) for _ in range(3)}
    assert len(digests) == 1


def test_the_stamped_digest_matches_a_recomputation() -> None:
    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)

    assert selection.selection_digest == selection_digest(selection)
    assert len(selection.selection_digest) == 64


def test_tool_declaration_order_does_not_change_the_decision() -> None:
    forward = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)
    reversed_order = route(*reversed(CAKE_TOOLS), family=MissionFamily.PURCHASE)

    assert selection_digest(forward) == selection_digest(reversed_order)


def test_the_registry_version_is_part_of_the_digest() -> None:
    """A registry edit must be visible in the replay, not silently reinterpreted."""

    selection = route(*CAKE_TOOLS, family=MissionFamily.PURCHASE)
    bumped = selection.model_copy(update={"registry_version": "domain-pack-registry.v2"})

    assert selection_digest(bumped) != selection_digest(selection)


def test_selection_carries_no_untyped_stop_reason() -> None:
    for tools in (CAKE_TOOLS, RESEARCH_TOOLS, STOCK_TOOLS, TICKET_TOOLS, ()):
        selection = route(*tools)
        if selection.stop is not None:
            assert isinstance(selection.stop, StopDecision)
