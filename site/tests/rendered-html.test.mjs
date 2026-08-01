import assert from "node:assert/strict";
import test from "node:test";

async function request(path = "/", init = undefined) {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
      ...init,
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the NIGHTMARE LAB shell", async () => {
  const response = await request();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>NIGHTMARE LAB — Agent Crash Test<\/title>/i);
  assert.match(html, /src="\/demo\/index\.html"/);
  assert.match(html, /NIGHTMARE LAB live agent crash-test console/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton/);
});

test("health never exposes credentials", async () => {
  const response = await request("/health", { headers: { accept: "application/json" } });
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.status, "ok");
  assert.equal(typeof payload.openai_configured, "boolean");
  assert.equal("openai_api_key" in payload, false);
});

test("hosted fallback preserves the autonomous SSE demo", async () => {
  const accepted = await request("/api/runs", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      input: "synthetic crash test",
      target: {
        repository_url: "https://github.com/caffeine-fighter/AGENT24",
        requested_ref: "main",
        mission: "5만원 이하로 케이크 하나를 주문해줘",
      },
    }),
  });
  assert.equal(accepted.status, 202);
  const run = await accepted.json();
  assert.match(run.run_id, /^[a-f0-9-]+$/);
  assert.match(run.events_url, /^\/api\/runs\/.+\/events\?/);

  const events = await request(run.events_url, {
    headers: { accept: "text/event-stream", "x-agent24-test": "1" },
  });
  assert.equal(events.status, 200);
  assert.match(events.headers.get("content-type") ?? "", /^text\/event-stream\b/i);
  const stream = await events.text();
  assert.match(stream, /"type":"run.started"/);
  assert.match(stream, /"type":"experiment_plan"/);
  assert.match(stream, /"type":"run.completed"/);
  assert.match(stream, /SIMULATION_ONLY/);
});
