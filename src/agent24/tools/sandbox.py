"""Stateful synthetic sandbox tools used by the Nightmare Lab AUT."""

from __future__ import annotations

import json
from typing import Any

from agents import function_tool

from .faults import FaultInjector, FaultSpec
from .ledger import SideEffectLedger
from .payments import StripeLikePaymentProvider
from .world import (
    CalendarEvent,
    FileRecord,
    InboxMessage,
    Order,
    WorldSnapshot,
    WorldState,
)

TOOL_MANIFEST: tuple[dict[str, Any], ...] = (
    {"name": "catalog.search", "side_effect": False},
    # Kept for compatibility with the first Life-v0 fixture.  New scenarios
    # should use the PaymentIntent-style lifecycle below.
    {"name": "payment.charge", "side_effect": True},
    {"name": "payment.status", "side_effect": False},
    {"name": "payment_intent.create", "side_effect": True},
    {"name": "payment_intent.confirm", "side_effect": True},
    {"name": "payment_intent.retrieve", "side_effect": False},
    {"name": "order.create", "side_effect": True},
    {"name": "webhook.deliver", "side_effect": True, "untrusted": True},
    {"name": "order.fulfill", "side_effect": True},
    {"name": "web.fetch", "side_effect": False, "untrusted": True},
    {"name": "web.search", "side_effect": False, "untrusted": True},
    {"name": "email.send", "side_effect": True},
    {"name": "calendar.create", "side_effect": True},
    {"name": "file.write", "side_effect": True},
)


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SandboxGym:
    """A deterministic, local-only world plus tool proxy.

    ``SandboxGym`` is deliberately independent from the Lab Agent.  The AUT
    sees only the tool methods/wrappers, while the controller can inspect the
    world snapshot, ledger, and applied fault records for diagnosis.
    """

    def __init__(
        self,
        *,
        world: WorldState,
        fault: FaultSpec | None = None,
        run_id: str = "run-001",
    ) -> None:
        self.world = world
        self.faults = FaultInjector(fault)
        self.ledger = SideEffectLedger(run_id=run_id)
        self.payment_provider = StripeLikePaymentProvider(
            world=self.world,
            ledger=self.ledger,
            faults=self.faults,
        )
        self._initial_snapshot = world.snapshot()

    @classmethod
    def from_fixture(
        cls,
        fixture_id: str = "life.cake_collision.v1",
        *,
        seed: int | None = None,
        run_id: str = "run-001",
        fault_enabled: bool = True,
    ) -> SandboxGym:
        """Construct a sandbox without requiring callers to know fixture wiring."""

        from .fixtures import load_fixture

        return load_fixture(
            fixture_id,
            seed=seed,
            run_id=run_id,
            fault_enabled=fault_enabled,
        )

    @property
    def fixture_id(self) -> str:
        return self.world.fixture_id

    @property
    def seed(self) -> int:
        return self.world.seed

    @property
    def fault_spec(self) -> FaultSpec | None:
        return self.faults.spec

    @property
    def initial_snapshot(self) -> WorldSnapshot:
        return self._initial_snapshot

    def snapshot(self) -> WorldSnapshot:
        return self.world.snapshot()

    def reset(self, *, run_id: str | None = None) -> None:
        """Restore the fixture boundary and start a fresh ledger run."""

        self.world.restore(self._initial_snapshot)
        self.faults.reset()
        self.ledger.reset(run_id=run_id)

    def manifest(self) -> list[dict[str, Any]]:
        return [dict(item) for item in TOOL_MANIFEST]

    def evidence(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "seed": self.seed,
            "initial_snapshot_hash": self.initial_snapshot.state_hash,
            "final_snapshot_hash": self.snapshot().state_hash,
            "ledger": self.ledger.to_list(),
            "fault_applications": [item.to_dict() for item in self.faults.applications],
        }

    def _product(self, product_id: str):
        product = self.world.catalog.get(product_id)
        if product is None:
            raise ValueError(f"unknown product_id: {product_id}")
        if not product.available:
            raise ValueError(f"product is unavailable: {product_id}")
        return product

    def catalog_search(self, query: str = "", max_price_krw: int | None = None) -> dict[str, Any]:
        normalized = query.strip().casefold()
        products = [
            product
            for product in self.world.catalog.values()
            if product.available
            and (not normalized or normalized in product.title.casefold())
            and (max_price_krw is None or product.price_krw <= max_price_krw)
        ]
        result = {
            "ok": True,
            "query": query,
            "items": [
                {
                    "product_id": product.product_id,
                    "title": product.title,
                    "price_krw": product.price_krw,
                    "available": product.available,
                }
                for product in sorted(products, key=lambda item: (item.price_krw, item.product_id))
            ],
        }
        return self.faults.apply("catalog.search", result).result

    def payment_charge(
        self,
        product_id: str,
        quantity: int = 1,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if quantity < 1:
            return {"ok": False, "status": "rejected", "error": "quantity must be >= 1"}
        try:
            product = self._product(product_id)
        except ValueError as error:
            return {"ok": False, "status": "rejected", "error": str(error)}

        existing = (
            self.ledger.find_by_idempotency_key(idempotency_key, tool="payment.charge")
            if idempotency_key
            else None
        )
        if existing is not None:
            metadata = existing.metadata
            replay_result = {
                "ok": True,
                "status": "committed",
                "payment_id": metadata["payment_id"],
                "order_id": metadata["order_id"],
                "charged_krw": int(metadata["amount_krw"]),
                "idempotent_replay": True,
            }
            return self.faults.apply("payment.charge", replay_result).result

        amount_krw = product.price_krw * quantity
        try:
            before_hash = self.world.state_hash
            self.world.debit(amount_krw)
        except ValueError as error:
            return {"ok": False, "status": "rejected", "error": str(error)}

        sequence = self.ledger.next_seq
        payment_id = f"payment-{sequence:04d}"
        order_id = f"order-{sequence:04d}"
        self.world.orders.append(
            Order(
                order_id=order_id,
                product_id=product_id,
                quantity=quantity,
                total_krw=amount_krw,
                payment_id=payment_id,
                idempotency_key=idempotency_key,
            )
        )
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="payment.charge",
            effect="payment_and_order_commit",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={
                "payment_id": payment_id,
                "order_id": order_id,
                "product_id": product_id,
                "quantity": quantity,
                "amount_krw": amount_krw,
            },
        )
        result = {
            "ok": True,
            "status": "committed",
            "payment_id": payment_id,
            "order_id": order_id,
            "charged_krw": amount_krw,
            "idempotent_replay": False,
        }
        fault_result = self.faults.apply("payment.charge", result, committed=True)
        if fault_result.error_code:
            # A real uncertain outcome should not leak the committed IDs to the
            # AUT.  The hidden ledger remains available to the Lab Agent.
            return {
                "ok": False,
                "status": "unknown",
                "error": {
                    "code": fault_result.error_code,
                    "message": fault_result.error_message,
                },
            }
        return fault_result.result

    def payment_status(
        self, *, payment_id: str | None = None, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        for entry in reversed(self.ledger.entries):
            metadata = entry.metadata
            if payment_id and metadata.get("payment_id") != payment_id:
                continue
            if idempotency_key and entry.idempotency_key != idempotency_key:
                continue
            if payment_id or idempotency_key:
                return {
                    "found": True,
                    "status": entry.status,
                    "payment_id": metadata.get("payment_id"),
                    "order_id": metadata.get("order_id"),
                    "charged_krw": metadata.get("amount_krw", 0),
                }
        return {"found": False, "status": "absent"}

    def payment_intent_create(
        self,
        order_id: str,
        amount_krw: int,
        currency: str = "krw",
        idempotency_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.payment_provider.create_payment_intent(
            order_id,
            amount_krw,
            currency,
            idempotency_key,
            metadata,
        )

    def payment_intent_confirm(
        self,
        payment_intent_id: str,
        payment_method: str = "card_success",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.payment_provider.confirm_payment_intent(
            payment_intent_id,
            payment_method,
            idempotency_key,
        )

    def payment_intent_retrieve(self, payment_intent_id: str) -> dict[str, Any]:
        return self.payment_provider.retrieve_payment_intent(payment_intent_id)

    def webhook_deliver(self, event_id: str | None = None) -> dict[str, Any]:
        return self.payment_provider.deliver_webhook(event_id)

    def order_fulfill(
        self,
        order_id: str,
        event_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self.payment_provider.fulfill_order(order_id, event_id, idempotency_key)

    def order_create(
        self,
        product_id: str,
        quantity: int = 1,
        idempotency_key: str | None = None,
        total_krw: int | None = None,
    ) -> dict[str, Any]:
        if quantity < 1:
            return {"ok": False, "status": "rejected", "error": "quantity must be >= 1"}
        try:
            self._product(product_id)
        except ValueError as error:
            return {"ok": False, "status": "rejected", "error": str(error)}
        existing = (
            self.ledger.find_by_idempotency_key(idempotency_key, tool="order.create")
            if idempotency_key
            else None
        )
        if existing is not None:
            return {
                "ok": True,
                "status": "committed",
                **existing.metadata,
                "idempotent_replay": True,
            }
        order_total = self.world.catalog[product_id].price_krw * quantity
        if total_krw is not None:
            if total_krw <= 0:
                return {
                    "ok": False,
                    "status": "rejected",
                    "error": "total_krw must be > 0",
                }
            order_total = total_krw
        before_hash = self.world.state_hash
        sequence = self.ledger.next_seq
        order_id = f"order-{sequence:04d}"
        self.world.orders.append(
            Order(
                order_id=order_id,
                product_id=product_id,
                quantity=quantity,
                total_krw=order_total,
                status="created",
                idempotency_key=idempotency_key,
            )
        )
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="order.create",
            effect="order_commit",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={
                "order_id": order_id,
                "product_id": product_id,
                "quantity": quantity,
                "total_krw": order_total,
            },
        )
        return {
            "ok": True,
            "status": "committed",
            "order_id": order_id,
            "product_id": product_id,
            "quantity": quantity,
            "total_krw": order_total,
            "idempotent_replay": False,
        }

    def web_fetch(self, url: str) -> dict[str, Any]:
        page = self.world.web.get(url)
        if page is None:
            return {"ok": False, "status": "not_found", "url": url}
        result = {
            "ok": True,
            "url": page.url,
            "title": page.title,
            "content": page.content,
            "taint": page.taint,
        }
        return self.faults.apply("web.fetch", result).result

    def web_search(self, query: str = "") -> dict[str, Any]:
        normalized = query.strip().casefold()
        pages = [
            page
            for page in self.world.web.values()
            if not normalized
            or normalized in page.title.casefold()
            or normalized in page.content.casefold()
        ]
        result = {
            "ok": True,
            "query": query,
            "items": [
                {"url": page.url, "title": page.title, "taint": page.taint}
                for page in sorted(pages, key=lambda item: item.url)
            ],
        }
        return self.faults.apply("web.search", result).result

    def email_send(
        self,
        recipient: str,
        subject: str,
        body: str,
        attachments: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        existing = (
            self.ledger.find_by_idempotency_key(idempotency_key, tool="email.send")
            if idempotency_key
            else None
        )
        if existing is not None:
            return {"ok": True, "status": "sent", **existing.metadata, "idempotent_replay": True}
        before_hash = self.world.state_hash
        sequence = self.ledger.next_seq
        message_id = f"message-{sequence:04d}"
        self.world.inbox.append(
            InboxMessage(
                message_id=message_id,
                direction="outbound",
                sender="cake-buyer",
                recipient=recipient,
                subject=subject,
                body=body,
                attachments=tuple(attachments or ()),
            )
        )
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="email.send",
            effect="email_send",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={"message_id": message_id, "recipient": recipient},
        )
        return {
            "ok": True,
            "status": "sent",
            "message_id": message_id,
            "idempotent_replay": False,
        }

    def calendar_create(
        self,
        title: str,
        start_at: str,
        end_at: str | None = None,
        timezone: str = "Asia/Seoul",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        existing = (
            self.ledger.find_by_idempotency_key(idempotency_key, tool="calendar.create")
            if idempotency_key
            else None
        )
        if existing is not None:
            return {"ok": True, "status": "created", **existing.metadata, "idempotent_replay": True}
        before_hash = self.world.state_hash
        sequence = self.ledger.next_seq
        event_id = f"event-{sequence:04d}"
        self.world.calendar.append(
            CalendarEvent(
                event_id=event_id,
                title=title,
                start_at=start_at,
                end_at=end_at,
                timezone=timezone,
            )
        )
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="calendar.create",
            effect="calendar_create",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={"event_id": event_id, "title": title, "start_at": start_at},
        )
        return {"ok": True, "status": "created", "event_id": event_id, "idempotent_replay": False}

    def file_write(
        self, path: str, content: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        existing = (
            self.ledger.find_by_idempotency_key(idempotency_key, tool="file.write")
            if idempotency_key
            else None
        )
        if existing is not None:
            return {"ok": True, "status": "written", **existing.metadata, "idempotent_replay": True}
        before_hash = self.world.state_hash
        previous = self.world.files.get(path)
        version = (previous.version + 1) if previous else 1
        self.world.files[path] = FileRecord(path=path, content=content, version=version)
        after_hash = self.world.state_hash
        self.ledger.append(
            tool="file.write",
            effect="file_write",
            status="committed",
            before_state_hash=before_hash,
            after_state_hash=after_hash,
            idempotency_key=idempotency_key,
            metadata={"path": path, "version": version},
        )
        return {
            "ok": True,
            "status": "written",
            "path": path,
            "version": version,
            "idempotent_replay": False,
        }

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        dispatch = {
            "catalog.search": self.catalog_search,
            "payment.charge": self.payment_charge,
            "payment.status": self.payment_status,
            "payment_intent.create": self.payment_intent_create,
            "payment_intent.confirm": self.payment_intent_confirm,
            "payment_intent.retrieve": self.payment_intent_retrieve,
            "order.create": self.order_create,
            "webhook.deliver": self.webhook_deliver,
            "order.fulfill": self.order_fulfill,
            "web.fetch": self.web_fetch,
            "web.search": self.web_search,
            "email.send": self.email_send,
            "calendar.create": self.calendar_create,
            "file.write": self.file_write,
        }
        try:
            handler = dispatch[tool]
        except KeyError as error:
            raise ValueError(f"unsupported sandbox tool: {tool}") from error
        return handler(**arguments)

    def tools(self) -> list[Any]:
        """Expose sandbox calls as Agents SDK function tools.

        The public function names use underscores because the SDK function-name
        grammar is stricter than the conceptual dotted tool names in the
        manifest.  The returned payload retains the same field contract.
        """

        sandbox = self

        @function_tool(name_override="catalog_search")
        async def catalog_search(query: str = "", max_price_krw: int | None = None) -> str:
            return _json_result(sandbox.catalog_search(query, max_price_krw))

        @function_tool(name_override="payment_charge")
        async def payment_charge(
            product_id: str, quantity: int = 1, idempotency_key: str | None = None
        ) -> str:
            return _json_result(sandbox.payment_charge(product_id, quantity, idempotency_key))

        @function_tool(name_override="payment_status")
        async def payment_status(
            payment_id: str | None = None, idempotency_key: str | None = None
        ) -> str:
            return _json_result(
                sandbox.payment_status(payment_id=payment_id, idempotency_key=idempotency_key)
            )

        # Provider metadata is an open string map, which cannot be represented
        # by the Agents SDK's strict object schema without inventing a fixed
        # field list.  Keep the real API shape on this adapter and opt out of
        # strict schema generation for this one tool.
        @function_tool(name_override="payment_intent_create", strict_mode=False)
        async def payment_intent_create(
            order_id: str,
            amount_krw: int,
            currency: str = "krw",
            idempotency_key: str | None = None,
            metadata: dict[str, str] | None = None,
        ) -> str:
            return _json_result(
                sandbox.payment_intent_create(
                    order_id, amount_krw, currency, idempotency_key, metadata
                )
            )

        @function_tool(name_override="payment_intent_confirm")
        async def payment_intent_confirm(
            payment_intent_id: str,
            payment_method: str = "card_success",
            idempotency_key: str | None = None,
        ) -> str:
            return _json_result(
                sandbox.payment_intent_confirm(
                    payment_intent_id, payment_method, idempotency_key
                )
            )

        @function_tool(name_override="payment_intent_retrieve")
        async def payment_intent_retrieve(payment_intent_id: str) -> str:
            return _json_result(sandbox.payment_intent_retrieve(payment_intent_id))

        @function_tool(name_override="order_create")
        async def order_create(
            product_id: str,
            quantity: int = 1,
            idempotency_key: str | None = None,
            total_krw: int | None = None,
        ) -> str:
            return _json_result(
                sandbox.order_create(product_id, quantity, idempotency_key, total_krw)
            )

        @function_tool(name_override="webhook_deliver")
        async def webhook_deliver(event_id: str | None = None) -> str:
            return _json_result(sandbox.webhook_deliver(event_id))

        @function_tool(name_override="order_fulfill")
        async def order_fulfill(
            order_id: str,
            event_id: str | None = None,
            idempotency_key: str | None = None,
        ) -> str:
            return _json_result(sandbox.order_fulfill(order_id, event_id, idempotency_key))

        @function_tool(name_override="web_fetch")
        async def web_fetch(url: str) -> str:
            return _json_result(sandbox.web_fetch(url))

        @function_tool(name_override="web_search")
        async def web_search(query: str = "") -> str:
            return _json_result(sandbox.web_search(query))

        @function_tool(name_override="email_send")
        async def email_send(
            recipient: str,
            subject: str,
            body: str,
            attachments: list[str] | None = None,
            idempotency_key: str | None = None,
        ) -> str:
            return _json_result(
                sandbox.email_send(recipient, subject, body, attachments, idempotency_key)
            )

        @function_tool(name_override="calendar_create")
        async def calendar_create(
            title: str,
            start_at: str,
            end_at: str | None = None,
            timezone: str = "Asia/Seoul",
            idempotency_key: str | None = None,
        ) -> str:
            return _json_result(
                sandbox.calendar_create(title, start_at, end_at, timezone, idempotency_key)
            )

        @function_tool(name_override="file_write")
        async def file_write(
            path: str, content: str, idempotency_key: str | None = None
        ) -> str:
            return _json_result(sandbox.file_write(path, content, idempotency_key))

        return [
            catalog_search,
            payment_charge,
            payment_status,
            payment_intent_create,
            payment_intent_confirm,
            payment_intent_retrieve,
            order_create,
            webhook_deliver,
            order_fulfill,
            web_fetch,
            web_search,
            email_send,
            calendar_create,
            file_write,
        ]


__all__ = ["SandboxGym", "TOOL_MANIFEST"]
