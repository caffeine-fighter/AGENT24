"""Unit tests for P0 experiment selection.

The invariant under test is that selection is driven by BehaviorProfile fields
and their evidence — never by keywords, tool-name substrings, or README text —
and that an unsupported input terminates honestly instead of degrading into
some experiment the gym happens to be able to run.
"""

import pytest

from agent24.agent import (
    CAKE_BUYER_TOOLS,
    PURCHASE_MISSION,
    ExperimentPlan,
    FailureCategory,
    FaultKind,
    Mission,
    MissionFamily,
    StopDecision,
    ToolSpec,
)
from agent24.agent.planner import (
    GYM_TOOLS,
    SUPPORTED_OPERATORS,
    candidate_operators,
    hypothesis_for,
    select_p0_experiment,
    unsupported_tools_of,
)
from agent24.agent.profile import (
    AgentManifest,
    BehaviorProfile,
    EvidenceRef,
    build_behavior_profile,
    determined,
    unknown,
)

TRACE_REF = EvidenceRef(kind="trace", trace_index=3, detail="관찰된 근거")


def profile_with(**overrides) -> BehaviorProfile:
    """A cake-buyer profile with every capability field explicitly set."""

    base = dict(
        agent_name="cake-buyer",
        source_ref="github.com/example/cake-buyer@abc123",
        mission_family=MissionFamily.PURCHASE,
        protected_assets=["calendar", "files", "inbox", "wallet"],
        side_effect_tools=["calendar.create", "email.send", "payment.charge"],
        permissions={"max_spend_krw": 50000},
        capabilities=build_behavior_profile(
            AgentManifest(name="c", source_ref="r", tools=list(CAKE_BUYER_TOOLS))
        ).capabilities,
        retry_behavior=unknown("관찰되지 않음"),
        idempotency_usage=determined("absent", TRACE_REF),
        reconciliation_usage=determined("absent", TRACE_REF),
        untrusted_input_handling=determined("present", TRACE_REF),
        loop_budget=determined("present", TRACE_REF),
        baseline_observed=True,
    )
    base.update(overrides)
    return BehaviorProfile(**base)


# --------------------------------------------------------------------------
# The P0 signature choice
# --------------------------------------------------------------------------


def test_missing_idempotency_selects_commit_then_timeout_on_payment() -> None:
    plan = select_p0_experiment(profile_with(), PURCHASE_MISSION)

    assert isinstance(plan, ExperimentPlan)
    assert len(plan.scenario.faults) == 1
    fault = plan.scenario.faults[0]
    assert fault.fault is FaultKind.COMMIT_THEN_TIMEOUT
    assert fault.target_tool == "payment.charge"
    assert plan.single_variable is True


def test_unknown_idempotency_also_selects_the_payment_experiment() -> None:
    """An unknown is a reason to probe, not a reason to skip."""

    plan = select_p0_experiment(
        profile_with(idempotency_usage=unknown("baseline에 결제 호출이 없다")), PURCHASE_MISSION
    )
    assert isinstance(plan, ExperimentPlan)
    assert plan.scenario.faults[0].fault is FaultKind.COMMIT_THEN_TIMEOUT


def test_established_idempotency_is_not_a_p0_target() -> None:
    """A capability observed working shouldn't consume the budget.

    With payment handling confirmed, selection must move to a different
    operator rather than re-testing what we already watched succeed.
    """

    plan = select_p0_experiment(
        profile_with(
            idempotency_usage=determined("present", TRACE_REF),
            reconciliation_usage=determined("present", TRACE_REF),
            untrusted_input_handling=unknown("untrusted 노출 없음"),
        ),
        PURCHASE_MISSION,
    )
    assert isinstance(plan, ExperimentPlan)
    assert plan.scenario.faults[0].fault is FaultKind.MALICIOUS_WEB_CONTENT


# --------------------------------------------------------------------------
# 완료 조건: selection is field-driven, and the reasoning survives
# --------------------------------------------------------------------------


def test_choice_reason_names_the_profile_field_and_its_evidence() -> None:
    plan = select_p0_experiment(profile_with(), PURCHASE_MISSION)
    assert isinstance(plan, ExperimentPlan)

    assert "profile.idempotency_usage=absent" in plan.tool_choice_reason
    assert "trace#3" in plan.tool_choice_reason
    assert "관찰된 근거" in plan.tool_choice_reason


def test_expected_evidence_states_the_observation_and_the_budget() -> None:
    plan = select_p0_experiment(profile_with(), PURCHASE_MISSION)
    assert isinstance(plan, ExperimentPlan)

    assert "ledger" in plan.expected_evidence
    assert "task.purchase_count" in plan.expected_evidence
    assert f"최대 {plan.scenario.max_turns} turn" in plan.expected_evidence


def test_budget_is_recorded_as_max_turns_plus_cost_units() -> None:
    profile = profile_with()
    plan = select_p0_experiment(profile, PURCHASE_MISSION)
    assert isinstance(plan, ExperimentPlan)

    hypothesis = hypothesis_for(plan, profile, PURCHASE_MISSION)
    assert hypothesis is not None
    assert plan.scenario.max_turns > 0
    assert 1 <= hypothesis.est_cost_units <= 5
    assert hypothesis.category is FailureCategory.DUPLICATE_SIDE_EFFECT


def test_unknown_gate_is_flagged_as_not_yet_a_confirmed_weakness() -> None:
    """Guards the honesty rule: a probe is not a finding."""

    plan = select_p0_experiment(
        profile_with(idempotency_usage=unknown("baseline에 결제 호출이 없다")), PURCHASE_MISSION
    )
    assert isinstance(plan, ExperimentPlan)
    assert "unknown" in plan.tool_choice_reason
    assert "단정하지 않는다" in plan.tool_choice_reason


def test_priority_score_follows_the_concept_doc_formula() -> None:
    profile = profile_with()
    candidates = candidate_operators(profile, PURCHASE_MISSION)
    assert candidates

    top = candidates[0]
    h = top.hypothesis
    expected = (h.expected_damage * h.relevance * h.novelty * h.reproducibility) // (
        h.est_cost_units
    )
    assert top.score == expected

    # Gate confidence is the primary key; score orders within each confidence
    # tier rather than across them.
    assert [c.gate_priority for c in candidates] == sorted(c.gate_priority for c in candidates)
    for tier in (0, 1):
        scores = [c.score for c in candidates if c.gate_priority == tier]
        assert scores == sorted(scores, reverse=True)


def test_observed_weakness_outranks_an_unobserved_gap() -> None:
    """An unknown scores higher on novelty but must not win the slot.

    Both operators are eligible; only idempotency was actually measured failing.
    Picking the injection probe here would spend the P0 budget on a guess while
    a confirmed money bug sat unexercised.
    """

    profile = profile_with(
        idempotency_usage=determined("absent", TRACE_REF),
        untrusted_input_handling=unknown("untrusted 노출 없음"),
    )
    candidates = candidate_operators(profile, PURCHASE_MISSION)
    by_id = {c.operator_id: c for c in candidates}

    injection = by_id["malicious_web_content.web_read"]
    payment = by_id["commit_then_timeout.payment"]
    assert injection.score > payment.score, "unknown should still score higher on novelty"
    assert candidates[0].operator_id == "commit_then_timeout.payment"


def test_selection_is_deterministic() -> None:
    profile = profile_with()
    first = select_p0_experiment(profile, PURCHASE_MISSION)
    second = select_p0_experiment(profile, PURCHASE_MISSION)
    assert isinstance(first, ExperimentPlan) and isinstance(second, ExperimentPlan)
    assert first.model_dump_json() == second.model_dump_json()


def test_selection_does_not_read_readme_or_tool_name_text() -> None:
    """Two profiles differing only in prose must select identically.

    This is the "fixed keyword가 아니라 profile 필드를 근거로" criterion, tested
    by making the prose actively misleading.
    """

    honest = build_behavior_profile(
        AgentManifest(
            name="a", source_ref="r", tools=list(CAKE_BUYER_TOOLS), readme_text="fully idempotent"
        )
    )
    alarming = build_behavior_profile(
        AgentManifest(
            name="a",
            source_ref="r",
            tools=list(CAKE_BUYER_TOOLS),
            readme_text="WARNING: double charges, prompt injection, infinite loops",
        )
    )
    first = select_p0_experiment(honest, PURCHASE_MISSION)
    second = select_p0_experiment(alarming, PURCHASE_MISSION)
    assert isinstance(first, ExperimentPlan) and isinstance(second, ExperimentPlan)
    assert first.plan_id == second.plan_id
    assert first.scenario.faults == second.scenario.faults


# --------------------------------------------------------------------------
# Honest termination
# --------------------------------------------------------------------------


def test_tools_outside_the_gym_vocabulary_terminate_as_unsupported() -> None:
    outside = build_behavior_profile(
        AgentManifest(
            name="research-bot",
            source_ref="r",
            tools=[ToolSpec(name="arxiv.search"), ToolSpec(name="notion.write", side_effect=True)],
        )
    )
    decision = select_p0_experiment(
        outside, Mission(text="논문 조사", family=MissionFamily.RESEARCH)
    )

    assert isinstance(decision, StopDecision)
    assert decision.stop is True
    assert decision.reason == "unsupported_input"
    assert "arxiv.search" in decision.detail
    assert unsupported_tools_of(outside) == ["arxiv.search", "notion.write"]


def test_empty_manifest_terminates_as_unsupported() -> None:
    empty = build_behavior_profile(AgentManifest(name="nothing", source_ref="r"))
    decision = select_p0_experiment(empty, PURCHASE_MISSION)

    assert isinstance(decision, StopDecision)
    assert decision.reason == "unsupported_input"
    assert "도구가 없어" in decision.detail


def test_everything_already_established_terminates_rather_than_inventing_work() -> None:
    decision = select_p0_experiment(
        profile_with(
            idempotency_usage=determined("present", TRACE_REF),
            untrusted_input_handling=determined("present", TRACE_REF),
            loop_budget=determined("present", TRACE_REF),
        ),
        PURCHASE_MISSION,
    )
    assert isinstance(decision, StopDecision)
    assert decision.reason == "unsupported_input"


def test_unsupported_decision_never_carries_a_fabricated_finding() -> None:
    """`unsupported` must not be dressed up as a result."""

    empty = build_behavior_profile(AgentManifest(name="nothing", source_ref="r"))
    decision = select_p0_experiment(empty, PURCHASE_MISSION)
    assert isinstance(decision, StopDecision)
    assert not isinstance(decision, ExperimentPlan)
    assert decision.detail
    for word in ("취약", "실패를 발견", "vulnerable"):
        assert word not in decision.detail


# --------------------------------------------------------------------------
# Operator allowlist
# --------------------------------------------------------------------------


def test_every_allowlisted_operator_targets_a_gym_tool() -> None:
    for operator in SUPPORTED_OPERATORS:
        assert operator.target_tool in GYM_TOOLS
        assert operator.requires_tools <= GYM_TOOLS
        assert operator.target_invariants


def test_operator_ids_are_unique() -> None:
    ids = [operator.operator_id for operator in SUPPORTED_OPERATORS]
    assert len(ids) == len(set(ids))


def test_gate_fields_exist_on_the_profile() -> None:
    """The allowlist can only gate on capabilities a profile actually reports."""

    fields = profile_with().assessments()
    for operator in SUPPORTED_OPERATORS:
        assert operator.gate_field in fields


@pytest.mark.parametrize("operator", SUPPORTED_OPERATORS, ids=lambda o: o.operator_id)
def test_loop_operator_repeats_and_others_do_not(operator) -> None:
    """Only the loop operator faults every matching call."""

    if operator.fault is FaultKind.EMPTY_RESULT:
        assert operator.fault_params == {"repeat": True}
        assert operator.max_turns == 6
    else:
        assert operator.fault_params == {}
