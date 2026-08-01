"""Unit tests for first-divergence computation.

The point of these tests is what gets *ignored*: call ids, payload prose and
message text are noise, and a divergence walker that reacts to them reports the
first cosmetic difference instead of the first behavioural branch.
"""

from agent24.agent.divergence import first_divergence, normalize_event
from agent24.agent.models import ToolCall, ToolResult, TraceEvent, TrustLabel


def call_event(index: int, tool: str, args: dict, *, call_id: str | None = None) -> TraceEvent:
    return TraceEvent(
        index=index,
        kind="tool_call",
        call=ToolCall(call_id=call_id or f"call-{index:03d}", tool=tool, args=args),
    )


def result_event(
    index: int, status: str, *, payload=None, call_id: str | None = None
) -> TraceEvent:
    return TraceEvent(
        index=index,
        kind="tool_result",
        result=ToolResult(
            call_id=call_id or f"call-{index:03d}", status=status, payload=payload
        ),
    )


def charge_trace(status: str, *, amount: int = 49000) -> list[TraceEvent]:
    return [
        call_event(0, "web.read", {"url": "u"}),
        result_event(1, "ok", payload={"content": "가격은 49000원입니다."}),
        call_event(2, "payment.charge", {"amount_krw": amount}),
        result_event(3, status),
    ]


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def test_identical_traces_return_none():
    assert first_divergence(charge_trace("ok"), charge_trace("ok")) is None


def test_call_id_is_ignored():
    baseline = [call_event(0, "web.read", {"url": "u"}, call_id="call-000")]
    failing = [call_event(0, "web.read", {"url": "u"}, call_id="call-777")]
    assert first_divergence(baseline, failing) is None


def test_payload_prose_is_ignored():
    """An injected sentence in a page body is the same *step*.  Treating it as a
    divergence would bury the real branch point under content noise."""

    baseline = [result_event(0, "ok", payload={"content": "가격은 49000원입니다."})]
    failing = [
        result_event(0, "ok", payload={"content": "가격은 49000원입니다. [[INJECT: ...]]"})
    ]
    assert first_divergence(baseline, failing) is None


def test_argument_order_is_ignored_but_argument_values_are_not():
    same = first_divergence(
        [call_event(0, "payment.charge", {"a": 1, "b": 2})],
        [call_event(0, "payment.charge", {"b": 2, "a": 1})],
    )
    assert same is None

    changed = first_divergence(
        [call_event(0, "payment.charge", {"amount_krw": 49000})],
        [call_event(0, "payment.charge", {"amount_krw": 12000})],
    )
    assert changed is not None
    assert changed.kind == "different_call"


def test_provenance_is_part_of_the_call_arguments_digest_only_via_args():
    """Provenance lives outside ``args``, so it is not a divergence on its own --
    the taint oracle is what reads it."""

    baseline = [call_event(0, "email.send", {"to": "a"})]
    tainted = TraceEvent(
        index=0,
        kind="tool_call",
        call=ToolCall(
            call_id="call-000",
            tool="email.send",
            args={"to": "a"},
            provenance=frozenset({TrustLabel.WEB_PAGE}),
        ),
    )
    assert first_divergence(baseline, [tainted]) is None


def test_aut_message_text_is_ignored():
    baseline = [TraceEvent(index=0, kind="aut_message", message="계획 A")]
    failing = [TraceEvent(index=0, kind="aut_message", message="계획 B")]
    assert first_divergence(baseline, failing) is None


def test_normalize_event_shape():
    assert normalize_event(result_event(1, "timeout")) == ("tool_result", None, None, "timeout")
    kind, tool, digest, status = normalize_event(call_event(0, "web.read", {"url": "u"}))
    assert (kind, tool, status) == ("tool_call", "web.read", None)
    assert isinstance(digest, str) and len(digest) == 64


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_a_different_status_is_a_different_result():
    divergence = first_divergence(charge_trace("ok"), charge_trace("timeout"))
    assert divergence is not None
    assert divergence.index == 3
    assert divergence.kind == "different_result"
    assert divergence.baseline_event.result.status == "ok"
    assert divergence.failing_event.result.status == "timeout"


def test_a_different_tool_is_a_different_call():
    baseline = charge_trace("ok") + [call_event(4, "calendar.create", {"title": "t"})]
    failing = charge_trace("ok") + [call_event(4, "payment.charge", {"amount_krw": 49000})]
    divergence = first_divergence(baseline, failing)
    assert divergence is not None
    assert divergence.index == 4
    assert divergence.kind == "different_call"


def test_a_call_result_desync_is_classified_as_a_different_call():
    baseline = [call_event(0, "web.read", {"url": "u"})]
    failing = [result_event(0, "ok")]
    divergence = first_divergence(baseline, failing)
    assert divergence is not None
    assert divergence.kind == "different_call"


def test_the_first_mismatch_wins():
    baseline = charge_trace("ok")
    failing = [call_event(0, "file.read", {"path": "p"})] + charge_trace("timeout")[1:]
    divergence = first_divergence(baseline, failing)
    assert divergence is not None
    assert divergence.index == 0


# --------------------------------------------------------------------------
# Prefix exhaustion
# --------------------------------------------------------------------------


def test_a_longer_failing_trace_yields_extra_events():
    baseline = charge_trace("ok")
    failing = charge_trace("ok") + [call_event(4, "payment.charge", {"amount_krw": 49000})]
    divergence = first_divergence(baseline, failing)
    assert divergence is not None
    assert divergence.index == 4
    assert divergence.kind == "extra_events"
    assert divergence.baseline_event is None
    assert divergence.failing_event.call.tool == "payment.charge"


def test_a_longer_baseline_trace_yields_missing_events():
    baseline = charge_trace("ok") + [call_event(4, "calendar.create", {"title": "t"})]
    failing = charge_trace("ok")
    divergence = first_divergence(baseline, failing)
    assert divergence is not None
    assert divergence.index == 4
    assert divergence.kind == "missing_events"
    assert divergence.baseline_event.call.tool == "calendar.create"
    assert divergence.failing_event is None


def test_two_empty_traces_do_not_diverge():
    assert first_divergence([], []) is None


def test_an_empty_baseline_reports_extra_events_at_zero():
    divergence = first_divergence([], charge_trace("ok"))
    assert divergence is not None
    assert divergence.index == 0
    assert divergence.kind == "extra_events"
