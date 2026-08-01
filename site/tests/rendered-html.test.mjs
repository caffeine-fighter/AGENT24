import assert from "node:assert/strict";
import test from "node:test";

const RUN_BODY = JSON.stringify({
  input: "synthetic crash test",
  target: {
    repository_url: "https://github.com/caffeine-fighter/AGENT24",
    requested_ref: "main",
    mission: "5만원 이하로 케이크 하나를 주문해줘",
  },
});

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
  const previousKey = process.env.OPENAI_API_KEY;
  delete process.env.OPENAI_API_KEY;
  try {
    const accepted = await request("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: RUN_BODY,
    });
    assert.equal(accepted.status, 202);
    const run = await accepted.json();
    assert.equal(run.mode, "offline_demo");
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
    assert.match(stream, /"fallback":true/);
    assert.match(stream, /SIMULATION_ONLY/);
  } finally {
    if (previousKey === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = previousKey;
  }
});

test("hosted OpenAI path keeps credentials server-side and emits its evidence", async () => {
  const previousFetch = globalThis.fetch;
  const previousOpenAIKey = process.env.OPENAI_API_KEY;
  const previousGitHubToken = process.env.GITHUB_TOKEN;
  const upstreamCalls = [];
  process.env.OPENAI_API_KEY = "test-openai-key";
  process.env.GITHUB_TOKEN = "test-github-token";

  globalThis.fetch = async (input, init = {}) => {
    const url = new URL(
      typeof input === "string" || input instanceof URL ? input : input.url,
    );
    upstreamCalls.push(url.hostname);
    const headers = new Headers(init.headers);

    if (url.hostname === "api.github.com") {
      assert.equal(headers.get("authorization"), "Bearer test-github-token");
      return Response.json({ sha: "a".repeat(40) });
    }
    if (url.hostname === "api.openai.com") {
      assert.equal(url.pathname, "/v1/responses");
      assert.equal(headers.get("authorization"), "Bearer test-openai-key");
      const body = JSON.parse(String(init.body));
      assert.equal(body.model, "gpt-5.6-terra");
      assert.equal(body.reasoning.effort, "low");
      assert.match(body.safety_identifier, /^repo_[a-f0-9]{24}$/);
      assert.equal(body.input[0].role, "developer");
      assert.match(body.input[0].content[0].text, /Do not claim.*executed or inspected/i);
      return Response.json({
        id: "resp_hosted_test",
        output: [
          {
            content: [
              {
                type: "output_text",
                text: JSON.stringify({
                  rationale: "결제 timeout 뒤 상태 조회 없는 재시도 가능성을 한 변수로 측정한다.",
                  expected_evidence: "ledger commit 뒤 동일 payload 재호출을 trace에서 확인한다.",
                }),
              },
            ],
          },
        ],
      });
    }
    throw new Error(`unexpected upstream request: ${url}`);
  };

  try {
    const accepted = await request("/api/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: RUN_BODY,
    });
    assert.equal(accepted.status, 202);
    const run = await accepted.json();
    assert.equal(run.mode, "openai_hosted");
    assert.deepEqual(upstreamCalls.sort(), ["api.github.com", "api.openai.com"]);

    const events = await request(run.events_url, {
      headers: { accept: "text/event-stream", "x-agent24-test": "1" },
    });
    const stream = await events.text();
    assert.match(stream, /"fallback":false/);
    assert.match(stream, /"response_id":"resp_hosted_test"/);
    assert.match(stream, /결제 timeout 뒤 상태 조회 없는 재시도 가능성/);
    assert.doesNotMatch(stream, /test-openai-key|test-github-token/);
  } finally {
    globalThis.fetch = previousFetch;
    if (previousOpenAIKey === undefined) delete process.env.OPENAI_API_KEY;
    else process.env.OPENAI_API_KEY = previousOpenAIKey;
    if (previousGitHubToken === undefined) delete process.env.GITHUB_TOKEN;
    else process.env.GITHUB_TOKEN = previousGitHubToken;
  }
});
