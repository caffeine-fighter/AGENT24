import assert from "node:assert/strict";

import {
  createInitialState,
  EVENT_ENVELOPE_SCHEMA,
  normalizeEvent,
  reduceRunState,
} from "../src/core.mjs";

const RUN_ID = "run-protocol-test";

function envelope(seq, type, payload = {}, overrides = {}) {
  return {
    schema_version: EVENT_ENVELOPE_SCHEMA,
    run_id: RUN_ID,
    seq,
    timestamp: new Date(Date.UTC(2026, 7, 2, 0, 0, seq)).toISOString(),
    type,
    origin: "controller",
    payload,
    ...overrides,
  };
}

function begin() {
  return reduceRunState(createInitialState(), envelope(0, "run_started", {
    mode: "live",
    target: {
      repositoryUrl: "https://github.com/example/agent",
      requestedRef: "main",
      mission: "케이크 하나를 한 번만 주문해줘.",
    },
  }, { origin: "system", phase: "PIN" }));
}

const rawApiCall = normalizeEvent(envelope(0, "tool_call", {
  type: "function_call",
  name: "github.resolve_ref",
  arguments: { requested_ref: "main" },
}, { origin: "openai_sdk" }));
assert.equal(rawApiCall.raw_api_item, true);
assert.equal(rawApiCall.origin, "openai_sdk");
assert.equal(normalizeEvent(envelope(0, "tool_call", {}, { origin: "controller" })).raw_api_item, false);

const first = envelope(0, "run_started", { mode: "live" }, { origin: "system", phase: "PIN" });
const once = reduceRunState(createInitialState(), first);
const exactDuplicate = reduceRunState(once, structuredClone(first));
assert.strictEqual(exactDuplicate, once, "an exact reconnect duplicate must be ignored");
assert.equal(exactDuplicate.events.length, 1);

const conflictingDuplicate = reduceRunState(once, {
  ...first,
  payload: { mode: "fixture" },
});
assert.equal(conflictingDuplicate.status, "protocol_error");
assert.equal(conflictingDuplicate.protocol.issue.code, "conflicting_duplicate");
assert.equal(conflictingDuplicate.events.length, 2, "both conflicting wire records must remain visible");

let gapState = begin();
const late = envelope(2, "phase.changed", { phase: "CRASH" }, { phase: "CRASH" });
gapState = reduceRunState(gapState, late);
assert.equal(gapState.protocol.status, "gap");
assert.equal(gapState.protocol.issue.expected, 1);
assert.equal(gapState.phase, "PIN", "out-of-order evidence must not change the projected result");
gapState = reduceRunState(gapState, envelope(1, "phase.changed", { phase: "PROFILE" }, { phase: "PROFILE" }));
assert.equal(gapState.protocol.status, "ready");
assert.equal(gapState.protocol.lastContiguousSeq, 2);
assert.equal(gapState.protocol.buffered.length, 0);
assert.equal(gapState.phase, "CRASH");
assert.equal(gapState.events.find((event) => event.seq === 2)?.projection_blocked, false);
assert.strictEqual(reduceRunState(gapState, structuredClone(late)), gapState);

const unknownEventState = reduceRunState(begin(), envelope(1, "future.capability.detected", { preserved: true }));
assert.equal(unknownEventState.status, "running");
assert.equal(unknownEventState.events.length, 2);
assert.equal(unknownEventState.unknownEvents.length, 1);
assert.deepEqual(unknownEventState.events.at(-1).raw, { preserved: true });

const unknownMajorState = reduceRunState(createInitialState(), {
  ...envelope(0, "run_started", { mode: "live" }, { origin: "system" }),
  schema_version: "event.envelope.v2",
});
assert.equal(unknownMajorState.status, "protocol_error");
assert.equal(unknownMajorState.protocol.issue.code, "unsupported_envelope");
assert.equal(unknownMajorState.phase, null, "an unsupported major version must stay out of the projection");
assert.equal(unknownMajorState.events[0].projection_blocked, true);

const issueState = reduceRunState(begin(), envelope(1, "operation.issue", {
  code: "explanation_timeout",
  safe_detail: "설명을 불러오지 못했지만 확인한 기록은 남겼어요.",
  fallback_started: false,
}));
assert.equal(issueState.status, "running", "an operation issue is not a terminal event");
assert.equal(issueState.outcomes.operation.status, "degraded");

let offlineState = begin();
offlineState = reduceRunState(offlineState, envelope(1, "source_descriptor", {
  repository: "example/agent",
  repository_url: "https://github.com/example/agent",
  requested_ref: "main",
  resolved_sha: "0123456789abcdef0123456789abcdef01234567",
  resolver: "github-api",
}));
assert.equal(offlineState.outcomes.submission.status, "pinned");
offlineState = reduceRunState(offlineState, envelope(2, "explanation_offline", {}));
assert.equal(offlineState.outcomes.submission.status, "pinned");
assert.equal(offlineState.outcomes.operation.status, "degraded");
assert.notEqual(offlineState.analysisScope, "fixture_fallback");

let terminalState = begin();
terminalState = reduceRunState(terminalState, envelope(1, "run_completed", {
  submission: { status: "accepted" },
  investigation: { status: "verified_mitigation" },
  operation: { status: "completed" },
  result_source: "submitted",
}, { origin: "system", phase: "REPLAY" }));
assert.equal(terminalState.status, "complete");
assert.equal(terminalState.protocol.terminalCount, 1);
assert.equal(terminalState.terminalAxes.result_source, "submitted");
const secondTerminal = reduceRunState(terminalState, envelope(2, "run_completed", {
  submission: { status: "accepted" },
  investigation: { status: "verified_mitigation" },
  operation: { status: "completed" },
  result_source: "submitted",
}, { origin: "system", phase: "REPLAY" }));
assert.equal(secondTerminal.status, "protocol_error");
assert.equal(secondTerminal.protocol.issue.code, "terminal_count_invalid");
assert.equal(secondTerminal.protocol.terminalCount, 1);

console.log("NIGHTMARE event protocol tests passed");
