from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent24.agent.invariants import life_v0_invariants
from agent24.agent.loop import _patch_for
from agent24.agent.models import FailureCategory
from agent24.agent.prompt_contract import (
    PromptContractExtractor,
    PromptContractStatus,
    PromptExtraction,
    contract_constraints,
    deterministic_purchase_extraction,
)

PROMPT = "엄마 생일 케이크 하나를 5만원 이하로 두 번 주문해."


def test_deterministic_projection_keeps_order_count_and_budget_scope() -> None:
    result = deterministic_purchase_extraction(PROMPT)

    assert result.source == "deterministic_fallback"
    assert result.contract is not None
    assert result.contract.item == "cake"
    assert result.contract.order_count == 2
    assert result.contract.quantity_per_order == 1
    assert result.contract.max_order_price_krw == 50_000
    assert result.contract.budget_scope == "per_order"
    assert result.contract.implied_total_spend_krw == 100_000


def test_permission_ceiling_is_marked_without_widening_the_manifest() -> None:
    constraints = contract_constraints(
        PROMPT,
        permissions={"max_spend_krw": 50_000, "max_purchase_count": 1},
    )

    contract = constraints["prompt_contract"]
    assert isinstance(contract, dict)
    assert contract["order_count"] == 2
    assert contract["max_order_price_krw"] == 50_000
    assert contract["implied_total_spend_krw"] == 100_000
    assert contract["status"] == PromptContractStatus.PERMISSION_CONFLICT.value
    assert constraints["max_spend_krw"] == 50_000
    assert constraints["max_purchase_count"] == 1


def test_invariants_and_patch_use_the_extracted_count() -> None:
    invariants = life_v0_invariants(max_spend_krw=100_000, purchase_count=2)
    purchase_check = invariants[0].check
    assert purchase_check.rhs.value == 2  # type: ignore[union-attr]
    assert invariants[2].check.rhs.value == 2  # type: ignore[union-attr]

    patch = _patch_for(
        FailureCategory.DUPLICATE_SIDE_EFFECT,
        max_spend_krw=100_000,
        purchase_count=2,
    )
    assert patch is not None
    assert patch.max_purchase_count == 2
    assert patch.max_spend_krw == 100_000


class _ParseResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def parse(self, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            output_parsed=PromptExtraction(
                intent="purchase",
                item="cake",
                order_count=2,
                quantity_per_order=1,
                max_order_price_krw=50_000,
                max_total_spend_krw=None,
                budget_scope="per_order",
                recipient="엄마",
                occasion="생일",
                calendar_required=False,
                confidence=0.99,
                ambiguities=[],
            )
        )


class _ParseClient:
    def __init__(self) -> None:
        self.responses = _ParseResponses()


@pytest.mark.asyncio
async def test_configured_luna_uses_structured_parse_and_preserves_model_name() -> None:
    client = _ParseClient()
    result = await PromptContractExtractor(
        model="gpt-5.6-luna",
        openai_client=client,
    ).extract(PROMPT)

    assert result.source == "luna"
    assert result.luna_attempted is True
    assert result.contract is not None
    assert result.contract.order_count == 2
    assert client.responses.calls[0]["model"] == "gpt-5.6-luna"
    assert client.responses.calls[0]["text_format"] is PromptExtraction
