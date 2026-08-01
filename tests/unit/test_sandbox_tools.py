from __future__ import annotations

from agent24.tools import load_fixture


def test_cake_collision_commits_once_then_returns_ambiguous_timeout() -> None:
    gym = load_fixture("life.cake_collision.v1", seed=42)

    first = gym.payment_charge("cake-49k")
    second = gym.payment_charge("cake-49k")

    assert first["status"] == "unknown"
    assert first["error"]["code"] == "TIMEOUT"
    assert second["status"] == "committed"
    assert len(gym.world.orders) == 2
    assert gym.world.wallet_balance_krw == 402_000
    assert len(gym.ledger.entries) == 2
    assert gym.ledger.total_spend() == 98_000
    assert gym.evidence()["fault_applications"] == [
        {
            "fault_id": "fault.payment.commit-timeout.v1",
            "type": "commit_then_timeout",
            "target": "payment.charge",
            "call_index": 1,
        }
    ]


def test_idempotency_and_status_reconcile_after_timeout() -> None:
    gym = load_fixture("life.cake_collision.v1", seed=42)

    first = gym.payment_charge("cake-49k", idempotency_key="cake-order-42")
    status = gym.payment_status(idempotency_key="cake-order-42")
    second = gym.payment_charge("cake-49k", idempotency_key="cake-order-42")

    assert first["status"] == "unknown"
    assert status == {
        "found": True,
        "status": "committed",
        "payment_id": "payment-0001",
        "order_id": "order-0001",
        "charged_krw": 49_000,
    }
    assert second["idempotent_replay"] is True
    assert len(gym.world.orders) == 1
    assert gym.world.wallet_balance_krw == 451_000
    assert len(gym.ledger.entries) == 1


def test_other_side_effects_are_recorded_and_fault_fixtures_are_deterministic() -> None:
    gym = load_fixture("life.cake_collision.v1", seed=42)
    email = gym.email_send("family@example.test", "Cake", "Pickup at 6pm")
    event = gym.calendar_create("Birthday", "2026-08-01T18:00:00+09:00")
    file_result = gym.file_write("/notes/cake.txt", "cake-49k")

    assert email["status"] == "sent"
    assert event["status"] == "created"
    assert file_result["status"] == "written"
    assert [entry.tool for entry in gym.ledger.entries] == [
        "email.send",
        "calendar.create",
        "file.write",
    ]

    injected = load_fixture("life.web_injection.v1", seed=42).web_fetch(
        "https://synthetic.shop/cake-49k"
    )
    empty = load_fixture("life.empty_search.v1", seed=42).catalog_search("cake")
    assert injected["injection_present"] is True
    assert empty["items"] == []


def test_function_tool_surface_keeps_dotted_manifest_as_stable_contract() -> None:
    gym = load_fixture("life.cake_collision.v1")
    names = {tool.name for tool in gym.tools()}

    assert names == {
        "catalog_search",
        "payment_charge",
        "payment_status",
        "payment_intent_create",
        "payment_intent_confirm",
        "payment_intent_retrieve",
        "order_create",
        "webhook_deliver",
        "order_fulfill",
        "web_fetch",
        "web_search",
        "email_send",
        "calendar_create",
        "file_write",
    }
