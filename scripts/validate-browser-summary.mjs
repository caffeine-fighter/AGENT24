import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const CASE_KEYS = {
  normal_hosted: ["id", "passed", "run_count", "submit_count", "seq_contiguous", "terminal_count", "raw_stream_visible", "expected_boundary_visible", "unexpected_evidence_count"],
  typed_unsupported: ["id", "passed", "run_count", "submit_count", "seq_contiguous", "terminal_count", "terminal_digest_stable", "raw_stream_visible", "expected_boundary_visible", "unexpected_evidence_count"],
  explicit_fixture_fallback: ["id", "passed", "run_count", "submit_count", "seq_contiguous", "terminal_count", "raw_stream_visible", "expected_boundary_visible", "unexpected_evidence_count"],
};
const FAILURE_CODES = {
  prerequisites: ["prerequisite_missing", "prerequisite_version_invalid", "dependency_install_failed"],
  normal_hosted: ["normal_route_failed", "sequence_gap", "terminal_count_invalid"],
  typed_unsupported: ["unsupported_boundary_failed", "unexpected_payment_evidence", "terminal_digest_mismatch", "sequence_gap", "terminal_count_invalid"],
  explicit_fixture_fallback: ["fallback_boundary_failed", "sequence_gap", "terminal_count_invalid"],
  artifact_export: ["artifact_policy_failed"],
};

function exactKeys(value, expected, label) {
  const actual = Object.keys(value).sort();
  const wanted = [...expected].sort();
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) throw new Error(`${label} keys are not allowed`);
}

function assertCase(item) {
  if (!item || typeof item !== "object" || Array.isArray(item) || !(item.id in CASE_KEYS)) throw new Error("unknown browser case");
  exactKeys(item, CASE_KEYS[item.id], `case ${item.id}`);
  for (const key of CASE_KEYS[item.id].filter((key) => ["passed", "seq_contiguous", "terminal_digest_stable", "raw_stream_visible", "expected_boundary_visible"].includes(key))) {
    if (typeof item[key] !== "boolean") throw new Error(`case ${item.id} has a non-boolean ${key}`);
  }
  for (const key of ["run_count", "submit_count", "terminal_count", "unexpected_evidence_count"]) {
    if (!Number.isInteger(item[key]) || item[key] < 0) throw new Error(`case ${item.id} has an invalid ${key}`);
  }
}

export async function validateBrowserSummary(summaryPath) {
  if ((await stat(summaryPath)).size > 8 * 1024) throw new Error("summary exceeds 8 KiB");
  const summary = JSON.parse(await readFile(summaryPath, "utf8"));
  exactKeys(summary, ["schema", "browser", "passed", "cases", "failure"], "summary");
  if (summary.schema !== "agent24.browser-smoke.v1" || summary.browser !== "chromium" || typeof summary.passed !== "boolean" || !Array.isArray(summary.cases)) {
    throw new Error("summary header is invalid");
  }
  summary.cases.forEach(assertCase);
  if (new Set(summary.cases.map((item) => item.id)).size !== summary.cases.length) throw new Error("duplicate browser case");

  if (summary.passed) {
    if (summary.failure !== null) throw new Error("passing summary cannot contain a failure");
    if (JSON.stringify(summary.cases.map((item) => item.id)) !== JSON.stringify(Object.keys(CASE_KEYS))) throw new Error("passing summary must contain the three ordered cases");
    for (const item of summary.cases) {
      const expectedRuns = item.id === "typed_unsupported" ? 2 : 1;
      if (!item.passed || item.run_count !== expectedRuns || item.submit_count !== expectedRuns || item.terminal_count !== expectedRuns || !item.seq_contiguous || !item.raw_stream_visible || !item.expected_boundary_visible || item.unexpected_evidence_count !== 0 || (item.id === "typed_unsupported" && !item.terminal_digest_stable)) {
        throw new Error(`passing case ${item.id} does not satisfy its counters`);
      }
    }
  } else {
    if (!summary.failure || typeof summary.failure !== "object" || Array.isArray(summary.failure)) throw new Error("failing summary requires one failure object");
    exactKeys(summary.failure, ["case_id", "code"], "failure");
    if (!(summary.failure.case_id in FAILURE_CODES) || !FAILURE_CODES[summary.failure.case_id].includes(summary.failure.code)) throw new Error("failure pair is outside the closed set");
  }
}

const invokedPath = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : "";
if (import.meta.url === invokedPath) {
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const summaryPath = path.join(repoRoot, "artifacts", "browser-smoke-summary.json");
  try {
    await validateBrowserSummary(summaryPath);
    process.stdout.write("VERIFY browser artifact PASS\n");
  } catch {
    process.stderr.write("VERIFY artifact_export FAIL: browser summary violates the closed schema. Recover: rerun the canonical browser gate.\n");
    process.exitCode = 1;
  }
}
