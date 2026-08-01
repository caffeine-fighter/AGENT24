import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const fixture = JSON.parse(
  await readFile(new URL("../fixtures/event-contract.v1.json", import.meta.url), "utf8"),
);

assert.equal(fixture.contract_version, "d3.v1");
assert.deepEqual(fixture.origins, ["openai_sdk", "controller", "fixture", "system"]);
assert.deepEqual(fixture.stage_sequence, [
  "PIN",
  "PROFILE",
  "CRASH",
  "AUTOPSY",
  "VACCINE",
  "REPLAY",
]);

assert.equal(fixture.canonical_request.schema_version, "external_target.v1");
assert.ok(!Object.hasOwn(fixture.canonical_request, "input"));
assert.ok(fixture.canonical_request.target.requested_ref);
assert.equal(
  fixture.legacy_mixed_request.input.trim(),
  fixture.legacy_mixed_request.target.mission.trim(),
);

assert.equal(fixture.accepted_response.schema_version, "run.accepted.v1");
assert.equal(fixture.accepted_response.status, "queued");
assert.deepEqual(fixture.accepted_response.deprecations, []);
assert.deepEqual(
  fixture.boundary_errors.map(({ error }) => error.code),
  ["invalid_submission", "conflicting_input"],
);

const events = fixture.happy_path_events;
assert.deepEqual(events.map(({ seq }) => seq), [0, 1, 2, 3]);
for (const event of events) {
  assert.equal(event.schema_version, "event.envelope.v1");
  assert.equal(event.run_id, fixture.accepted_response.run_id);
  assert.ok(fixture.origins.includes(event.origin));
  assert.ok(Object.hasOwn(event, "payload"));
}

const rawToolResult = events.find(({ type }) => type === "tool_result");
assert.equal(rawToolResult.origin, "openai_sdk");
assert.equal(rawToolResult.payload.type, "function_call_output");

const terminals = events.filter(({ type }) => type === "run_completed");
assert.equal(terminals.length, 1);
assert.equal(events.at(-1), terminals[0]);
assert.equal(terminals[0].payload.result_source, "submitted");
assert.ok(!events.some(({ type }) => ["run_failed", "offline_demo"].includes(type)));

const fallback = fixture.fallback_terminal;
assert.equal(fallback.type, "run_completed");
assert.equal(fallback.origin, "system");
assert.equal(fallback.payload.investigation.status, "not_run");
assert.equal(fallback.payload.operation.status, "fallback_demo");
assert.equal(fallback.payload.result_source, "fixture");

const contract = await readFile(new URL("../event-contract.md", import.meta.url), "utf8");
assert.match(contract, /Status: D3\.1–D3\.8 ratified by Rekhet/);
assert.match(contract, /Last-Event-ID/);
assert.match(contract, /exactly one `run_completed`/i);
assert.match(contract, /explanation_offline/);

console.log("NIGHTMARE D3 event contract tests passed");
