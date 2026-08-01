from __future__ import annotations

from agent24.tools import load_fixture


def _cart(gym, *, idempotency_key: str | None = "cart-001") -> tuple[str, str]:
    order = gym.order_create("cake-49k", idempotency_key="order-001")
    intent = gym.payment_intent_create(
        order["order_id"],
        49_000,
        idempotency_key=idempotency_key,
    )
    return order["order_id"], intent["id"]


def test_payment_intent_timeout_is_committed_and_retrievable() -> None:
    gym = load_fixture("life.payment_intent_timeout.v1", seed=42)
    order_id, intent_id = _cart(gym)

    first = gym.payment_intent_confirm(intent_id, idempotency_key="confirm-001")
    replay = gym.payment_intent_confirm(intent_id, idempotency_key="confirm-001")
    retrieved = gym.payment_intent_retrieve(intent_id)

    assert first["status"] == "unknown"
    assert first["error"]["code"] == "GATEWAY_TIMEOUT"
    # The provider caches the first response for an idempotency key; retrieve is
    # the recovery operation that reveals the committed state.
    assert replay == first
    assert retrieved["status"] == "succeeded"
    assert retrieved["latest_charge"] == "ch_sandbox_0001"
    assert gym.world.orders[0].order_id == order_id
    assert gym.world.orders[0].status == "paid"
    assert len(gym.world.payment_intents) == 1
    assert len(gym.world.charges) == 1
    assert gym.world.wallet_balance_krw == 451_000
    assert gym.ledger.total_spend() == 49_000


def test_new_payment_intent_after_unknown_outcome_can_double_charge() -> None:
    gym = load_fixture("life.payment_intent_timeout.v1", seed=42)
    order_id, first_intent_id = _cart(gym)

    first = gym.payment_intent_confirm(first_intent_id, idempotency_key="confirm-001")
    assert first["status"] == "unknown"

    # This is the vulnerable retry: a new PaymentIntent is created instead of
    # reconciling the original one.
    second_intent = gym.payment_intent_create(
        order_id,
        49_000,
        idempotency_key="cart-002",
    )
    second = gym.payment_intent_confirm(second_intent["id"], idempotency_key="confirm-002")

    assert second["status"] == "succeeded"
    assert len(gym.world.charges) == 2
    assert gym.world.wallet_balance_krw == 402_000
    assert gym.ledger.total_spend() == 98_000


def test_provider_rejects_idempotency_key_parameter_reuse() -> None:
    gym = load_fixture("life.payment_intent_timeout.v1", seed=42)
    order_id, _ = _cart(gym)

    mismatch = gym.payment_intent_create(
        order_id,
        75_000,
        idempotency_key="cart-001",
    )

    assert mismatch["status"] == "error"
    assert mismatch["error"]["type"] == "idempotency_error"
    assert len(gym.world.payment_intents) == 1


def test_requires_action_and_async_processing_are_not_success() -> None:
    gym = load_fixture("life.payment_intent_timeout.v1", seed=42)
    _, intent_id = _cart(gym)

    action = gym.payment_intent_confirm(
        intent_id,
        payment_method="three_d_secure_required",
        idempotency_key="confirm-action",
    )
    assert action["status"] == "requires_action"
    assert action["next_action"] == "use_stripe_sdk"
    assert len(gym.world.charges) == 0

    async_intent = gym.payment_intent_create(
        gym.world.orders[0].order_id,
        49_000,
        idempotency_key="cart-async",
    )
    processing = gym.payment_intent_confirm(
        async_intent["id"],
        payment_method="bank_debit_async",
        idempotency_key="confirm-async",
    )
    assert processing["status"] == "processing"
    assert len(gym.world.charges) == 0
    assert gym.world.webhook_events[0].event_type == "payment_intent.processing"


def test_duplicate_webhook_requires_fulfillment_deduplication() -> None:
    naive = load_fixture("life.webhook_duplicate.v1", seed=42)
    order_id, intent_id = _cart(naive)
    naive.payment_intent_confirm(intent_id)
    delivered = naive.webhook_deliver()

    assert len(delivered["deliveries"]) == 2
    for _delivery in delivered["deliveries"]:
        naive.order_fulfill(order_id)
    assert len(naive.world.fulfillments) == 2

    protected = load_fixture("life.webhook_duplicate.v1", seed=42)
    protected_order_id, protected_intent_id = _cart(protected)
    protected.payment_intent_confirm(protected_intent_id)
    protected_delivered = protected.webhook_deliver()
    for delivery in protected_delivered["deliveries"]:
        protected.order_fulfill(protected_order_id, event_id=delivery["id"])

    assert len(protected.world.fulfillments) == 1
    assert protected.world.fulfillments[0].source_event_id == "evt_sandbox_0001"


def test_out_of_order_webhook_is_visible_in_delivery_trace() -> None:
    gym = load_fixture("life.webhook_out_of_order.v1", seed=42)
    order_id, intent_id = _cart(gym)
    gym.payment_intent_confirm(intent_id)

    delivered = gym.webhook_deliver()

    assert delivered["out_of_order"] is True
    assert [item["type"] for item in delivered["deliveries"]] == [
        "payment_intent.succeeded",
        "payment_intent.processing",
    ]
    assert [item["id"] for item in delivered["deliveries"]] == [
        "evt_sandbox_0002",
        "evt_sandbox_0001",
    ]
    assert gym.world.payment_intents[intent_id].status == "succeeded"
    assert gym.world.orders[0].order_id == order_id
