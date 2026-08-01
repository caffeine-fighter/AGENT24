import { test as base } from "@playwright/test";

const INTENTIONAL_FAILURE_HOSTS = new Set(["127.0.0.1:1"]);

function isIntentionalFailure(url) {
  try {
    return INTENTIONAL_FAILURE_HOSTS.has(new URL(url).host);
  } catch {
    return false;
  }
}

export const test = base.extend({
  browserHealth: [
    async ({ page }, use) => {
      const violations = {
        console: [],
        pageerror: [],
        requestfailed: [],
        badResponse: 0,
      };
      page.on("console", (message) => {
        // The explicit 127.0.0.1:1 provider-failure fixture is intentionally
        // unreachable; Chromium reports that closed-port probe as unsafe.
        if (
          message.type() === "error"
          && !message.text().includes("ERR_UNSAFE_PORT")
          // The local bundle is intentionally rejected by the hosted API;
          // the UI then switches to the deterministic fixture fallback.
          && !message.text().includes("status of 422")
        ) {
          violations.console.push(message.text().slice(0, 120));
        }
      });
      page.on("pageerror", (error) => {
        violations.pageerror.push(String(error).slice(0, 120));
      });
      page.on("requestfailed", (request) => {
        if (!isIntentionalFailure(request.url())) {
          const pathname = new URL(request.url()).pathname;
          const errorText = request.failure()?.errorText ?? "unknown";
          // EventSource closes after exactly one terminal; Chromium reports
          // the client-side close as ERR_ABORTED, not as a server failure.
          if (!(pathname.endsWith("/events") && errorText === "net::ERR_ABORTED")) {
            violations.requestfailed.push(`${pathname}:${errorText}`);
          }
        }
      });
      page.on("response", (response) => {
        if (response.status() >= 500 && !isIntentionalFailure(response.url())) {
          violations.badResponse += 1;
        }
      });

      await use();

      const total = violations.console.length + violations.pageerror.length + violations.requestfailed.length + violations.badResponse;
      if (total > 0) {
        throw new Error(
          `browser hygiene failed: console=${violations.console.length}, pageerror=${violations.pageerror.length}, requestfailed=${violations.requestfailed.length}, bad_response=${violations.badResponse}`,
        );
      }
    },
    { auto: true },
  ],
});

export { expect } from "@playwright/test";
