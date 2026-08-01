"""Deterministic in-memory world state for the Nightmare Lab sandbox.

The world deliberately contains only synthetic data.  It is mutable inside a
single sandbox run, while :class:`WorldSnapshot` is a canonical, hashable
representation that can be used to reset and replay a fixture.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class Product:
    product_id: str
    title: str
    price_krw: int
    available: bool = True


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    product_id: str
    quantity: int
    total_krw: int
    status: str = "confirmed"
    payment_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    intent_id: str
    order_id: str
    amount_krw: int
    currency: str
    status: str
    payment_method: str | None = None
    latest_charge_id: str | None = None
    attempt_count: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Charge:
    charge_id: str
    payment_intent_id: str
    amount_krw: int
    currency: str
    status: str
    paid: bool


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    event_id: str
    event_type: str
    object_id: str
    payload: dict[str, Any]
    signature_valid: bool = True
    delivery_count: int = 0


@dataclass(frozen=True, slots=True)
class Fulfillment:
    fulfillment_id: str
    order_id: str
    source_event_id: str | None = None
    status: str = "fulfilled"


@dataclass(frozen=True, slots=True)
class InboxMessage:
    message_id: str
    direction: str
    sender: str
    recipient: str
    subject: str
    body: str
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    event_id: str
    title: str
    start_at: str
    end_at: str | None = None
    timezone: str = "Asia/Seoul"


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    content: str
    version: int = 1


@dataclass(frozen=True, slots=True)
class WebPage:
    url: str
    title: str
    content: str
    taint: str = "untrusted"


@dataclass(frozen=True, slots=True)
class WorldSnapshot:
    """Immutable serialized world state used as a replay boundary."""

    fixture_id: str
    seed: int
    state_json: str
    state_hash: str

    @property
    def state(self) -> dict[str, Any]:
        """Return a fresh copy so callers cannot mutate the snapshot."""

        return json.loads(self.state_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "state": self.state,
            "state_hash": self.state_hash,
        }


@dataclass
class WorldState:
    """Mutable synthetic state owned by one sandbox run."""

    fixture_id: str
    seed: int
    wallet_balance_krw: int = 500_000
    catalog: dict[str, Product] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    payment_intents: dict[str, PaymentIntent] = field(default_factory=dict)
    charges: dict[str, Charge] = field(default_factory=dict)
    webhook_events: list[WebhookEvent] = field(default_factory=list)
    fulfillments: list[Fulfillment] = field(default_factory=list)
    provider_idempotency: dict[str, dict[str, Any]] = field(default_factory=dict)
    inbox: list[InboxMessage] = field(default_factory=list)
    calendar: list[CalendarEvent] = field(default_factory=list)
    files: dict[str, FileRecord] = field(default_factory=dict)
    web: dict[str, WebPage] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a canonical-shaped, JSON-compatible state dictionary."""

        return {
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "wallet": {"balance_krw": self.wallet_balance_krw},
            "catalog": {
                product_id: asdict(product)
                for product_id, product in sorted(self.catalog.items())
            },
            "orders": [asdict(order) for order in self.orders],
            "payment_intents": {
                intent_id: asdict(intent)
                for intent_id, intent in sorted(self.payment_intents.items())
            },
            "charges": {
                charge_id: asdict(charge) for charge_id, charge in sorted(self.charges.items())
            },
            "webhook_events": [asdict(event) for event in self.webhook_events],
            "fulfillments": [asdict(fulfillment) for fulfillment in self.fulfillments],
            "provider_idempotency": copy.deepcopy(self.provider_idempotency),
            "inbox": [asdict(message) for message in self.inbox],
            "calendar": [asdict(event) for event in self.calendar],
            "files": {
                path: asdict(record) for path, record in sorted(self.files.items())
            },
            "web": {url: asdict(page) for url, page in sorted(self.web.items())},
        }

    def snapshot(self) -> WorldSnapshot:
        state_json = _canonical_json(self.to_dict())
        state_hash = hashlib.sha256(state_json.encode("utf-8")).hexdigest()
        return WorldSnapshot(
            fixture_id=self.fixture_id,
            seed=self.seed,
            state_json=state_json,
            state_hash=state_hash,
        )

    @property
    def state_hash(self) -> str:
        return self.snapshot().state_hash

    def clone(self) -> WorldState:
        return copy.deepcopy(self)

    def debit(self, amount_krw: int) -> None:
        if amount_krw < 0:
            raise ValueError("amount_krw must be non-negative")
        if amount_krw > self.wallet_balance_krw:
            raise ValueError("insufficient synthetic wallet balance")
        self.wallet_balance_krw -= amount_krw

    def credit(self, amount_krw: int) -> None:
        if amount_krw < 0:
            raise ValueError("amount_krw must be non-negative")
        self.wallet_balance_krw += amount_krw

    @classmethod
    def from_snapshot(cls, snapshot: WorldSnapshot) -> WorldState:
        expected_hash = hashlib.sha256(snapshot.state_json.encode("utf-8")).hexdigest()
        if expected_hash != snapshot.state_hash:
            raise ValueError("world snapshot hash does not match serialized state")
        state = snapshot.state
        return cls(
            fixture_id=str(state["fixture_id"]),
            seed=int(state["seed"]),
            wallet_balance_krw=int(state["wallet"]["balance_krw"]),
            catalog={
                product_id: Product(**product)
                for product_id, product in state.get("catalog", {}).items()
            },
            orders=[Order(**order) for order in state.get("orders", [])],
            payment_intents={
                intent_id: PaymentIntent(**intent)
                for intent_id, intent in state.get("payment_intents", {}).items()
            },
            charges={
                charge_id: Charge(**charge)
                for charge_id, charge in state.get("charges", {}).items()
            },
            webhook_events=[WebhookEvent(**event) for event in state.get("webhook_events", [])],
            fulfillments=[
                Fulfillment(**fulfillment) for fulfillment in state.get("fulfillments", [])
            ],
            provider_idempotency=copy.deepcopy(state.get("provider_idempotency", {})),
            inbox=[
                InboxMessage(
                    **{
                        **message,
                        "attachments": tuple(message.get("attachments", ())),
                    }
                )
                for message in state.get("inbox", [])
            ],
            calendar=[CalendarEvent(**event) for event in state.get("calendar", [])],
            files={
                path: FileRecord(**record)
                for path, record in state.get("files", {}).items()
            },
            web={url: WebPage(**page) for url, page in state.get("web", {}).items()},
        )

    def restore(self, snapshot: WorldSnapshot) -> None:
        restored = type(self).from_snapshot(snapshot)
        self.fixture_id = restored.fixture_id
        self.seed = restored.seed
        self.wallet_balance_krw = restored.wallet_balance_krw
        self.catalog = restored.catalog
        self.orders = restored.orders
        self.payment_intents = restored.payment_intents
        self.charges = restored.charges
        self.webhook_events = restored.webhook_events
        self.fulfillments = restored.fulfillments
        self.provider_idempotency = restored.provider_idempotency
        self.inbox = restored.inbox
        self.calendar = restored.calendar
        self.files = restored.files
        self.web = restored.web


__all__ = [
    "CalendarEvent",
    "Charge",
    "FileRecord",
    "Fulfillment",
    "InboxMessage",
    "Order",
    "PaymentIntent",
    "Product",
    "WebPage",
    "WebhookEvent",
    "WorldSnapshot",
    "WorldState",
]
