import { expect, test } from "@playwright/test";

const TIME_MISSION = "같은 캘린더 검색을 무한 반복하지만 상태가 바뀌지 않는 Agent를 진단해줘.";

async function submit(page, mission) {
  await page.goto("/demo/index.html");
  await page.locator("#missionInput").fill(mission);
  await page.locator("#runButton").click();
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
}

test("normal mission uses the hosted form, SSE stream, and one terminal event", async ({ page }) => {
  await submit(page, "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.");

  await expect(page.locator("#rawStream .stream-type", { hasText: "run.completed" })).toHaveCount(1);
  await expect(page.locator("#rawStream")).toContainText("payment.charge");
  await expect(page.locator("#runNotice")).not.toContainText("이 작업에 맞는 안전 실험을 지원하지 않아요");
});

test("unsupported Surprise mission stops once without payment substitution", async ({ page }) => {
  await submit(page, TIME_MISSION);

  await expect(page.locator("#investigationOutcome")).toHaveText("현재 지원 안 함");
  await expect(page.locator("#runNotice")).toContainText("지금은 이 작업에 맞는 안전 실험을 지원하지 않아요");
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
  await expect(page.locator("#modeBadge")).toHaveText("내장 예시 완료");
});

test("mobile intake keeps natural Korean copy inside the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/demo/index.html");

  await expect(page.getByRole("heading", { level: 1 })).toContainText("먼저 실패시켜 보세요");
  await expect(page.getByLabel("GitHub 저장소")).toBeVisible();
  await expect(page.getByLabel("확인할 버전")).toBeVisible();
  await expect(page.getByLabel("에이전트에게 맡길 일")).toBeVisible();
  await expect(page.getByRole("button", { name: "안전 실험 시작" })).toBeVisible();

  const layout = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    text: document.body.innerText,
  }));
  expect(layout.scrollWidth).toBe(layout.clientWidth);
  expect(layout.text).not.toMatch(/[가-힣](?:합니다|됩니다|있습니다|없습니다|않습니다)/);
});
