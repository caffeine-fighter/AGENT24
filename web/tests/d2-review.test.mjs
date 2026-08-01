import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  createD2ReviewSurfaces,
  D2_REVIEW_SURFACE_IDS,
} from "../src/d2-review-model.mjs";
import { renderD2Surface } from "../src/d2-review-view.mjs";

const surfaces = createD2ReviewSurfaces();

assert.deepEqual(D2_REVIEW_SURFACE_IDS, ["r1", "r2", "r3", "r4"]);
assert.deepEqual(Object.keys(surfaces), D2_REVIEW_SURFACE_IDS);

assert.deepEqual(surfaces.r1.fields.map((field) => field.name), [
  "repository",
  "ref",
  "mission",
]);
assert.equal(surfaces.r1.primaryAction, "충돌 시험 시작");
assert.match(surfaces.r1.supportBoundary, /public GitHub/);
assert.match(surfaces.r1.validationExample, /실행을 만들지 않았습니다/);

assert.deepEqual(surfaces.r2.steps.map((step) => step.id), [
  "PIN",
  "PROFILE",
  "CRASH",
  "AUTOPSY",
  "VACCINE",
  "REPLAY",
]);
assert.equal(surfaces.r2.steps.find((step) => step.id === "CRASH").status, "active");
assert.equal(surfaces.r2.provenance.resolvedSha.length, 40);
assert.equal(surfaces.r2.experiment.fixture, "life.payment_intent_timeout.v1");
assert.equal(surfaces.r2.experiment.seed, 42);

assert.deepEqual(surfaces.r3.before, {
  logicalOrders: 1,
  charges: 2,
  fulfillments: 2,
  spendKrw: 98000,
  walletKrw: 402000,
});
assert.deepEqual(surfaces.r3.after, {
  logicalOrders: 1,
  charges: 1,
  fulfillments: 1,
  spendKrw: 49000,
  walletKrw: 451000,
});
assert.equal(surfaces.r3.evidenceSections.length, 5);
assert.deepEqual(surfaces.r3.gates.map((gate) => gate.status), ["pass", "pass", "pass", "pass"]);

assert.deepEqual(surfaces.r4.outcomes.map((outcome) => outcome.code), [
  "unsupported",
  "no_failure_observed",
  "budget_exhausted",
  "fallback_demo",
]);
assert.equal(surfaces.r4.outcomes.at(-1).axis, "operational");
assert.equal(surfaces.r4.outcomes.at(-1).source, "fixture");
assert.ok(surfaces.r4.outcomes.at(-1).copy.includes("제출 agent와 무관합니다"));

const entryMarkup = renderD2Surface(surfaces.r1);
assert.match(entryMarkup, /data-review-surface="r1"/);
assert.match(entryMarkup, /Public GitHub repository/);
assert.match(entryMarkup, /role="alert"/);

const runningMarkup = renderD2Surface(surfaces.r2);
assert.match(runningMarkup, new RegExp(surfaces.r2.provenance.resolvedSha));
assert.match(runningMarkup, /role="log"/);
assert.match(runningMarkup, /data-step-status="active"/);

const resultMarkup = renderD2Surface(surfaces.r3);
assert.match(resultMarkup, /₩98,000/);
assert.match(resultMarkup, /₩49,000/);
assert.equal((resultMarkup.match(/class="evidence-key"/g) || []).length, 5);

const nonResultMarkup = renderD2Surface(surfaces.r4);
assert.match(nonResultMarkup, /data-axis="operational"/);
assert.match(nonResultMarkup, /source=fixture/);
assert.match(nonResultMarkup, /제출 agent와 무관합니다/);

const html = await readFile(new URL("../d2-review.html", import.meta.url), "utf8");
assert.match(html, /aria-label="D2 최소 리뷰 표면"/);
assert.match(html, /id="reviewSurface"/);
assert.match(html, /실제 동작 없음/);
assert.doesNotMatch(html, /캘린더|calendar|메일|files|🎂/i);

const css = await readFile(new URL("../d2-review.css", import.meta.url), "utf8");
assert.doesNotMatch(
  css,
  /\.review-surface\s*\{[^}]*animation:/s,
  "review captures must not depend on animation timing",
);
assert.match(css, /prefers-reduced-motion/);

console.log("NIGHTMARE D2 review surface tests passed");
