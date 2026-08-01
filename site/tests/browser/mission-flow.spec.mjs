import { expect, test } from "@playwright/test";

const TIME_MISSION = "같은 캘린더 검색을 무한 반복하지만 상태가 바뀌지 않는 Agent를 진단해줘.";

async function submit(page, mission, api = null) {
  const query = api ? `?api=${encodeURIComponent(api)}` : "";
  await page.goto(`/demo/index.html${query}`);
  await page.locator("#missionInput").fill(mission);
  await page.locator("#runButton").click();
  await expect(page.locator("#connectionStatus")).toContainText(/완료|진행하지 못함/, { timeout: 15_000 });
}

test("unresolved hosted source stops before a synthetic payment experiment", async ({ page }) => {
  await submit(page, "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.");

  await expect(page.locator("#rawStream .stream-type", { hasText: "run.completed" })).toHaveCount(1);
  await expect(page.locator("#diagnosisSeverity")).toHaveText("NOT RUN · 분석 안 함");
  await expect(page.locator("#diagnosisScope")).toContainText("immutable source");
  await expect(page.locator("#runNotice")).toContainText("GitHub ref를 immutable commit으로 고정하지 못해");
  await expect(page.locator("#rawStream")).not.toContainText("payment.charge");
});

test("unsupported Surprise mission stops once without payment substitution", async ({ page }) => {
  await submit(page, TIME_MISSION, "http://127.0.0.1:1");

  await expect(page.locator("#diagnosisSeverity")).toHaveText("NOT RUN · 지원하지 않음");
  await expect(page.locator("#diagnosisScope")).toContainText("내장 예시");
  await expect(page.locator("#runNotice")).toContainText("이 작업에 맞는 실험은 아직 준비되지 않았어요");
  await expect(page.locator("#rawStream .stream-type", { hasText: "run.completed" })).toHaveCount(1);
  await expect(page.locator("#rawStream")).not.toContainText("payment.charge");
  await expect(page.locator("#rawStream")).not.toContainText("experiment_plan");
  await expect(page.locator("#rawStream")).not.toContainText("protected_replay");
});

test("API failure is labeled as a built-in example and still ends once", async ({ page }) => {
  await page.goto("http://localhost:4173/demo/index.html?api=http://127.0.0.1:1");
  await page.locator("#runButton").click();

  await expect(page.locator("#runNotice")).toContainText(
    "API에 연결하지 못해 내장 예시를 보여드려요",
    { timeout: 5_000 },
  );
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
  await expect(page.locator("#rawStream .stream-type", { hasText: "run.completed" })).toHaveCount(1);
  await expect(page.locator("#modeBadge")).toHaveText("내장 예시 확인 완료");
});
