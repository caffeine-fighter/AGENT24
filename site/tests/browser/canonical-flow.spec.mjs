import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { expect, test } from "@playwright/test";

const TIME_MISSION = "같은 캘린더 검색을 무한 반복하지만 상태가 바뀌지 않는 Agent를 진단해줘.";
const CASE_ORDER = ["normal_hosted", "typed_unsupported", "explicit_fixture_fallback"];
const results = new Map();
let firstFailure = null;

function recordFailure(caseId, code) {
  if (!firstFailure) firstFailure = { case_id: caseId, code };
}

async function guarded(caseId, code, action) {
  try {
    return await action();
  } catch (error) {
    recordFailure(caseId, code);
    throw error;
  }
}

function observeRunRequests(page) {
  const observation = { submitCount: 0 };
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (request.method() === "POST" && url.pathname === "/api/runs") observation.submitCount += 1;
  });
  return observation;
}

function buildObservedCase({
  boundaryVisible,
  digestStable,
  expectedRuns,
  id,
  rawVisible,
  runCount,
  sequenceContiguous,
  submitCount,
  terminalCount,
  unexpectedEvidenceCount,
}) {
  if (runCount !== expectedRuns) throw new Error(`observed run count ${runCount}, expected ${expectedRuns}`);
  if (submitCount !== expectedRuns) throw new Error(`observed submit count ${submitCount}, expected ${expectedRuns}`);
  if (terminalCount !== expectedRuns) throw new Error(`observed terminal count ${terminalCount}, expected ${expectedRuns}`);
  if (!sequenceContiguous) throw new Error("observed a non-contiguous event sequence");
  if (!rawVisible) throw new Error("Raw Stream was not visible");
  if (!boundaryVisible) throw new Error("expected result boundary was not visible");
  if (unexpectedEvidenceCount !== 0) throw new Error(`observed ${unexpectedEvidenceCount} unexpected evidence items`);
  if (id === "typed_unsupported" && !digestStable) throw new Error("unsupported terminal digest changed");

  return {
    id,
    passed: true,
    run_count: runCount,
    submit_count: submitCount,
    seq_contiguous: sequenceContiguous,
    terminal_count: terminalCount,
    ...(id === "typed_unsupported" ? { terminal_digest_stable: digestStable } : {}),
    raw_stream_visible: rawVisible,
    expected_boundary_visible: boundaryVisible,
    unexpected_evidence_count: unexpectedEvidenceCount,
  };
}

async function submit(page, mission) {
  await page.goto("/demo/index.html");
  await page.locator("#missionInput").fill(mission);
  await page.locator("#runButton").click();
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
}

async function streamMetrics(page) {
  const sequence = (await page.locator("#rawStream .stream-seq").allTextContents())
    .map((value) => Number(value.replace("#", "")));
  return {
    rawVisible: await page.locator("#rawStream").isVisible(),
    runCount: await page.locator('#rawStream .stream-line[data-kind="run.started"]').count(),
    sequenceContiguous: sequence.length > 0 && sequence.every((value, index) => value === index + 1),
    terminalCount: await page.locator(
      '#rawStream .stream-line[data-kind="run.completed"], #rawStream .stream-line[data-kind="run.failed"]',
    ).count(),
  };
}

test.afterAll(async () => {
  const output = {
    schema: "agent24.browser-smoke.v1",
    browser: "chromium",
    passed: firstFailure === null && results.size === CASE_ORDER.length,
    cases: CASE_ORDER.filter((id) => results.has(id)).map((id) => results.get(id)),
    failure: firstFailure,
  };
  if (!output.passed && !output.failure) {
    output.failure = { case_id: "artifact_export", code: "artifact_policy_failed" };
  }
  const artifactDirectory = path.resolve(process.cwd(), "..", "artifacts");
  await mkdir(artifactDirectory, { recursive: true });
  await writeFile(
    path.join(artifactDirectory, "browser-smoke-summary.json"),
    `${JSON.stringify(output, null, 2)}\n`,
    "utf8",
  );
});

test("summary builder rejects claims that observations do not support", async () => {
  const observed = {
    boundaryVisible: true,
    expectedRuns: 1,
    id: "normal_hosted",
    rawVisible: true,
    runCount: 1,
    sequenceContiguous: true,
    submitCount: 1,
    terminalCount: 1,
    unexpectedEvidenceCount: 0,
  };
  try {
    expect(() => buildObservedCase({ ...observed, submitCount: 0 })).toThrow(/submit count/);
    expect(() => buildObservedCase({ ...observed, boundaryVisible: false })).toThrow(/boundary/);
    expect(() => buildObservedCase({ ...observed, unexpectedEvidenceCount: 1 })).toThrow(/unexpected evidence/);
  } catch (error) {
    recordFailure("artifact_export", "artifact_policy_failed");
    throw error;
  }
});

test("canonical normal hosted path", async ({ page }) => {
  const id = "normal_hosted";
  const requests = observeRunRequests(page);
  await guarded(id, "normal_route_failed", () => submit(page, "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘."));
  const metrics = await streamMetrics(page);
  await guarded(id, "sequence_gap", () => expect(metrics.sequenceContiguous).toBe(true));
  await guarded(id, "terminal_count_invalid", () => expect(metrics.terminalCount).toBe(1));

  const paymentEvidenceCount = await page.locator("#rawStream .stream-line", { hasText: "payment.charge" }).count();
  const fallbackScopeCount = await page.locator('#scopeNotice[data-scope="fixture_fallback"]').count();
  const unsupportedOutcomeCount = await page.locator('#investigationOutcome[data-status="unsupported"]').count();
  const result = await guarded(id, "normal_route_failed", () => buildObservedCase({
    boundaryVisible: paymentEvidenceCount > 0 && fallbackScopeCount === 0,
    expectedRuns: 1,
    id,
    rawVisible: metrics.rawVisible,
    runCount: metrics.runCount,
    sequenceContiguous: metrics.sequenceContiguous,
    submitCount: requests.submitCount,
    terminalCount: metrics.terminalCount,
    unexpectedEvidenceCount: fallbackScopeCount + unsupportedOutcomeCount,
  }));
  results.set(id, result);
});

test("canonical typed unsupported path is stable across isolated runs", async ({ browser }) => {
  const id = "typed_unsupported";
  const digests = [];
  let boundaryVisible = true;
  let rawVisible = true;
  let runCount = 0;
  let sequenceContiguous = true;
  let submitCount = 0;
  let terminalCount = 0;
  let unexpectedEvidenceCount = 0;

  for (let index = 0; index < 2; index += 1) {
    const context = await browser.newContext();
    try {
      const page = await context.newPage();
      const requests = observeRunRequests(page);
      await guarded(id, "unsupported_boundary_failed", () => submit(page, TIME_MISSION));
      const metrics = await streamMetrics(page);
      rawVisible &&= metrics.rawVisible;
      runCount += metrics.runCount;
      sequenceContiguous &&= metrics.sequenceContiguous;
      submitCount += requests.submitCount;
      terminalCount += metrics.terminalCount;
      await guarded(id, "sequence_gap", () => expect(metrics.sequenceContiguous).toBe(true));
      await guarded(id, "terminal_count_invalid", () => expect(metrics.terminalCount).toBe(1));

      const investigationText = await page.locator("#investigationOutcome").textContent();
      const noticeText = await page.locator("#runNotice").textContent();
      boundaryVisible &&= investigationText === "아직 지원하지 않음"
        && (noticeText ?? "").includes("지금은 이 작업에서 생길 수 있는 문제를 재현할 실험이 없어요");
      unexpectedEvidenceCount += await page.locator("#rawStream .stream-line").filter({
        hasText: /payment\.charge|experiment_plan|protected_replay/,
      }).count();
      const safeProjection = await Promise.all([
        page.locator("#investigationOutcome").textContent(),
        page.locator("#runNotice").textContent(),
        page.locator("#rawStream .stream-type").allTextContents(),
      ]);
      digests.push(createHash("sha256").update(JSON.stringify(safeProjection)).digest("hex"));
    } finally {
      await context.close();
    }
  }

  const digestStable = digests.length === 2 && digests[0] === digests[1];
  await guarded(id, "terminal_digest_mismatch", () => expect(digestStable).toBe(true));
  await guarded(id, "unexpected_payment_evidence", () => expect(unexpectedEvidenceCount).toBe(0));
  const result = await guarded(id, "unsupported_boundary_failed", () => buildObservedCase({
    boundaryVisible,
    digestStable,
    expectedRuns: 2,
    id,
    rawVisible,
    runCount,
    sequenceContiguous,
    submitCount,
    terminalCount,
    unexpectedEvidenceCount,
  }));
  results.set(id, result);
});

test("canonical explicit fixture fallback path", async ({ page }) => {
  const id = "explicit_fixture_fallback";
  const requests = observeRunRequests(page);
  await guarded(id, "fallback_boundary_failed", async () => {
    await page.goto("http://localhost:4173/demo/index.html?api=http://127.0.0.1:1");
    await page.locator("#runButton").click();
    await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
  });
  const metrics = await streamMetrics(page);
  await guarded(id, "sequence_gap", () => expect(metrics.sequenceContiguous).toBe(true));
  await guarded(id, "terminal_count_invalid", () => expect(metrics.terminalCount).toBe(1));

  const noticeText = await page.locator("#runNotice").textContent();
  const modeText = await page.locator("#modeBadge").textContent();
  const fallbackScopeCount = await page.locator('#scopeNotice[data-scope="fixture_fallback"]').count();
  const submittedClaimCount = await page.locator(
    '#submissionOutcome[data-status="accepted"], #investigationOutcome[data-status="verified"]',
  ).count();
  const targetShaText = await page.locator("#targetSha").textContent();
  const pinnedSubmittedShaCount = /^[a-f0-9]{40}$/i.test((targetShaText ?? "").trim()) ? 1 : 0;
  const result = await guarded(id, "fallback_boundary_failed", () => buildObservedCase({
    boundaryVisible: (noticeText ?? "").includes("API에 연결하지 못해 내장 예시를 보여드려요")
      && modeText === "내장 예시 확인 완료"
      && fallbackScopeCount === 1,
    expectedRuns: 1,
    id,
    rawVisible: metrics.rawVisible,
    runCount: metrics.runCount,
    sequenceContiguous: metrics.sequenceContiguous,
    submitCount: requests.submitCount,
    terminalCount: metrics.terminalCount,
    unexpectedEvidenceCount: submittedClaimCount + pinnedSubmittedShaCount,
  }));
  results.set(id, result);
});
