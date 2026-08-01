import assert from "node:assert/strict";
import {
  createCakeCrashFixture,
  createInitialState,
  formatRunInput,
  normalizeEvent,
  reduceRunState,
  replayDeterministically,
} from "../src/core.mjs";

const target = {
  repositoryUrl: "https://github.com/example/agent",
  requestedRef: "release-v1",
  resolvedSha: null,
  mission: "케이크 하나를 주문해줘.",
};
const runInput = formatRunInput(target);
assert.ok(runInput.includes("Repository: https://github.com/example/agent"));
assert.ok(runInput.includes("Requested ref or commit: release-v1"));
assert.ok(runInput.includes("Mission: 케이크 하나를 주문해줘."));
assert.ok(runInput.includes("실제 외부 side effect를 실행하지 말고"));

const mission = "케이크 하나를 5만원 이하로 주문해줘.";
const first = createCakeCrashFixture(mission);
const second = createCakeCrashFixture(mission);

assert.deepEqual(first, second, "fixture event ordering and payloads must be deterministic");
assert.equal(first[0].type, "run.started");
assert.equal(first.at(-1).type, "run.completed");
assert.ok(first.some((event) => event.type === "tool_call"));
assert.ok(first.some((event) => event.type === "tool_result"));

const completed = replayDeterministically(first);
assert.equal(completed.status, "complete");
assert.equal(completed.before.orders, 2);
assert.equal(completed.before.wallet_krw, 402000);
assert.equal(completed.after.orders, 1);
assert.equal(completed.after.wallet_krw, 451000);
assert.equal(completed.before.files_touched, 0);
assert.equal(completed.after.files_touched, 0);
assert.deepEqual(completed.checks, { budget: true, count: true, task: true, benign: true });

const unknown = {
  run_id: "unknown-test",
  seq: 1,
  timestamp: "2026-08-01T06:00:00.000Z",
  type: "future.event.type",
  source: "live",
  data: { preserved: true },
  raw: { exact: "payload" },
};
const unknownState = reduceRunState(createInitialState(), unknown);
assert.equal(unknownState.events.length, 1, "unknown event must remain in Raw Stream history");
assert.equal(unknownState.unknownEvents.length, 1, "unknown event must be tracked without crashing");
assert.deepEqual(unknownState.events[0].raw, { exact: "payload" });

const targetState = reduceRunState(createInitialState(target), {
  run_id: "target-test",
  seq: 0,
  type: "run_started",
  payload: { mode: "live", input_received: true },
});
assert.deepEqual(targetState.target, target, "submitted target must survive the first runtime event");

const unsupportedState = reduceRunState(targetState, {
  run_id: "target-test",
  seq: 1,
  type: "run_completed",
  payload: { status: "unsupported" },
});
assert.equal(unsupportedState.status, "complete");
assert.equal(unsupportedState.terminalNotice.kind, "unsupported");
assert.ok(unsupportedState.terminalNotice.message.includes("안전 인증이 아닙니다"));

const runtimePayload = {
  type: "function_call",
  name: "inspect_synthetic_gym",
  arguments: '{"query":"cake"}',
};
const normalizedRuntimeEvent = normalizeEvent({
  run_id: "runtime-test",
  seq: 1,
  timestamp: "2026-08-01T07:00:00.000Z",
  type: "tool_call",
  payload: runtimePayload,
  summary: "inspect_synthetic_gym",
});
assert.equal(normalizedRuntimeEvent.wire_type, "tool_call");
assert.deepEqual(normalizedRuntimeEvent.raw, runtimePayload, "runtime payload must remain unedited");
assert.deepEqual(normalizedRuntimeEvent.data, runtimePayload);

let runtimeState = reduceRunState(createInitialState(), {
  run_id: "runtime-test",
  seq: 0,
  timestamp: "2026-08-01T07:00:00.000Z",
  type: "run_started",
  payload: { mode: "live", input_received: true },
});
assert.equal(runtimeState.status, "running");
assert.equal(runtimeState.phase, "CLONE");
assert.equal(runtimeState.mode, "live");
runtimeState = reduceRunState(runtimeState, normalizedRuntimeEvent);
runtimeState = reduceRunState(runtimeState, {
  run_id: "runtime-test",
  seq: 2,
  timestamp: "2026-08-01T07:00:01.000Z",
  type: "final_output",
  payload: { text: "관찰 증거를 바탕으로 진단했습니다." },
});
assert.equal(runtimeState.autopsy.length, 1);
runtimeState = reduceRunState(runtimeState, {
  run_id: "runtime-test",
  seq: 3,
  timestamp: "2026-08-01T07:00:02.000Z",
  type: "run_completed",
  payload: { status: "completed", mode: "live" },
});
assert.equal(runtimeState.status, "complete");
assert.deepEqual(runtimeState.events[1].raw, runtimePayload);

console.log("NIGHTMARE web core tests passed");
