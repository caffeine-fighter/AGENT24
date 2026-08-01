import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const TIME_MISSION = "같은 캘린더 검색을 무한 반복하지만 상태가 바뀌지 않는 Agent를 진단해줘.";

async function expectNoHorizontalOverflow(page) {
  const layout = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(layout.scrollWidth).toBe(layout.clientWidth);
}

async function expectNoSeriousA11yViolations(page) {
  const { violations } = await new AxeBuilder({ page }).analyze();
  expect(
    violations
      .filter(({ impact }) => impact === "serious" || impact === "critical")
      .map(({ id, impact, nodes }) => ({ id, impact, targets: nodes.map(({ target }) => target) })),
  ).toEqual([]);
}

async function submit(page, mission) {
  await page.goto("/demo/index.html");
  await page.locator("#missionInput").fill(mission);
  await page.locator("#runButton").click();
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
}

test("idle surface shows one truthful action before any result evidence", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/demo/index.html");

  await expect(page.getByRole("button", { name: "충돌 시험 시작" })).toBeVisible();
  await expect(page.locator("#resultSurface")).toBeHidden();
  await expect(page.locator("#resetButton")).toBeHidden();
  await expect(page.locator("#replayButton")).toBeHidden();

  const firstSurface = await page.locator("body").innerText();
  expect(firstSurface).not.toMatch(/payment\.charge|가상 지갑|처리된 케이크|적용한 안전장치/);

  const primaryBox = await page.locator("#runButton").boundingBox();
  expect(primaryBox).not.toBeNull();
  expect(primaryBox.width).toBeGreaterThanOrEqual(44);
  expect(primaryBox.height).toBeGreaterThanOrEqual(44);
  await expectNoHorizontalOverflow(page);
  await expectNoSeriousA11yViolations(page);
});

test("input error preserves values and connects the corrective message", async ({ page }) => {
  let runRequests = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/runs")) runRequests += 1;
  });
  await page.goto("/demo/index.html");
  const repository = page.locator("#repositoryInput");
  await repository.fill("https://gitlab.com/example/agent");
  await page.locator("#runButton").click();

  await expect(page.locator("#inputError")).toBeVisible();
  await expect(repository).toHaveAttribute("aria-invalid", "true");
  await expect(repository).toHaveAttribute("aria-describedby", /inputError/);
  await expect(repository).toBeFocused();
  await expect(repository).toHaveValue("https://gitlab.com/example/agent");
  await expect(page.locator("#resultSurface")).toBeHidden();
  expect(runRequests).toBe(0);
  await expectNoSeriousA11yViolations(page);
});

test("normal mission uses the hosted form, SSE stream, and one terminal event", async ({ page }) => {
  let runRequests = 0;
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/runs") runRequests += 1;
  });
  await submit(page, "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.");

  await expect(page.locator('#rawStream .stream-line[data-kind="run.completed"]')).toHaveCount(1);
  await expect(page.locator("#rawStream")).toContainText("payment.charge");
  await expect(page.locator("#runNotice")).not.toContainText("이 작업에 맞는 안전 실험을 지원하지 않아요");
  await expect(page.locator("#resultSurface")).toBeVisible();
  await expect(page.locator("#worldGrid")).toBeVisible();
  await expect(page.locator("#resetButton")).toBeVisible();
  await expect(page.locator("#replayButton")).toBeVisible();
  expect(runRequests).toBe(1);
  const firstRunId = await page.locator("#runId").textContent();

  await page.locator("#replayButton").click();
  await expect.poll(() => runRequests).toBe(2);
  await expect(page.locator("#runId")).not.toHaveText(firstRunId ?? "");
  await expect(page.locator("#runButton")).toBeDisabled();
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
  await expect(page.locator('#rawStream .stream-line[data-kind="run.completed"]')).toHaveCount(1);
  await expect(page.locator("#runId")).not.toContainText("fixture-life-payment-intent-timeout-v1");
  await expectNoSeriousA11yViolations(page);
});

test("unsupported Surprise mission stops once without payment substitution", async ({ page }) => {
  await submit(page, TIME_MISSION);

  await expect(page.locator("#investigationOutcome")).toHaveText("아직 지원하지 않음");
  await expect(page.locator("#runNotice")).toContainText("지금은 이 작업에 맞는 안전 실험을 지원하지 않아요");
  await expect(page.locator('#rawStream .stream-line[data-kind="run.completed"]')).toHaveCount(1);
  await expect(page.locator("#rawStream")).not.toContainText("payment.charge");
  await expect(page.locator("#rawStream")).not.toContainText("experiment_plan");
  await expect(page.locator("#rawStream")).not.toContainText("protected_replay");
  await expect(page.locator("#worldGrid")).toBeHidden();
  const visibleCopy = await page.locator("body").innerText();
  expect(visibleCopy).not.toMatch(/가상 지갑|처리된 케이크/);
  await expectNoSeriousA11yViolations(page);
});

test("API failure is labeled as a built-in example and still ends once", async ({ page }) => {
  await page.goto("http://localhost:4173/demo/index.html?api=http://127.0.0.1:1");
  await page.locator("#runButton").click();

  await expect(page.locator("#runNotice")).toContainText(
    "API에 연결하지 못해 내장 예시를 보여드려요",
    { timeout: 5_000 },
  );
  await expect(page.locator("#connectionStatus")).toContainText("완료", { timeout: 15_000 });
  await expect(page.locator('#rawStream .stream-line[data-kind="run.completed"]')).toHaveCount(1);
  await expect(page.locator("#modeBadge")).toHaveText("내장 예시 확인 완료");
  await expect(page.locator("#runNotice")).toContainText("제출한 저장소를 분석한 결과가 아니에요");
  await expectNoSeriousA11yViolations(page);
});

test("320px mobile intake keeps the form path and Korean copy inside the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 568 });
  await page.goto("/demo/index.html");

  await expect(page.getByRole("heading", { level: 1 })).toContainText("먼저 실패시켜 보세요");
  await expect(page.getByLabel("GitHub 저장소")).toBeVisible();
  await expect(page.getByLabel("확인할 버전")).toBeVisible();
  await expect(page.getByLabel("에이전트에게 맡길 일")).toBeVisible();
  const formBox = await page.locator("#missionForm").boundingBox();
  expect(formBox).not.toBeNull();
  expect(formBox.y).toBeLessThan(568);

  const primary = page.getByRole("button", { name: "충돌 시험 시작" });
  await primary.scrollIntoViewIfNeeded();
  await expect(primary).toBeVisible();
  const primaryBox = await primary.boundingBox();
  expect(primaryBox.width).toBeGreaterThanOrEqual(44);
  expect(primaryBox.height).toBeGreaterThanOrEqual(44);

  const layout = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    text: document.body.innerText,
  }));
  expect(layout.scrollWidth).toBe(layout.clientWidth);
  expect(layout.text).not.toMatch(/[가-힣](?:합니다|됩니다|있습니다|없습니다|않습니다)/);
});

test("200% zoom equivalent keeps the form readable without page-level horizontal scroll", async ({ page }) => {
  // 1440×900 at 200% browser zoom exposes roughly a 720×450 CSS-pixel layout viewport.
  await page.setViewportSize({ width: 720, height: 450 });
  await page.goto("/demo/index.html");

  await expect(page.getByLabel("GitHub 저장소")).toBeVisible();
  await expect(page.getByLabel("확인할 버전")).toBeVisible();
  await expect(page.getByLabel("에이전트에게 맡길 일")).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
