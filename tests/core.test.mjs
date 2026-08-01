import assert from "node:assert/strict";
import {
  createCakeCrashFixture,
  createInitialState,
  reduceRunState,
  replayDeterministically,
} from "../src/core.mjs";

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

console.log("NIGHTMARE web core tests passed");
