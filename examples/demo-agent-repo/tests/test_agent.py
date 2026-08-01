from __future__ import annotations

from typing import Any

import pytest

from example_agent import create_agent


class ScenarioTools:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.charge_count = 0
        self.payment_effects = 0

    def catalog_search(self, *, query: str, max_price_krw: int | None) -> dict[str, Any]:
        self.calls.append(
            (
                "catalog.search",
                {"query": query, "max_price_krw": max_price_krw},
            )
        )
        return {
            "ok": True,
            "query": query,
            "items": [
                {
                    "product_id": "cake-49k",
                    "title": "Birthday Cake",
                    "price_krw": 49_000,
                    "available": True,
                }
            ],
        }

    def payment_charge(
        self,
        *,
        product_id: str,
        quantity: int,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self.charge_count += 1
        self.calls.append(
            (
                "payment.charge",
                {
                    "product_id": product_id,
                    "quantity": quantity,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        if self.charge_count == 1:
            self.payment_effects += 1
        if self.scenario == "protected" and self.charge_count > 1:
            return {
                "ok": True,
                "status": "committed",
                "payment_id": "payment-0001",
                "order_id": "order-0001",
                "charged_krw": 49_000,
                "idempotent_replay": True,
            }
        if self.scenario == "clean" or self.charge_count > 1 and self.scenario == "vulnerable":
            if self.charge_count > 1:
                self.payment_effects += 1
            return {
                "ok": True,
                "status": "committed",
                "payment_id": f"payment-{self.charge_count:04d}",
                "order_id": f"order-{self.charge_count:04d}",
                "charged_krw": 49_000,
                "idempotent_replay": False,
            }
        return {
            "ok": False,
            "status": "unknown",
            "error": {"code": "TIMEOUT", "message": "outcome is unknown"},
        }

    def payment_status(
        self,
        *,
        payment_id: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "payment.status",
                {"payment_id": payment_id, "idempotency_key": idempotency_key},
            )
        )
        return {"found": False, "status": "absent"}

    def calendar_create(
        self,
        *,
        title: str,
        start_at: str,
        end_at: str | None,
        timezone: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "calendar.create",
                {
                    "title": title,
                    "start_at": start_at,
                    "end_at": end_at,
                    "timezone": timezone,
                    "idempotency_key": idempotency_key,
                },
            )
        )
        return {
            "ok": True,
            "status": "created",
            "event_id": "event-0001",
            "idempotent_replay": False,
        }


@pytest.mark.parametrize(
    ("scenario", "expected_status", "expected_charges", "expected_effects", "expected_calendar"),
    [
        ("clean", "completed", 1, 1, True),
        ("vulnerable", "completed", 2, 2, True),
        ("protected", "completed", 2, 1, True),
    ],
)
def test_same_agent_and_contract_run_in_all_harness_modes(
    scenario: str,
    expected_status: str,
    expected_charges: int,
    expected_effects: int,
    expected_calendar: bool,
) -> None:
    tools = ScenarioTools(scenario)

    result = create_agent().order_one_cake(tools)

    assert result["status"] == expected_status
    assert tools.charge_count == expected_charges
    assert tools.payment_effects == expected_effects
    assert [name for name, _ in tools.calls].count("calendar.create") == int(expected_calendar)
    assert [name for name, _ in tools.calls].count("payment.status") == 0
    assert all(
        set(arguments)
        == {
            "query",
            "max_price_krw",
        }
        for name, arguments in tools.calls
        if name == "catalog.search"
    )
    assert all(
        set(arguments) == {"product_id", "quantity", "idempotency_key"}
        for name, arguments in tools.calls
        if name == "payment.charge"
    )
    assert all(
        set(arguments)
        == {"title", "start_at", "end_at", "timezone", "idempotency_key"}
        for name, arguments in tools.calls
        if name == "calendar.create"
    )


def test_malformed_tool_result_fails_closed_without_follow_on_side_effects() -> None:
    class MalformedCatalog(ScenarioTools):
        def catalog_search(self, *, query: str, max_price_krw: int | None) -> dict[str, Any]:
            self.calls.append(("catalog.search", {"query": query, "max_price_krw": max_price_krw}))
            del query, max_price_krw
            return {"ok": True, "query": "birthday cake", "items": [], "unexpected": True}

    tools = MalformedCatalog("clean")

    assert create_agent().order_one_cake(tools) == {
        "status": "failed",
        "reason": "tool_contract_mismatch",
    }
    assert [name for name, _ in tools.calls] == ["catalog.search"]
