"""First divergence between a baseline trace and a failing trace.

"Where did this run stop behaving like the healthy one?" is the question a
diagnosis has to answer before it can propose a cause.  This module answers it
structurally, with no LLM and no diffing of prose.

Each event is normalized to ``(kind, tool, args_digest, status)``:

* ``call_id`` is dropped -- it is a positional artifact (``call-003``), so
  keeping it would report a divergence at the first event after any earlier
  length change.
* payloads and ``aut_message`` text are dropped -- a ``web.read`` result whose
  body gained an injected sentence is still the same *step*, and treating page
  prose as a behavioural difference would bury the real branch point under
  content noise.
* ``status`` is kept, because ``ok`` versus ``timeout`` is precisely the branch
  that makes an AUT decide to retry.

What survives is the decision sequence: which tool, with which arguments, and
what it answered.
"""

from collections.abc import Sequence

from .models import Divergence, TraceEvent, args_digest

NormalizedEvent = tuple[str, str | None, str | None, str | None]
"""``(kind, tool, args_digest, status)``."""


def normalize_event(event: TraceEvent) -> NormalizedEvent:
    """Reduce an event to the part that represents behaviour."""

    if event.kind == "tool_call" and event.call is not None:
        return ("tool_call", event.call.tool, args_digest(event.call.args), None)
    if event.kind == "tool_result" and event.result is not None:
        return ("tool_result", None, None, event.result.status)
    return (event.kind, None, None, None)


def first_divergence(
    baseline: Sequence[TraceEvent], failing: Sequence[TraceEvent]
) -> Divergence | None:
    """Return the first behavioural difference, or ``None`` if there is none.

    Walks the paired prefix.  The first mismatch is ``different_result`` when
    both sides are results and ``different_call`` otherwise -- the ``otherwise``
    also covers a call/result desync, where the two traces have stopped being
    comparable at all and the call side is what changed.

    When the prefix matches all the way through, the longer trace is reported at
    ``index == len(shorter)`` as ``extra_events`` (failing is longer) or
    ``missing_events`` (baseline is longer).
    """

    shared = min(len(baseline), len(failing))
    for index in range(shared):
        base_event = baseline[index]
        fail_event = failing[index]
        if normalize_event(base_event) == normalize_event(fail_event):
            continue
        both_results = base_event.kind == "tool_result" and fail_event.kind == "tool_result"
        return Divergence(
            index=index,
            kind="different_result" if both_results else "different_call",
            baseline_event=base_event,
            failing_event=fail_event,
        )

    if len(failing) > shared:
        return Divergence(
            index=shared,
            kind="extra_events",
            baseline_event=None,
            failing_event=failing[shared],
        )
    if len(baseline) > shared:
        return Divergence(
            index=shared,
            kind="missing_events",
            baseline_event=baseline[shared],
            failing_event=None,
        )
    return None


__all__ = ["NormalizedEvent", "first_divergence", "normalize_event"]
