import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const readRepoFile = (path) =>
  readFile(new URL(`../../${path}`, import.meta.url), "utf8");

const [planText, manifestText, concept, integration] = await Promise.all([
  readRepoFile("docs/test-plan.md"),
  readRepoFile("tests/evals/d5a-plan.json"),
  readRepoFile("docs/nightmare-lab-concept.md"),
  readRepoFile("docs/integration-acceptance.md"),
]);
const manifest = JSON.parse(manifestText);

assert.equal(manifest.schema_version, "nightmare.d5a.plan.v1");
assert.equal(manifest.status, "ratified");
assert.equal(manifest.scope.p0_package, "payment.commit_then_timeout");
assert.equal(manifest.scope.fixture_id, "life.payment_intent_timeout.v1");
assert.equal(manifest.scope.seed, 42);

assert.deepEqual(
  manifest.coverage_families.map(({ id }) => id),
  [
    "C1_start_boundary",
    "C2_source_provenance",
    "C3_controller_success",
    "C4_honest_outcomes",
    "C5_event_transport",
    "C6_browser_journey",
    "C7_safety_privacy",
  ],
);

const allCaseIds = manifest.coverage_families.flatMap(({ case_ids }) => case_ids);
assert.equal(new Set(allCaseIds).size, allCaseIds.length, "case IDs must be unique");
assert.equal(allCaseIds.length, 44);
for (const family of manifest.coverage_families) {
  assert.ok(family.owners.length > 0, `${family.id} needs an owner`);
  assert.ok(
    family.implementation_surfaces.length > 0,
    `${family.id} needs an implementation surface`,
  );
  assert.ok(family.contracts.length > 0, `${family.id} needs contract traceability`);
  assert.ok(family.layers.length > 0, `${family.id} needs an execution layer`);
  assert.ok(family.required_assertions.length > 0, `${family.id} needs assertions`);
}

assert.deepEqual(manifest.heldout.quotas, {
  independently_pinned_public_target: 1,
  boundary_source_manifest_shapes: 6,
  honest_terminal_degradation_shapes: 5,
  event_protocol_mutations: 5,
  browser_surface_viewport_pairs: 8,
});
assert.equal(manifest.heldout.one_use, true);
assert.equal(manifest.heldout.burn_after_execution, true);
assert.equal(manifest.heldout.cleartext_committed_before_run, false);
const materializationSteps = manifest.heldout.materialize_after;
for (const [earlier, later] of [
  ["A4 migration complete", "A5 visible-case initial measurements complete"],
  ["A5 visible-case initial measurements complete", "D5b repetitions thresholds and completion ratified"],
  ["D5b repetitions thresholds and completion ratified", "scorers and family manifest committed"],
  ["scorers and family manifest committed", "implementation and runner commits frozen"],
]) {
  const earlierIndex = materializationSteps.indexOf(earlier);
  const laterIndex = materializationSteps.indexOf(later);
  assert.notEqual(earlierIndex, -1, `missing materialization prerequisite: ${earlier}`);
  assert.notEqual(laterIndex, -1, `missing materialization prerequisite: ${later}`);
  assert.ok(earlierIndex < laterIndex, `${earlier} must precede ${later}`);
}

assert.deepEqual(
  manifest.environments.map(({ id }) => id),
  ["E1_deterministic_ci", "E2_github_source_pin", "E3_sdk_raw_identity", "E4_browser"],
);
assert.deepEqual(manifest.environments.at(-1).viewports, ["320x800", "1440x1000"]);
assert.equal(manifest.evidence.ci_retention_days, 7);
for (const forbidden of ["raw run logs", "generated media", "credentials", "personal data"]) {
  assert.ok(manifest.evidence.never_commit.includes(forbidden));
}
assert.ok(manifest.d5b_deferred.includes("repetition count"));
assert.ok(manifest.d5b_deferred.includes("final completion rule"));

assert.match(planText, /Status: D5a T1–T4 ratified by Rekhet/);
assert.match(planText, /Existing visible regression baseline/);
assert.match(planText, /Cleartext cases are never committed before execution/);
assert.match(planText, /Tautology/);
assert.match(planText, /Verifier = designer/);
assert.match(planText, /Wrong null hypothesis/);
assert.match(planText, /D5a deliberately does not decide/);
assert.match(concept, /D5a non-leaky test design ratified/);
assert.match(integration, /test-plan\.md/);

console.log("NIGHTMARE D5a test-plan contract tests passed");
