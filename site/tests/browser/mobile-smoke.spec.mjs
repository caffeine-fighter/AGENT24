import { expect, test } from "./fixtures.mjs";

const MISSION = "엄마 생일 케이크 하나를 5만원 이하로 한 번만 주문해줘.";

test("mobile one-input path completes over production SSE", async ({ page }) => {
  await page.goto("/demo/index.html?demo=github");
  await page.locator("#missionInput").fill(MISSION);
  await page.locator("#runButton").click();
  await expect(page.locator("#connectionStatus")).toContainText("완료", {
    timeout: 15_000,
  });

  const disclosure = page.locator("#rawStreamDisclosure");
  await expect(disclosure).not.toHaveAttribute("hidden");
  if (!(await disclosure.evaluate((element) => element.open))) {
    await disclosure.locator("summary").click();
  }
  await expect(page.locator("#rawStream")).toBeVisible();

  const metrics = await page.locator("#rawStream .stream-line").evaluateAll((lines) => ({
    runStarted: lines.filter((line) => line.dataset.kind === "run.started").length,
    terminal: lines.filter((line) =>
      ["run.completed", "run.failed"].includes(line.dataset.kind),
    ).length,
    sequence: lines.map((line) => Number(line.querySelector(".stream-seq")?.textContent?.replace("#", ""))),
  }));
  expect(metrics.runStarted).toBe(1);
  expect(metrics.terminal).toBe(1);
  expect(metrics.sequence.length).toBeGreaterThan(0);
  expect(metrics.sequence.every((value, index) => value === index + 1)).toBe(true);
  await expect(page.locator("#scopeNotice")).toHaveAttribute("data-scope", /.+/);
});
