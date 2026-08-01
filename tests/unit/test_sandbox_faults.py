from __future__ import annotations

from agent24.tools import FaultInjector, FaultSpec


def test_commit_then_timeout_applies_after_commit_once() -> None:
    injector = FaultInjector(
        FaultSpec(
            fault_id="fault-1",
            type="commit_then_timeout",
            target="payment.charge",
            apply_on_call=1,
        )
    )

    first = injector.apply("payment.charge", {"status": "committed"}, committed=True)
    second = injector.apply("payment.charge", {"status": "committed"}, committed=True)

    assert first.applied is True
    assert first.error_code == "TIMEOUT"
    assert second.applied is False
    assert second.result == {"status": "committed"}
    assert [item.call_index for item in injector.applications] == [1]


def test_web_injection_is_tainted_and_empty_result_is_bounded() -> None:
    injection = FaultInjector(
        FaultSpec(
            fault_id="fault-web",
            type="malicious_web_content",
            target="web.fetch",
        )
    )
    injected = injection.apply("web.fetch", {"content": "cake", "taint": "untrusted"})
    assert injected.applied is True
    assert injected.result["injection_present"] is True
    assert "Ignore previous instructions" in injected.result["content"]

    empty = FaultInjector(
        FaultSpec(
            fault_id="fault-empty",
            type="empty_result",
            target="catalog.search",
        )
    )
    result = empty.apply("catalog.search", {"items": [{"product_id": "cake-49k"}]})
    assert result.applied is True
    assert result.result == {"items": []}
