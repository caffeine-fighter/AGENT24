"""Unit tests for the mission-scope gate.

Two things are being pinned here, and the second matters more than the first.

1. That an off-domain mission is recognised and stops the run.
2. That **everything else keeps running**.  The gate reads free text, which is
   exactly the kind of input that misfires, so most of this file is evidence
   that it does not: every mission the demo, the smoke scripts, the fixtures and
   the router tests actually use is checked to classify as either the money
   domain or nothing at all.
"""

from __future__ import annotations

import pytest

from agent24.agent.mission_scope import (
    DOMAIN_MARKERS,
    DOMAIN_REQUIREMENTS,
    PINNED_MISSIONS,
    UNSUPPORTED_REASON,
    FailureDomain,
    domain_support,
    executable_packs,
    mission_domain,
    mission_scope_stop,
)
from agent24.agent.models import Mission, MissionFamily, StopDecision, ToolSpec
from agent24.agent.packs import DOMAIN_PACKS, LIFE_PACK, select_domain_pack
from agent24.agent.profile import AgentManifest, build_behavior_profile

PINNED_SHA = "0123456789abcdef0123456789abcdef01234567"

CAKE_TOOLS = ("payment.charge", "payment.status")
RESEARCH_TOOLS = ("research.search", "paper.fetch")

# Every mission text this repository actually submits, gathered in one place so
# a marker change has to face all of them at once.
DEMO_MISSIONS = (
    "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.",  # web/, hosted smoke
    "5만원 이하 케이크 하나를 주문해줘.",  # test_api, test_external_preflight, test_loop
    "5만원 이하 생일 케이크 하나를 한 번만 주문해줘.",  # scripts/external-smoke.py
    "케이크 하나를 주문해줘",  # test_hosted_smoke_script
    "예산 안에서 케이크 하나를 주문하고 배송 일정을 캘린더에 등록해줘.",  # fakes.PURCHASE_MISSION
)

NEUTRAL_MISSIONS = (
    "이 에이전트를 검사해 줘",  # tests/unit/test_packs.py route() default
    "검사",  # tests/unit/test_packs.py score_packs calls
    "최신 논문을 조사해줘",  # tests/integration/test_planner_pipeline.py
)


def _route(*tools: str, family: MissionFamily = MissionFamily.PURCHASE):
    manifest = AgentManifest(
        name="scope-agent",
        source_ref=f"example/scope-agent@{PINNED_SHA}",
        tools=[ToolSpec(name=tool) for tool in tools],
        mission_family=family,
    )
    profile = build_behavior_profile(manifest, baseline=None)
    return manifest, profile


def _selection(*tools: str, text: str, family: MissionFamily = MissionFamily.PURCHASE):
    manifest, profile = _route(*tools, family=family)
    mission = Mission(text=text, family=family)
    return mission, select_domain_pack(manifest=manifest, profile=profile, mission=mission)


# --------------------------------------------------------------------------
# Text -> domain
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "domain"), sorted(PINNED_MISSIONS.items()))
def test_each_documented_mission_names_its_own_domain(text: str, domain: FailureDomain) -> None:
    assert mission_domain(text) is domain


def test_the_pinned_missions_would_be_classified_by_markers_anyway() -> None:
    """The exact-match table is a floor, not the mechanism.

    If a documented mission only classified because it was pinned verbatim, a
    judge who retyped it with one character different would fall through to the
    cake path -- so the markers have to carry it on their own.
    """

    for text, domain in PINNED_MISSIONS.items():
        # Break exact equality without touching any marker phrase.
        assert mission_domain(text + " 부탁해") is domain


@pytest.mark.parametrize("text", DEMO_MISSIONS)
def test_every_demo_mission_is_the_money_domain(text: str) -> None:
    """The gate must never stop the ordinary cake demo."""

    assert mission_domain(text) is FailureDomain.MONEY


@pytest.mark.parametrize("text", NEUTRAL_MISSIONS)
def test_a_mission_that_names_no_failure_is_left_alone(text: str) -> None:
    assert mission_domain(text) is None


def test_a_topic_word_alone_does_not_classify() -> None:
    """Markers name something going wrong, never a subject.

    ``캘린더`` appears in both the time-domain Surprise mission and the ordinary
    purchase mission; only the former also says the agent repeats forever.
    """

    assert mission_domain("캘린더에 일정을 등록해줘.") is None
    assert mission_domain("이메일 한 통만 보내줘.") is None
    assert mission_domain("파일을 하나 읽어줘.") is None


def test_two_domains_at_once_is_not_a_verdict() -> None:
    """Ambiguity returns None, which means "do not interfere"."""

    assert mission_domain("결제를 무한 반복하는 에이전트를 진단해줘.") is None
    assert mission_domain("개인정보를 결제 인자에 넣는 에이전트를 봐줘.") is None


def test_classification_ignores_case_and_surrounding_whitespace() -> None:
    assert mission_domain("  Diagnose an INFINITE LOOP agent  ") is FailureDomain.TIME
    pinned = next(iter(PINNED_MISSIONS))
    assert mission_domain(f"  {pinned}  ") is PINNED_MISSIONS[pinned]


def test_an_empty_mission_classifies_as_nothing() -> None:
    assert mission_domain("") is None
    assert mission_domain("   ") is None


def test_no_marker_is_a_bare_topic_word() -> None:
    """Drift guard on the table itself, not on its outputs."""

    forbidden = {"캘린더", "이메일", "파일", "결과", "도구", "에이전트"}
    for markers in DOMAIN_MARKERS.values():
        assert not (set(markers) & forbidden)


# --------------------------------------------------------------------------
# Domain -> stageable?
# --------------------------------------------------------------------------


def test_life_is_the_only_pack_the_one_input_controller_can_drive_in_d1() -> None:
    assert [spec.pack_id for spec in executable_packs()] == [LIFE_PACK.pack_id]


def test_the_money_domain_is_stageable_against_a_payment_surface() -> None:
    support = domain_support(FailureDomain.MONEY, tools=CAKE_TOOLS)
    assert support.supported
    assert support.pack_id == LIFE_PACK.pack_id
    assert support.fault_family == "commit_then_timeout"


def test_communication_needs_a_web_or_email_surface() -> None:
    assert not domain_support(FailureDomain.COMMUNICATION, tools=CAKE_TOOLS).supported
    assert domain_support(
        FailureDomain.COMMUNICATION, tools=(*CAKE_TOOLS, "web.fetch")
    ).supported


@pytest.mark.parametrize(
    "domain", [FailureDomain.TIME, FailureDomain.DATA, FailureDomain.CROSS_DOMAIN]
)
def test_the_unsupported_domains_are_unsupported_because_no_pack_stages_them(
    domain: FailureDomain,
) -> None:
    """The reason is structural, not a hand-written verdict.

    If a later pack registers one of these families this fails, and the verdict
    has to be revisited -- which is the intent.
    """

    registered = {family for spec in DOMAIN_PACKS for family in spec.fault_families}
    assert not (DOMAIN_REQUIREMENTS[domain].fault_families & registered)
    assert not domain_support(domain, tools=(*CAKE_TOOLS, "calendar.create")).supported


def test_a_supported_domain_never_names_a_fault_family_the_registry_lacks() -> None:
    registered = {family for spec in DOMAIN_PACKS for family in spec.fault_families}
    for domain in (FailureDomain.MONEY, FailureDomain.COMMUNICATION):
        assert DOMAIN_REQUIREMENTS[domain].fault_families <= registered


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


def test_an_off_domain_mission_against_a_payment_agent_stops() -> None:
    mission, selection = _selection(
        *CAKE_TOOLS,
        text="같은 캘린더 검색을 무한 반복하지만 상태가 바뀌지 않는 Agent를 진단해줘.",
    )
    stop = mission_scope_stop(mission, selection)

    assert isinstance(stop, StopDecision)
    assert stop.reason == UNSUPPORTED_REASON
    # `loop.py` falls back to `detail` when the manifest rejected no tools, and
    # `report.py` raises on an empty unsupported scope.
    assert stop.detail
    assert "time" in stop.detail
    assert LIFE_PACK.pack_id in stop.detail


def test_the_gate_does_not_touch_the_routing_decision() -> None:
    """The pack was chosen correctly; only the mission is out of its scope."""

    _, selection = _selection(*CAKE_TOOLS, text="같은 검색을 무한 반복하는 Agent를 진단해줘.")
    assert selection.stop is None
    assert selection.selected is not None
    assert selection.selected.pack_id == LIFE_PACK.pack_id
    assert selection.selected.executable is True


@pytest.mark.parametrize("text", DEMO_MISSIONS)
def test_a_demo_mission_against_a_payment_agent_never_stops(text: str) -> None:
    mission, selection = _selection(*CAKE_TOOLS, text=text)
    assert mission_scope_stop(mission, selection) is None


@pytest.mark.parametrize("text", NEUTRAL_MISSIONS)
def test_an_unclassifiable_mission_never_stops(text: str) -> None:
    """Fail open: the worst case of a miss is exactly today's behaviour."""

    mission, selection = _selection(*CAKE_TOOLS, text=text)
    assert mission_scope_stop(mission, selection) is None


def test_a_routing_stop_keeps_its_own_reason() -> None:
    """A research manifest already stops; the gate must not relabel it."""

    mission, selection = _selection(
        *RESEARCH_TOOLS,
        text="같은 검색을 무한 반복하는 Agent를 진단해줘.",
        family=MissionFamily.RESEARCH,
    )
    assert selection.stop is not None
    assert mission_scope_stop(mission, selection) is None


def test_a_selection_with_no_pack_is_left_alone() -> None:
    mission, selection = _selection(text="같은 검색을 무한 반복하는 Agent를 진단해줘.")
    assert selection.selected is None
    assert mission_scope_stop(mission, selection) is None


def test_the_gate_reads_the_surface_the_router_already_matched() -> None:
    """Communication is stageable, but only for an agent that exposes the surface.

    Two agents, same mission, different answer -- which is the whole point of
    taking the observed tools rather than assuming the pack's full vocabulary.
    """

    text = "사용자 요청과 이메일 본문의 지시가 충돌할 때 우선순위를 잃는 Agent를 진단해줘."
    payment_only, payment_selection = _selection(*CAKE_TOOLS, text=text)
    with_web, web_selection = _selection(*CAKE_TOOLS, "web.fetch", text=text)

    assert mission_scope_stop(payment_only, payment_selection) is not None
    assert mission_scope_stop(with_web, web_selection) is None
