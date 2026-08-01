import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const readRepoFile = (path) =>
  readFile(new URL(`../../${path}`, import.meta.url), "utf8");

const [acceptance, concept, externalContract, eventContract] = await Promise.all([
  readRepoFile("docs/integration-acceptance.md"),
  readRepoFile("docs/nightmare-lab-concept.md"),
  readRepoFile("docs/external-agent-contract.md"),
  readRepoFile("web/event-contract.md"),
]);

assert.match(acceptance, /Status: D4 I1–I4 ratified by Rekhet/);
for (const surface of [
  "I1 · Accepted slice",
  "I2 · Successful completion",
  "I3 · Honest boundaries",
  "I4 · Independent proof",
]) {
  assert.match(acceptance, new RegExp(surface.replace("·", "\\·")));
}

assert.match(acceptance, /same-origin production web application/);
assert.match(acceptance, /external_target\.v1/);
assert.match(acceptance, /prebuilt hosted sequence or browser fixture cannot prove/);

const phases = ["`PIN`", "`PROFILE`", "`CRASH`", "`AUTOPSY`", "`VACCINE`", "`REPLAY`"];
let previousPhaseIndex = -1;
for (const phase of phases) {
  const phaseIndex = acceptance.indexOf(phase, previousPhaseIndex + 1);
  assert.ok(phaseIndex > previousPhaseIndex, `${phase} must appear in order`);
  previousPhaseIndex = phaseIndex;
}

for (const terminalLine of [
  "submission.status = accepted",
  "investigation.status = verified_mitigation",
  "operation.status = completed",
  "result_source = submitted",
]) {
  assert.match(acceptance, new RegExp(terminalLine.replaceAll(".", "\\.")));
}

assert.match(acceptance, /typed D3 `422` error and creates no run/);
assert.match(acceptance, /result_source=fixture/);
assert.match(acceptance, /Legacy string adapter is compatibility evidence only/i);
assert.match(acceptance, /Expected full SHA recorded before resolver execution/);
assert.match(acceptance, /value-identical after canonical JSON normalization/);
assert.match(acceptance, /D5a has ratified the non-leaky/);
assert.match(acceptance, /D4 is ratified as the acceptance target\s+but not satisfied/);

assert.match(concept, /D4 integration acceptance ratified/);
assert.match(concept, /integration-acceptance\.md/);
assert.match(externalContract, /exact D4 integrated-slice pass meaning is ratified/);
assert.match(eventContract, /necessary but not sufficient for the ratified D4/);

console.log("NIGHTMARE D4 integration acceptance contract tests passed");
