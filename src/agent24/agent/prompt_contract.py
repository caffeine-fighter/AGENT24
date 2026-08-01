"""Structured mission extraction for side-effect safety.

The user mission is prose, but the values used by an invariant or a payment
policy must not be prose.  This module gives the prose one typed boundary:
Luna extracts a small JSON object, the host validates it, and an explicit
deterministic parser remains available when the provider is unavailable.

The fallback is deliberately conservative.  It is not presented as a Luna
answer, and a provider result that disagrees with an explicit number in the
prompt is rejected in favour of the host parser.  Neither path can widen the
manifest permission ceiling.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

DEFAULT_PROMPT_MODEL = "gpt-5.6-luna"
MAX_PROMPT_CONTRACT_TEXT = 2_000
MAX_ORDER_COUNT = 100
MAX_QUANTITY_PER_ORDER = 100


class PromptContractStatus(StrEnum):
    READY = "ready"
    AMBIGUOUS = "ambiguous"
    PERMISSION_CONFLICT = "permission_conflict"


class PromptExtraction(BaseModel):
    """The strict schema Luna is asked to fill from one mission."""

    model_config = ConfigDict(extra="forbid")

    intent: Literal["purchase", "non_purchase", "unknown"]
    item: str = Field(min_length=1, max_length=120)
    order_count: int = Field(ge=1, le=MAX_ORDER_COUNT)
    quantity_per_order: int = Field(ge=1, le=MAX_QUANTITY_PER_ORDER)
    max_order_price_krw: int | None = Field(default=None, ge=1, le=10_000_000)
    max_total_spend_krw: int | None = Field(default=None, ge=1, le=1_000_000_000)
    budget_scope: Literal["per_order", "total", "unspecified"]
    recipient: str | None = Field(default=None, max_length=80)
    occasion: str | None = Field(default=None, max_length=120)
    calendar_required: bool
    confidence: float = Field(ge=0.0, le=1.0)
    ambiguities: list[str] = Field(default_factory=list, max_length=8)


class PurchasePromptContract(BaseModel):
    """Host-owned, validated meaning of a purchase mission."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["purchase-prompt.v1"] = "purchase-prompt.v1"
    intent: Literal["purchase"] = "purchase"
    item: str = Field(min_length=1, max_length=120)
    order_count: int = Field(ge=1, le=MAX_ORDER_COUNT)
    quantity_per_order: int = Field(ge=1, le=MAX_QUANTITY_PER_ORDER)
    max_order_price_krw: int | None = Field(default=None, ge=1, le=10_000_000)
    max_total_spend_krw: int | None = Field(default=None, ge=1, le=1_000_000_000)
    budget_scope: Literal["per_order", "total", "unspecified"]
    recipient: str | None = Field(default=None, max_length=80)
    occasion: str | None = Field(default=None, max_length=120)
    calendar_required: bool
    source: Literal["luna", "deterministic_fallback"]
    model: str = Field(min_length=1, max_length=120)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: tuple[str, ...] = ()
    status: PromptContractStatus = PromptContractStatus.READY
    permission_conflicts: tuple[str, ...] = ()
    fallback_reason: str | None = None

    @property
    def requested_units(self) -> int:
        return self.order_count * self.quantity_per_order

    @property
    def implied_total_spend_krw(self) -> int | None:
        if self.max_total_spend_krw is not None:
            return self.max_total_spend_krw
        if self.budget_scope == "per_order" and self.max_order_price_krw is not None:
            return self.order_count * self.quantity_per_order * self.max_order_price_krw
        return None

    def to_constraints(self) -> dict[str, JsonValue]:
        """Return JSON-only mission constraints for existing event contracts."""

        serialized_contract = self.model_dump(mode="json")
        serialized_contract["requested_units"] = self.requested_units
        serialized_contract["implied_total_spend_krw"] = self.implied_total_spend_krw
        return {
            "prompt_contract": serialized_contract,
            "purchase_count": self.order_count,
            "quantity_per_order": self.quantity_per_order,
            "requested_units": self.requested_units,
            "item": self.item,
            "max_order_price_krw": self.max_order_price_krw,
            "max_total_spend_krw": self.max_total_spend_krw,
            "implied_total_spend_krw": self.implied_total_spend_krw,
            "budget_scope": self.budget_scope,
            "calendar_required": self.calendar_required,
            "prompt_contract_status": self.status.value,
        }


class PromptExtractionResult(BaseModel):
    """Bounded extraction outcome, including whether Luna was actually used."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract: PurchasePromptContract | None = None
    source: Literal["luna", "deterministic_fallback", "not_purchase"]
    model: str
    luna_attempted: bool
    fallback_reason: str | None = None


class PromptContractExtractionError(RuntimeError):
    """A provider response cannot be safely converted to the schema."""


_NUMBER_WORDS: dict[str, int] = {
    "한": 1,
    "하나": 1,
    "한개": 1,
    "한 개": 1,
    "일": 1,
    "두": 2,
    "둘": 2,
    "두개": 2,
    "두 개": 2,
    "이": 2,
    "세": 3,
    "셋": 3,
    "세개": 3,
    "세 개": 3,
    "삼": 3,
    "네": 4,
    "넷": 4,
    "네개": 4,
    "네 개": 4,
    "사": 4,
    "다섯": 5,
    "오": 5,
    "여섯": 6,
    "육": 6,
    "일곱": 7,
    "칠": 7,
    "여덟": 8,
    "팔": 8,
    "아홉": 9,
    "구": 9,
    "열": 10,
}
_NUMBER_PATTERN = "|".join(sorted(map(re.escape, _NUMBER_WORDS), key=len, reverse=True))
_PURCHASE_MARKER = re.compile(r"(?:주문|구매|사줘|사 줘|결제|order|buy|purchase)", re.I)
_ITEM_PATTERN = re.compile(
    r"(?P<item>[가-힣A-Za-z][가-힣A-Za-z0-9 _-]{0,50}?)\s*"
    r"(?:하나|한|두|둘|세|셋|네|넷|\d+)?\s*"
    r"(?:개|개를|번)?\s*(?:을|를)?\s*(?:주문|구매|사|order|buy)",
    re.I,
)
_PRICE_PATTERN = re.compile(
    r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>억|천만|백만|만|천)?\s*(?:원|krw)?",
    re.I,
)


def _number(value: str) -> int | None:
    normalized = value.strip().replace(" ", "")
    if normalized.isdigit():
        return int(normalized)
    return _NUMBER_WORDS.get(normalized) or _NUMBER_WORDS.get(value.strip())


def _money(text: str) -> tuple[int, str] | None:
    for match in _PRICE_PATTERN.finditer(text):
        raw = match.group("number").replace(",", "")
        try:
            number = float(raw)
        except ValueError:
            continue
        unit = (match.group("unit") or "").lower()
        multiplier = {
            "억": 100_000_000,
            "천만": 10_000_000,
            "백만": 1_000_000,
            "만": 10_000,
            "천": 1_000,
            "": 1,
        }[unit]
        amount = int(number * multiplier)
        if amount > 0:
            return amount, match.group(0).strip()
    return None


def _order_count(text: str) -> tuple[int, str | None]:
    pattern = re.compile(
        rf"(?P<number>\d+|{_NUMBER_PATTERN})\s*(?:번|차례|회)\s*(?:주문|구매|결제|order|buy)?",
        re.I,
    )
    match = pattern.search(text)
    if match:
        return _number(match.group("number")) or 1, match.group(0)
    # English prompts often say "twice" rather than "two times".
    if re.search(r"\btwice\b|두 번|두번", text, re.I):
        return 2, "두 번/twice"
    return 1, None


def _quantity_per_order(text: str, order_count: int) -> tuple[int, str | None]:
    # "두 번 주문" describes two order operations, not quantity two in one
    # operation.  Inspect an item/count phrase only when no order multiplier
    # was stated.
    if order_count > 1:
        return 1, None
    match = re.search(
        rf"(?:케이크|cake|상품|product|item)\s*(?P<number>\d+|{_NUMBER_PATTERN})\s*개",
        text,
        re.I,
    )
    if match:
        return _number(match.group("number")) or 1, match.group(0)
    return 1, None


def _item(text: str) -> str:
    # Keep the canonical catalog query stable for the reviewed synthetic cake
    # surface; the surrounding recipient/occasion/budget words are not the
    # product name.
    if re.search(r"케이크|cake", text, re.I):
        return "cake"
    match = _ITEM_PATTERN.search(text)
    if match:
        value = re.sub(r"\s+", " ", match.group("item")).strip(" 을를")
        if value:
            return value
    match = re.search(
        r"(?:주문|구매|order|buy)\s*(?:해|a|an|the)?\s*"
        r"([가-힣A-Za-z][가-힣A-Za-z0-9 _-]{0,50})",
        text,
        re.I,
    )
    return match.group(1).strip() if match else "item"


def _recipient(text: str) -> str | None:
    match = re.search(
        r"(엄마|아빠|부모님|친구|동료|어머니|아버지|mom|dad|mother|father|friend)",
        text,
        re.I,
    )
    return match.group(1) if match else None


def _occasion(text: str) -> str | None:
    match = re.search(r"(생일|기념일|결혼기념일|birthday|anniversary)", text, re.I)
    return match.group(1) if match else None


def _budget_scope(text: str, price_start: int | None, order_count: int) -> str:
    if price_start is None:
        return "unspecified"
    prefix = text[max(0, price_start - 24) : price_start]
    if re.search(r"총|전체|모두|합계|합쳐|total|altogether|in total", prefix, re.I):
        return "total"
    # A repeated order with a local "under X" phrase is a per-order cap.  The
    # resulting implied total is still checked against the permission ceiling.
    if order_count > 1:
        return "per_order"
    return "total"


def deterministic_purchase_extraction(
    prompt: str,
    *,
    model: str = DEFAULT_PROMPT_MODEL,
    fallback_reason: str | None = None,
) -> PromptExtractionResult:
    """Extract explicit Korean/English purchase quantities without a provider."""

    bounded = str(prompt)[:MAX_PROMPT_CONTRACT_TEXT]
    if not _PURCHASE_MARKER.search(bounded):
        return PromptExtractionResult(
            contract=None,
            source="not_purchase",
            model=model,
            luna_attempted=False,
            fallback_reason=fallback_reason,
        )

    order_count, order_evidence = _order_count(bounded)
    quantity, quantity_evidence = _quantity_per_order(bounded, order_count)
    price = _money(bounded)
    price_value = price[0] if price else None
    price_evidence = price[1] if price else None
    price_start = bounded.find(price_evidence) if price_evidence else None
    scope = _budget_scope(bounded, price_start, order_count)
    max_total = price_value if scope == "total" else None
    evidence = tuple(
        item
        for item in (
            order_evidence,
            f"order_count={order_count}" if order_evidence else None,
            quantity_evidence,
            f"quantity_per_order={quantity}" if quantity_evidence else None,
            price_evidence,
            f"budget_scope={scope}" if price_value is not None else None,
        )
        if item
    )
    confidence = 0.96 if order_evidence and (price_evidence or scope == "unspecified") else 0.78
    ambiguities: list[str] = []
    if price_value is None:
        ambiguities.append("budget_not_explicit")
    if order_evidence is None:
        ambiguities.append("order_count_not_explicit")
    if not evidence:
        ambiguities.append("no_explicit_purchase_fields")
    contract = PurchasePromptContract(
        item=_item(bounded),
        order_count=order_count,
        quantity_per_order=quantity,
        max_order_price_krw=price_value,
        max_total_spend_krw=max_total,
        budget_scope=scope,  # type: ignore[arg-type]
        recipient=_recipient(bounded),
        occasion=_occasion(bounded),
        calendar_required=bool(re.search(r"캘린더|일정|calendar|delivery", bounded, re.I)),
        source="deterministic_fallback",
        model=model,
        confidence=confidence,
        evidence=evidence,
        status=PromptContractStatus.READY,
        fallback_reason=fallback_reason,
    )
    if ambiguities:
        contract = contract.model_copy(
            update={"status": PromptContractStatus.AMBIGUOUS, "permission_conflicts": ()}
        )
    return PromptExtractionResult(
        contract=contract,
        source="deterministic_fallback",
        model=model,
        luna_attempted=False,
        fallback_reason=fallback_reason,
    )


def _contract_from_luna(
    extracted: PromptExtraction,
    *,
    model: str,
) -> PurchasePromptContract | None:
    if extracted.intent != "purchase":
        return None
    return PurchasePromptContract(
        item=extracted.item.strip(),
        order_count=extracted.order_count,
        quantity_per_order=extracted.quantity_per_order,
        max_order_price_krw=extracted.max_order_price_krw,
        max_total_spend_krw=extracted.max_total_spend_krw,
        budget_scope=extracted.budget_scope,
        recipient=extracted.recipient.strip() if extracted.recipient else None,
        occasion=extracted.occasion.strip() if extracted.occasion else None,
        calendar_required=extracted.calendar_required,
        source="luna",
        model=model,
        confidence=extracted.confidence,
        evidence=tuple(extracted.ambiguities),
        status=(
            PromptContractStatus.AMBIGUOUS
            if extracted.ambiguities
            else PromptContractStatus.READY
        ),
    )


def _explicit_fields_agree(
    deterministic: PurchasePromptContract,
    luna: PurchasePromptContract,
) -> bool:
    """Do not let a model overwrite an explicit count or price in the text."""

    has_explicit_order_count = any(
        item.startswith("order_count=") for item in deterministic.evidence
    )
    if has_explicit_order_count and deterministic.order_count != luna.order_count:
        return False
    has_explicit_quantity = any(
        item.startswith("quantity_per_order=") for item in deterministic.evidence
    )
    if has_explicit_quantity and deterministic.quantity_per_order != luna.quantity_per_order:
        return False
    if deterministic.max_order_price_krw is not None:
        model_price = luna.max_order_price_krw or luna.max_total_spend_krw
        if model_price != deterministic.max_order_price_krw:
            return False
    explicit_scope = next(
        (
            item.split("=", 1)[1]
            for item in deterministic.evidence
            if item.startswith("budget_scope=")
        ),
        None,
    )
    if explicit_scope is not None and luna.budget_scope != explicit_scope:
        return False
    if (
        deterministic.implied_total_spend_krw is not None
        and luna.implied_total_spend_krw != deterministic.implied_total_spend_krw
    ):
        return False
    return True


class PromptContractExtractor:
    """Use the configured Luna client, with a typed local fallback."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_PROMPT_MODEL,
        openai_client: Any | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 8.0,
    ) -> None:
        self.model = model
        self.openai_client = openai_client
        self.api_key = api_key
        self.timeout_seconds = max(0.1, min(float(timeout_seconds), 30.0))

    @property
    def configured(self) -> bool:
        return self.openai_client is not None or bool((self.api_key or "").strip())

    async def _client(self) -> Any:
        if self.openai_client is not None:
            return self.openai_client
        from openai import AsyncOpenAI

        return AsyncOpenAI(api_key=self.api_key)

    async def _luna(self, prompt: str) -> PurchasePromptContract | None:
        client = await self._client()
        responses = getattr(client, "responses", None)
        parse = getattr(responses, "parse", None)
        if parse is None:
            raise PromptContractExtractionError("structured_parse_unavailable")
        response = await parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Extract a purchase mission into the supplied schema. "
                        "Preserve explicit quantities and KRW limits. "
                        "Interpret 'twice'/'두 번' as two separate order operations. "
                        "Do not infer a budget that is not in the user text."
                    ),
                },
                {"role": "user", "content": prompt[:MAX_PROMPT_CONTRACT_TEXT]},
            ],
            text_format=PromptExtraction,
        )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise PromptContractExtractionError("structured_output_missing")
        if not isinstance(parsed, PromptExtraction):
            parsed = PromptExtraction.model_validate(parsed)
        return _contract_from_luna(parsed, model=self.model)

    async def extract(self, prompt: str) -> PromptExtractionResult:
        deterministic = deterministic_purchase_extraction(prompt, model=self.model)
        if deterministic.source == "not_purchase":
            return deterministic
        if not self.configured:
            reason = "openai_key_missing"
            return deterministic.model_copy(
                update={
                    "fallback_reason": reason,
                    "contract": (
                        deterministic.contract.model_copy(update={"fallback_reason": reason})
                        if deterministic.contract is not None
                        else None
                    ),
                }
            )
        try:
            luna = await asyncio.wait_for(self._luna(prompt), timeout=self.timeout_seconds)
            if luna is not None and (
                deterministic.contract is None
                or _explicit_fields_agree(deterministic.contract, luna)
            ):
                return PromptExtractionResult(
                    contract=luna,
                    source="luna",
                    model=self.model,
                    luna_attempted=True,
                )
            reason = "luna_conflicts_with_explicit_text"
        except TimeoutError:
            reason = "luna_timeout"
        except Exception:  # noqa: BLE001 - provider detail must not cross the boundary
            reason = "luna_provider_or_schema_error"
        return deterministic.model_copy(
            update={
                "luna_attempted": True,
                "fallback_reason": reason,
                "contract": (
                    deterministic.contract.model_copy(update={"fallback_reason": reason})
                    if deterministic.contract is not None
                    else None
                ),
            }
        )


def apply_permission_ceiling(
    contract: PurchasePromptContract,
    permissions: Mapping[str, Any],
) -> PurchasePromptContract:
    """Mark a prompt/manifest conflict without ever widening the manifest cap."""

    conflicts: list[str] = []
    max_count = permissions.get("max_purchase_count")
    if isinstance(max_count, int) and not isinstance(max_count, bool):
        if contract.order_count > max_count:
            conflicts.append(
                f"requested_order_count={contract.order_count} exceeds "
                f"permission_max_purchase_count={max_count}"
            )

    declared_total = permissions.get("max_total_spend_krw")
    if not isinstance(declared_total, int) or isinstance(declared_total, bool):
        # Legacy manifests called this total budget ``max_spend_krw``.  Keep
        # that interpretation at the permission boundary; prompt semantics are
        # stored separately as max_order_price_krw.
        declared_total = permissions.get("max_spend_krw")
    requested_total = contract.implied_total_spend_krw
    if (
        isinstance(declared_total, int)
        and not isinstance(declared_total, bool)
        and requested_total is not None
        and requested_total > declared_total
    ):
        conflicts.append(
            f"requested_total_budget={requested_total} exceeds "
            f"permission_max_total_spend={declared_total}"
        )

    if not conflicts:
        return contract
    return contract.model_copy(
        update={
            "status": PromptContractStatus.PERMISSION_CONFLICT,
            "permission_conflicts": tuple(conflicts),
        }
    )


def contract_constraints(
    prompt: str,
    *,
    permissions: Mapping[str, Any] | None = None,
    extraction: PromptExtractionResult | None = None,
) -> dict[str, JsonValue]:
    """Synchronous convenience projection used by deterministic callers."""

    result = extraction or deterministic_purchase_extraction(prompt)
    if result.contract is None:
        return dict(permissions or {})
    contract = apply_permission_ceiling(result.contract, permissions or {})
    merged: dict[str, JsonValue] = dict(permissions or {})
    merged.update(contract.to_constraints())
    return merged


__all__ = [
    "DEFAULT_PROMPT_MODEL",
    "MAX_PROMPT_CONTRACT_TEXT",
    "PromptContractExtractionError",
    "PromptContractExtractor",
    "PromptContractStatus",
    "PromptExtraction",
    "PromptExtractionResult",
    "PurchasePromptContract",
    "apply_permission_ceiling",
    "contract_constraints",
    "deterministic_purchase_extraction",
]
