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

async function submit(page, mission) {
  await page.goto("/demo/index.html");
  await page.locator("#missionInput").fill(mission);
  await page.locator("#runButton").click();
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
}

async function streamMetrics(page) {
  const sequence = (await page.locator("#rawStream .stream-seq").allTextContents())
    .map((value) => Number(value.replace("#", "")));
  const terminalCount = await page.locator(
    '#rawStream .stream-line[data-kind="run.completed"], #rawStream .stream-line[data-kind="run.failed"]',
  ).count();
  return {
    rawVisible: await page.locator("#rawStream").isVisible(),
    sequenceContiguous: sequence.length > 0 && sequence.every((value, index) => value === index + 1),
    terminalCount,
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
    output.failure = { case_id: "prerequisites", code: "dependency_install_failed" };
  }
  const artifactDirectory = path.resolve(process.cwd(), "..", "artifacts");
  await mkdir(artifactDirectory, { recursive: true });
  await writeFile(
    path.join(artifactDirectory, "browser-smoke-summary.json"),
    `${JSON.stringify(output, null, 2)}\n`,
    "utf8",
  );
});

test("canonical normal hosted path", async ({ page }) => {
  const id = "normal_hosted";
  await guarded(id, "normal_route_failed", () => submit(page, "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘."));
  const metrics = await streamMetrics(page);
  await guarded(id, "sequence_gap", () => expect(metrics.sequenceContiguous).toBe(true));
  await guarded(id, "terminal_count_invalid", () => expect(metrics.terminalCount).toBe(1));
  await guarded(id, "normal_route_failed", async () => {
    await expect(page.locator("#rawStream")).toContainText("payment.charge");
    await expect(page.locator("#scopeNotice")).not.toHaveAttribute("data-scope", "fixture_fallback");
    expect(metrics.rawVisible).toBe(true);
  });
  results.set(id, {
    id,
    passed: true,
    run_count: 1,
    submit_count: 1,
    seq_contiguous: metrics.sequenceContiguous,
    terminal_count: metrics.terminalCount,
    raw_stream_visible: metrics.rawVisible,
    expected_boundary_visible: true,
    unexpected_evidence_count: 0,
  });
});

test("canonical typed unsupported path is stable across isolated runs", async ({ browser }) => {
  const id = "typed_unsupported";
  const digests = [];
  let terminalCount = 0;
  let sequenceContiguous = true;
  let rawVisible = true;
  try {
    for (let index = 0; index < 2; index += 1) {
      const context = await browser.newContext();
      try {
        const page = await context.newPage();
        await guarded(id, "unsupported_boundary_failed", () => submit(page, TIME_MISSION));
        const metrics = await streamMetrics(page);
        terminalCount += metrics.terminalCount;
        sequenceContiguous &&= metrics.sequenceContiguous;
        rawVisible &&= metrics.rawVisible;
        await guarded(id, "sequence_gap", () => expect(metrics.sequenceContiguous).toBe(true));
        await guarded(id, "terminal_count_invalid", () => expect(metrics.terminalCount).toBe(1));
        await guarded(id, "unsupported_boundary_failed", async () => {
          await expect(page.locator("#investigationOutcome")).toHaveText("아직 지원하지 않음");
          await expect(page.locator("#runNotice")).toContainText("지금은 이 작업에서 생길 수 있는 문제를 재현할 실험이 없어요");
        });
        await guarded(id, "unexpected_payment_evidence", async () => {
          await expect(page.locator("#rawStream")).not.toContainText("payment.charge");
          await expect(page.locator("#rawStream")).not.toContainText("experiment_plan");
          await expect(page.locator("#rawStream")).not.toContainText("protected_replay");
        });
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
  } catch (error) {
    throw error;
  }
  const digestStable = digests.length === 2 && digests[0] === digests[1];
  await guarded(id, "terminal_digest_mismatch", () => expect(digestStable).toBe(true));
  results.set(id, {
    id,
    passed: true,
    run_count: 2,
    submit_count: 2,
    seq_contiguous: sequenceContiguous,
    terminal_count: terminalCount,
    terminal_digest_stable: digestStable,
    raw_stream_visible: rawVisible,
    expected_boundary_visible: true,
    unexpected_evidence_count: 0,
  });
});

test("canonical explicit fixture fallback path", async ({ page }) => {
  const id = "explicit_fixture_fallback";
  await guarded(id, "fallback_boundary_failed", async () => {
    await page.goto("http://localhost:4173/demo/index.html?api=http://127.0.0.1:1");
    await page.locator("#runButton").click();
    await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
  });
  const metrics = await streamMetrics(page);
  await guarded(id, "sequence_gap", () => expect(metrics.sequenceContiguous).toBe(true));
  await guarded(id, "terminal_count_invalid", () => expect(metrics.terminalCount).toBe(1));
  await guarded(id, "fallback_boundary_failed", async () => {
    await expect(page.locator("#runNotice")).toContainText("API에 연결하지 못해 내장 예시를 보여드려요");
    await expect(page.locator("#modeBadge")).toHaveText("내장 예시 확인 완료");
    await expect(page.locator("#scopeNotice")).toHaveAttribute("data-scope", "fixture_fallback");
    expect(metrics.rawVisible).toBe(true);
  });
  results.set(id, {
    id,
    passed: true,
    run_count: 1,
    submit_count: 1,
    seq_contiguous: metrics.sequenceContiguous,
    terminal_count: metrics.terminalCount,
    raw_stream_visible: metrics.rawVisible,
    expected_boundary_visible: true,
    unexpected_evidence_count: 0,
  });
});
