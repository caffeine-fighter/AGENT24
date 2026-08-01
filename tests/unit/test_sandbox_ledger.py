from __future__ import annotations

from agent24.tools import SideEffectLedger


def test_ledger_entries_are_sequenced_and_metadata_isolated() -> None:
    ledger = SideEffectLedger(run_id="run-1")
    metadata = {"amount_krw": 49_000, "nested": {"source": "fixture"}}
    entry = ledger.append(
        tool="payment.charge",
        effect="payment_and_order_commit",
        status="committed",
        before_state_hash="before",
        after_state_hash="after",
        idempotency_key="cake-1",
        metadata=metadata,
    )
    metadata["amount_krw"] = 0
    metadata["nested"]["source"] = "mutated"

    assert entry.seq == 1
    assert entry.action_id == "run-1:action-0001"
    assert entry.metadata == {"amount_krw": 49_000, "nested": {"source": "fixture"}}
    assert ledger.total_spend() == 49_000
    assert ledger.find_by_idempotency_key("cake-1") == entry


def test_ledger_to_list_returns_copies() -> None:
    ledger = SideEffectLedger()
    ledger.append(
        tool="email.send",
        effect="email_send",
        status="committed",
        before_state_hash="a",
        after_state_hash="b",
        metadata={"message_id": "message-1"},
    )
    exported = ledger.to_list()
    exported[0]["metadata"]["message_id"] = "changed"

    assert ledger.entries[0].metadata["message_id"] == "message-1"
