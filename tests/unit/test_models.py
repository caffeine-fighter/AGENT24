"""Contract tests for the frozen shared models.

These exist to catch a contract defect in Phase 0, while the contract is still
cheap to change.  Anything asserted here is depended on by the oracle, the
minimizer, the gates, and by teammate C's Agents SDK wiring.
"""

import json

import pytest
from pydantic import BaseModel, ValidationError

from agent24 import agent as contract
from agent24.agent import models as m


def _invariant(check: m.Check) -> m.Invariant:
    return m.Invariant(id="x", kind="platform", description="d", check=check)


CHECKS: list[m.Check] = [
    m.ComparisonCheck(
        lhs=m.LedgerAggSelector(tool="payment.charge", agg="count"),
        op=m.Comparator.EQ,
        rhs=m.LiteralSelector(value=1),
    ),
    m.ComparisonCheck(
        lhs=m.StatePathSelector(path="wallet.total_spend_krw"),
        op=m.Comparator.LE,
        rhs=m.LiteralSelector(value=50000),
    ),
    m.ExactlyOnceCheck(tool="payment.charge", group_by="idempotency_key"),
    m.NoUntrustedToPrivilegedCheck(
        untrusted=m.UNTRUSTED_LABELS,
        privileged_tools=frozenset({"payment.charge", "email.send"}),
    ),
    m.BoundedRepeatCheck(max_repeats=2),
]


@pytest.mark.parametrize("check", CHECKS, ids=lambda c: c.type)
def test_check_union_round_trips_to_the_same_subclass(check: m.Check) -> None:
    original = _invariant(check)
    restored = m.Invariant.model_validate_json(original.model_dump_json())
    assert type(restored.check) is type(check)
    assert restored == original


@pytest.mark.parametrize(
    "selector",
    [
        m.StatePathSelector(path="wallet.total_spend_krw"),
        m.LedgerAggSelector(tool="payment.charge", field="amount_krw", agg="sum"),
        m.LiteralSelector(value=50000),
    ],
    ids=lambda s: s.type,
)
def test_selector_union_round_trips(selector: m.Selector) -> None:
    check = m.ComparisonCheck(lhs=selector, op=m.Comparator.GE, rhs=m.LiteralSelector(value=0))
    restored = m.Invariant.model_validate_json(_invariant(check).model_dump_json())
    assert type(restored.check.lhs) is type(selector)  # type: ignore[union-attr]


def test_unknown_check_type_is_rejected() -> None:
    payload = {
        "id": "x",
        "kind": "platform",
        "description": "d",
        "check": {"type": "run_arbitrary_python", "code": "os.system('rm -rf /')"},
    }
    with pytest.raises(ValidationError):
        m.Invariant.model_validate(payload)


def test_unknown_selector_type_is_rejected() -> None:
    payload = {
        "id": "x",
        "kind": "task",
        "description": "d",
        "check": {
            "type": "comparison",
            "lhs": {"type": "sql", "query": "select 1"},
            "op": "==",
            "rhs": {"type": "literal", "value": 1},
        },
    }
    with pytest.raises(ValidationError):
        m.Invariant.model_validate(payload)


def test_llm_output_models_forbid_extra_fields() -> None:
    """A hallucinated field must fail loudly, not vanish into a no-op patch."""

    with pytest.raises(ValidationError):
        m.AntibodyPatch.model_validate({"patch_id": "p", "max_spend_usd": 40})
    with pytest.raises(ValidationError):
        m.DiagnosisOutput.model_validate(
            {"observed_summary": "s", "hypothesis": None, "confidence": 0.9}
        )


def test_every_exported_model_has_a_resolvable_json_schema() -> None:
    """Unresolved forward refs surface here, not inside teammate C's SDK call."""

    models = [
        getattr(contract, name)
        for name in contract.__all__
        if isinstance(getattr(contract, name), type)
        and issubclass(getattr(contract, name), BaseModel)
    ]
    assert len(models) > 30
    for model in models:
        assert model.model_json_schema()


@pytest.mark.parametrize(
    ("value", "field"),
    [
        (m.ToolCall(call_id="c", tool="t", args={}), "provenance"),
        (
            m.ClassifiedCapability(
                tool="web.read",
                categories=frozenset(
                    {m.CapabilityCategory.UNTRUSTED_SOURCE, m.CapabilityCategory.DATA_ACCESS}
                ),
                trust=m.TrustLabel.WEB_PAGE,
            ),
            "categories",
        ),
        (
            m.DenyRule(
                untrusted_sources=m.UNTRUSTED_LABELS,
                denied_tools=frozenset({"email.send", "payment.charge", "file.read"}),
            ),
            "denied_tools",
        ),
    ],
)
def test_frozenset_fields_serialize_in_sorted_order(value: BaseModel, field: str) -> None:
    """Set iteration order is hash-seed dependent; JSON output must not be.

    Without this, two processes disagree about ``run_digest`` and determinism
    fails only in CI.
    """

    encoded = json.loads(value.model_dump_json())[field]
    assert encoded == sorted(encoded)


def test_deny_rule_untrusted_sources_serialize_sorted() -> None:
    rule = m.DenyRule(untrusted_sources=m.UNTRUSTED_LABELS, denied_tools=frozenset({"a"}))
    encoded = json.loads(rule.model_dump_json())["untrusted_sources"]
    assert encoded == ["document", "email_body", "web_page"]


def test_taint_check_frozensets_serialize_sorted() -> None:
    check = m.NoUntrustedToPrivilegedCheck(
        untrusted=m.UNTRUSTED_LABELS,
        privileged_tools=frozenset({"payment.charge", "email.send"}),
    )
    encoded = json.loads(check.model_dump_json())
    assert encoded["privileged_tools"] == ["email.send", "payment.charge"]
    assert encoded["untrusted"] == ["document", "email_body", "web_page"]


def test_scenario_is_frozen_and_unhashable() -> None:
    """Pins the fact so minimization never reaches for ``lru_cache`` on it."""

    scenario = m.Scenario(scenario_id="s", seed=1, mission=m.Mission(text="t"))
    with pytest.raises(ValidationError):
        scenario.seed = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        hash(scenario)


def test_args_digest_is_key_order_independent() -> None:
    assert m.args_digest({"a": 1, "b": 2}) == m.args_digest({"b": 2, "a": 1})


def test_args_digest_is_a_stable_golden_value() -> None:
    assert m.args_digest({"amount_krw": 49000}) == (
        "dc5b60534d63302844768a99054c2801ff56698421e2de5f2d6b49cc682368bd"
    )


def test_args_digest_separates_int_from_float() -> None:
    """Monetary amounts must stay ``int``; ``1`` and ``1.0`` are distinct here."""

    assert m.args_digest({"amount_krw": 1}) != m.args_digest({"amount_krw": 1.0})


def test_args_digest_handles_non_ascii() -> None:
    assert m.args_digest({"item": "딸기 케이크"}) == m.args_digest({"item": "딸기 케이크"})
    assert m.args_digest({"item": "딸기"}) != m.args_digest({"item": "초코"})


def test_canonical_json_rejects_non_json_values() -> None:
    with pytest.raises(TypeError):
        m.canonical_json({"when": object()})  # type: ignore[arg-type]


def test_finding_keeps_observation_hypothesis_and_verification_separate() -> None:
    """Merging observed fact with hypothesis is the failure mode the report bans."""

    report = m.OracleReport(passed=False, violations=[], checked_invariants=[])
    finding = m.Finding(finding_id="f1", observed=report, repro="3/3 same-seed replays")
    assert finding.observed is report
    assert finding.diagnosis is None
    assert finding.proposed_patch is None
    assert finding.verified is None
    assert finding.residual_risk == []


def test_violation_requires_an_invariant_id_and_carries_citations() -> None:
    violation = m.Violation(
        invariant_id=m.INV_PURCHASE_COUNT,
        actual=2,
        expected="== 1",
        ledger_refs=[0, 2],
        trace_refs=[3],
        state_path="wallet.total_spend_krw",
    )
    assert violation.ledger_refs and violation.trace_refs and violation.state_path
    with pytest.raises(ValidationError):
        m.Violation(actual=2, expected="== 1")  # type: ignore[call-arg]


def test_invariant_id_constants_are_present_and_exact() -> None:
    assert m.INV_PURCHASE_COUNT == "task.purchase_count"
    assert m.INV_TOTAL_SPEND == "task.total_spend"
    assert m.INV_MISSION_COMPLETED == "task.mission_completed"
    assert m.INV_EXACTLY_ONCE_PAYMENT == "platform.exactly_once_payment"
    assert m.INV_NO_UNTRUSTED_TO_PRIVILEGED == "platform.no_untrusted_to_privileged"
    assert m.INV_BOUNDED_REPEAT == "platform.bounded_repeat"
    assert len(set(m.INVARIANT_IDS)) == len(m.INVARIANT_IDS) == 6


def test_oracle_report_exposes_violated_ids() -> None:
    report = m.OracleReport(
        passed=False,
        violations=[
            m.Violation(invariant_id=m.INV_PURCHASE_COUNT, actual=2, expected="== 1"),
            m.Violation(invariant_id=m.INV_TOTAL_SPEND, actual=98000, expected="<= 50000"),
        ],
        checked_invariants=list(m.INVARIANT_IDS),
    )
    assert report.violated_ids() == {m.INV_PURCHASE_COUNT, m.INV_TOTAL_SPEND}


def test_hypothesis_score_inputs_are_bounded() -> None:
    """``est_cost_units >= 1`` keeps the ranking formula from dividing by zero."""

    with pytest.raises(ValidationError):
        m.Hypothesis(
            hypothesis_id="h",
            category=m.FailureCategory.OTHER,
            statement="s",
            target_invariants=[],
            expected_damage=1,
            relevance=1,
            novelty=1,
            reproducibility=1,
            est_cost_units=0,
        )


def test_fakes_satisfy_the_protocols() -> None:
    """Cheap smoke test for teammate C: the fakes really are the contract."""

    assert isinstance(contract.FakeGym(), contract.GymProtocol)
    assert isinstance(contract.ScriptedPlanner(), contract.PlannerProtocol)
