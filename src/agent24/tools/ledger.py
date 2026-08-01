"""Append-only synthetic side-effect ledger."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One immutable side-effect record.

    ``metadata_json`` is stored as serialized JSON rather than a mutable dict so
    a caller cannot change evidence after it has been appended.
    """

    seq: int
    run_id: str
    action_id: str
    tool: str
    effect: str
    status: str
    before_state_hash: str
    after_state_hash: str
    idempotency_key: str | None = None
    metadata_json: str = "{}"

    @property
    def metadata(self) -> dict[str, Any]:
        return json.loads(self.metadata_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "action_id": self.action_id,
            "tool": self.tool,
            "effect": self.effect,
            "status": self.status,
            "before_state_hash": self.before_state_hash,
            "after_state_hash": self.after_state_hash,
            "idempotency_key": self.idempotency_key,
            "metadata": self.metadata,
        }


class SideEffectLedger:
    """In-memory append-only ledger for a single sandbox run."""

    def __init__(self, *, run_id: str = "run-001") -> None:
        self.run_id = run_id
        self._entries: list[LedgerEntry] = []

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def next_seq(self) -> int:
        return len(self._entries) + 1

    def append(
        self,
        *,
        tool: str,
        effect: str,
        status: str,
        before_state_hash: str,
        after_state_hash: str,
        idempotency_key: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        action_id: str | None = None,
    ) -> LedgerEntry:
        seq = self.next_seq
        entry = LedgerEntry(
            seq=seq,
            run_id=self.run_id,
            action_id=action_id or f"{self.run_id}:action-{seq:04d}",
            tool=tool,
            effect=effect,
            status=status,
            before_state_hash=before_state_hash,
            after_state_hash=after_state_hash,
            idempotency_key=idempotency_key,
            metadata_json=_json(dict(metadata or {})),
        )
        self._entries.append(entry)
        return entry

    def find_by_idempotency_key(
        self, idempotency_key: str, *, tool: str | None = None
    ) -> LedgerEntry | None:
        for entry in reversed(self._entries):
            if entry.idempotency_key != idempotency_key:
                continue
            if tool is not None and entry.tool != tool:
                continue
            if entry.status == "committed":
                return entry
        return None

    def find_by_action_id(self, action_id: str) -> LedgerEntry | None:
        return next((entry for entry in self._entries if entry.action_id == action_id), None)

    def total_spend(self, *, tool: str | None = None) -> int:
        """Return committed synthetic spend across legacy and provider paths.

        ``payment.charge`` is retained for the first fixture, while the
        PaymentIntent adapter records the irreversible debit as
        ``payment_intent.confirm`` with ``effect=charge_commit``.  A caller can
        still pass ``tool`` to restrict the measurement to one API boundary.
        """

        def matches(entry: LedgerEntry) -> bool:
            if tool is None:
                return entry.tool == "payment.charge" or (
                    entry.tool == "payment_intent.confirm" and entry.effect == "charge_commit"
                )
            return entry.tool == tool
        return sum(
            int(entry.metadata.get("amount_krw", 0))
            for entry in self._entries
            if matches(entry) and entry.status == "committed"
        )

    def to_list(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(entry.to_dict()) for entry in self._entries]

    def reset(self, *, run_id: str | None = None) -> None:
        """Start a new run; existing entries are not edited or reused."""

        self._entries.clear()
        if run_id is not None:
            self.run_id = run_id


__all__ = ["LedgerEntry", "SideEffectLedger"]
