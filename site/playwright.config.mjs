import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/browser",
  fullyParallel: false,
  retries: 0,
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:4173",
    screenshot: "off",
    trace: "off",
    video: "off",
  },
  webServer: [
    {
      command: "node tests/browser/github-upstream.mjs",
      reuseExistingServer: !process.env.CI,
      timeout: 10_000,
      url: "http://127.0.0.1:4174/health",
    },
    {
      command: "node tests/browser/production-server.mjs",
      env: {
        ...process.env,
        AGENT24_GITHUB_API_BASE_URL: "http://127.0.0.1:4174",
        RUN_CONTEXT_SECRET: "browser-only-context-secret-with-at-least-32-bytes",
      },
      reuseExistingServer: !process.env.CI,
      timeout: 30_000,
      url: "http://localhost:4173/health",
    },
  ],
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
