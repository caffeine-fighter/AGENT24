"""Stripe-like synthetic payment provider semantics.

This module intentionally models the resource lifecycle used by common hosted
payment APIs without making a network request.  The names are provider-neutral
in the public sandbox, while the state machine follows PaymentIntent-style
create/confirm/retrieve and webhook delivery behavior.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from typing import Any

from .faults import FaultInjector
from .ledger import SideEffectLedger
from .world import Charge, Fulfillment, PaymentIntent, WebhookEvent, WorldState


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class StripeLikePaymentProvider:
    """A local PaymentIntent-style provider with realistic retry boundaries."""

    def __init__(
        self,
        *,
        world: WorldState,
        ledger: SideEffectLedger,
        faults: FaultInjector,
    ) -> None:
        self.world = world
        self.ledger = ledger
        self.faults = faults

    def _cache_key(self, endpoint: str, idempotency_key: str) -> str:
        return f"{endpoint}:{idempotency_key}"

    def _cached(
        self, endpoint: str, idempotency_key: str | None, params: dict[str, Any]
    ) -> tuple[bool, dict[str, Any] | None]:
        if not idempotency_key:
            return False, None
        cached = self.world.provider_idempotency.get(self._cache_key(endpoint, idempotency_key))
        if cached is None:
            return False, None
        if cached["fingerprint"] != _fingerprint(params):
            return True, {
                "ok": False,
                "status": "error",
                "error": {
                    "type": "idempotency_error",
                    "message": "idempotency key was reused with different parameters",
                },
            }
        return True, copy.deepcopy(cached["response"])

    def _store(
        self,
        endpoint: str,
        idempotency_key: str | None,
        params: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        if idempotency_key:
            self.world.provider_idempotency[self._cache_key(endpoint, idempotency_key)] = {
                "fingerprint": _fingerprint(params),
                "response": copy.deepcopy(response),
            }

    def _update_order_status(self, order_id: str, status: str) -> None:
        for index, order in enumerate(self.world.orders):
            if order.order_id == order_id:
                self.world.orders[index] = replace(order, status=status)
                return

    def _enqueue_webhook(self, event_type: str, object_id: str, payload: dict[str, Any]) -> None:
        event_number = len(self.world.webhook_events) + 1
        self.world.webhook_events.append(
            WebhookEvent(
                event_id=f"evt_sandbox_{event_number:04d}",
                event_type=event_type,
                object_id=object_id,
                payload=copy.deepcopy(payload),
            )
        )

    def create_payment_intent(
        self,
        order_id: str,
        amount_krw: int,
        currency: str = "krw",
        idempotency_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params = {
            "order_id": order_id,
            "amount_krw": amount_krw,
            "currency": currency,
            "metadata": metadata or {},
        }
        was_cached, cached = self._cached("payment_intent.create", idempotency_key, params)
        if was_cached:
            return cached or {"ok": False, "status": "error"}
        order = next((item for item in self.world.orders if item.order_id == order_id), None)
        if order is None:
            return {"ok": False, "status": "error", "error": {"type": "order_not_found"}}
        if amount_krw <= 0:
            return {"ok": False, "status": "error", "error": {"type": "invalid_amount"}}
        if order.total_krw and order.total_krw != amount_krw:
            return {
                "ok": False,
                "status": "error",
                "error": {"type": "amount_mismatch", "expected": order.total_krw},
            }

        before_hash = self.world.state_hash
        intent_number = len(self.world.payment_intents) + 1
        intent_id = f"pi_sandbox_{intent_number:04d}"
        intent = PaymentIntent(
            intent_id=intent_id,
            order_id=order_id,
            amount_krw=amount_krw,
            currency=currency.lower(),
            status="requires_payment_method",
            metadata=dict(metadata or {}),
        )
        self.world.payment_intents[intent_id] = intent
        self._update_order_status(order_id, "payment_pending")
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="payment_intent.create",
            effect="provider_object_create",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={"payment_intent_id": intent_id, "order_id": order_id},
        )
        response = {
            "ok": True,
            "object": "payment_intent",
            "id": intent_id,
            "status": intent.status,
            "amount_krw": amount_krw,
            "currency": intent.currency,
            "order_id": order_id,
        }
        self._store("payment_intent.create", idempotency_key, params, response)
        return response

    def confirm_payment_intent(
        self,
        payment_intent_id: str,
        payment_method: str = "card_success",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        params = {
            "payment_intent_id": payment_intent_id,
            "payment_method": payment_method,
        }
        was_cached, cached = self._cached("payment_intent.confirm", idempotency_key, params)
        if was_cached:
            return cached or {"ok": False, "status": "error"}
        intent = self.world.payment_intents.get(payment_intent_id)
        if intent is None:
            return {"ok": False, "status": "error", "error": {"type": "not_found"}}
        if intent.status == "succeeded":
            response = self._intent_response(intent)
            self._store("payment_intent.confirm", idempotency_key, params, response)
            return response

        attempt_count = intent.attempt_count + 1
        if payment_method == "card_declined":
            updated = replace(
                intent,
                status="requires_payment_method",
                payment_method=payment_method,
                attempt_count=attempt_count,
            )
            self.world.payment_intents[payment_intent_id] = updated
            response = self._intent_response(updated, error_type="card_declined")
            self._store("payment_intent.confirm", idempotency_key, params, response)
            return response
        if payment_method == "three_d_secure_required":
            updated = replace(
                intent,
                status="requires_action",
                payment_method=payment_method,
                attempt_count=attempt_count,
            )
            self.world.payment_intents[payment_intent_id] = updated
            response = self._intent_response(updated, next_action="use_stripe_sdk")
            self._store("payment_intent.confirm", idempotency_key, params, response)
            return response
        if payment_method == "bank_debit_async":
            updated = replace(
                intent,
                status="processing",
                payment_method=payment_method,
                attempt_count=attempt_count,
            )
            self.world.payment_intents[payment_intent_id] = updated
            self._enqueue_webhook(
                "payment_intent.processing",
                payment_intent_id,
                {"id": payment_intent_id, "status": "processing"},
            )
            response = self._intent_response(updated)
            self._store("payment_intent.confirm", idempotency_key, params, response)
            return response

        before_hash = self.world.state_hash
        try:
            self.world.debit(intent.amount_krw)
        except ValueError:
            response = self._intent_response(intent, error_type="insufficient_balance")
            self._store("payment_intent.confirm", idempotency_key, params, response)
            return response
        charge_number = len(self.world.charges) + 1
        charge_id = f"ch_sandbox_{charge_number:04d}"
        updated = replace(
            intent,
            status="succeeded",
            payment_method=payment_method,
            latest_charge_id=charge_id,
            attempt_count=attempt_count,
        )
        self.world.payment_intents[payment_intent_id] = updated
        self.world.charges[charge_id] = Charge(
            charge_id=charge_id,
            payment_intent_id=payment_intent_id,
            amount_krw=intent.amount_krw,
            currency=intent.currency,
            status="succeeded",
            paid=True,
        )
        self._update_order_status(intent.order_id, "paid")
        # A fixture may ask the provider to expose a stale intermediate event
        # as well as the terminal event.  The out-of-order fault then reorders
        # this deterministic queue without changing the committed world state.
        pre_success_events: Any = ()
        if self.faults.spec is not None and self.faults.spec.type == "out_of_order_webhook":
            pre_success_events = self.faults.spec.params.get(
                "pre_success_event_types", ()
            )
        if isinstance(pre_success_events, (list, tuple)):
            for event_type in pre_success_events:
                self._enqueue_webhook(
                    str(event_type),
                    payment_intent_id,
                    {
                        "id": payment_intent_id,
                        "status": "processing",
                        "order_id": intent.order_id,
                    },
                )
        self._enqueue_webhook(
            "payment_intent.succeeded",
            payment_intent_id,
            {"id": payment_intent_id, "status": "succeeded", "order_id": intent.order_id},
        )
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="payment_intent.confirm",
            effect="charge_commit",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={
                "payment_intent_id": payment_intent_id,
                "charge_id": charge_id,
                "order_id": intent.order_id,
                "amount_krw": intent.amount_krw,
            },
        )
        response = self._intent_response(updated)
        fault_result = self.faults.apply("payment_intent.confirm", response, committed=True)
        if fault_result.error_code:
            response = {
                "ok": False,
                "status": "unknown",
                "error": {
                    "type": "api_connection_error",
                    "code": fault_result.error_code,
                    "message": fault_result.error_message,
                },
            }
        else:
            response = fault_result.result
        # Stripe-like v1 behavior caches the first response, including a 5xx-like
        # response. Retrieval remains the recovery path for an unknown outcome.
        self._store("payment_intent.confirm", idempotency_key, params, response)
        return response

    def retrieve_payment_intent(self, payment_intent_id: str) -> dict[str, Any]:
        intent = self.world.payment_intents.get(payment_intent_id)
        if intent is None:
            return {"ok": False, "status": "error", "error": {"type": "not_found"}}
        return self._intent_response(intent)

    def deliver_webhook(self, event_id: str | None = None) -> dict[str, Any]:
        events: list[WebhookEvent] = []
        if event_id:
            event = next(
                (item for item in self.world.webhook_events if item.event_id == event_id), None
            )
            if event is not None:
                events = [event]
        else:
            pending = [
                item for item in self.world.webhook_events if item.delivery_count == 0
            ]
            if self.faults.spec is not None and self.faults.spec.type == "out_of_order_webhook":
                events = pending
            elif pending:
                events = [pending[0]]
        if not events:
            return {"ok": True, "status": "no_events", "deliveries": []}
        deliveries: list[dict[str, Any]] = []
        for event in events:
            before_hash = self.world.state_hash
            updated_event = replace(event, delivery_count=event.delivery_count + 1)
            self.world.webhook_events = [
                updated_event if item.event_id == event.event_id else item
                for item in self.world.webhook_events
            ]
            deliveries.append(
                {
                    "id": event.event_id,
                    "type": event.event_type,
                    "object_id": event.object_id,
                    "data": copy.deepcopy(event.payload),
                    "signature_valid": event.signature_valid,
                    "delivery_attempt": updated_event.delivery_count,
                }
            )
            after_hash = self.world.state_hash
            self.ledger.append(
                tool="webhook.deliver",
                effect="webhook_delivery",
                status="committed",
                before_state_hash=before_hash,
                after_state_hash=after_hash,
                metadata={"event_id": event.event_id, "event_type": event.event_type},
            )
        result = {"ok": True, "status": "delivered", "deliveries": deliveries}
        return self.faults.apply("webhook.deliver", result).result

    def fulfill_order(
        self,
        order_id: str,
        event_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        existing = (
            self.ledger.find_by_idempotency_key(idempotency_key, tool="order.fulfill")
            if idempotency_key
            else None
        )
        if existing is not None:
            return {
                "ok": True,
                "status": "fulfilled",
                **existing.metadata,
                "idempotent_replay": True,
            }
        if event_id:
            existing_fulfillment = next(
                (item for item in self.world.fulfillments if item.source_event_id == event_id), None
            )
            if existing_fulfillment is not None:
                return {
                    "ok": True,
                    "status": "fulfilled",
                    "fulfillment_id": existing_fulfillment.fulfillment_id,
                    "idempotent_replay": True,
                }
        if not any(order.order_id == order_id for order in self.world.orders):
            return {"ok": False, "status": "error", "error": {"type": "order_not_found"}}
        before_hash = self.world.state_hash
        sequence = len(self.world.fulfillments) + 1
        fulfillment_id = f"fulfillment-{sequence:04d}"
        self.world.fulfillments.append(
            Fulfillment(
                fulfillment_id=fulfillment_id,
                order_id=order_id,
                source_event_id=event_id,
            )
        )
        self._update_order_status(order_id, "fulfilled")
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="order.fulfill",
            effect="fulfillment_commit",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={
                "fulfillment_id": fulfillment_id,
                "order_id": order_id,
                "source_event_id": event_id,
            },
        )
        return {
            "ok": True,
            "status": "fulfilled",
            "fulfillment_id": fulfillment_id,
            "idempotent_replay": False,
        }

    def _intent_response(
        self,
        intent: PaymentIntent,
        *,
        error_type: str | None = None,
        next_action: str | None = None,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {
            "ok": error_type is None,
            "object": "payment_intent",
            "id": intent.intent_id,
            "status": intent.status,
            "amount_krw": intent.amount_krw,
            "currency": intent.currency,
            "order_id": intent.order_id,
            "latest_charge": intent.latest_charge_id,
            "attempt_count": intent.attempt_count,
        }
        if error_type:
            response["error"] = {"type": error_type}
        if next_action:
            response["next_action"] = next_action
        return response


__all__ = ["StripeLikePaymentProvider"]
