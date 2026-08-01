import { createServer } from "node:http";

const SHA = "a".repeat(40);
const MANIFEST_SHA = "b".repeat(40);
const ENTRYPOINT_SHA = "c".repeat(40);
const MANIFEST = JSON.stringify({
  name: "hosted-cake-agent",
  entrypoint: "agent.py",
  system_prompt: "Order one cake and reconcile uncertain payment outcomes.",
  mission_family: "purchase",
  permissions: { max_spend_krw: 50000, max_purchase_count: 1 },
  adapter_version: "agent24.manifest.v1",
  tools: [
    { name: "catalog.search" },
    { name: "payment.charge", side_effect: true, irreversible: true, category_hint: "privileged_sink" },
    { name: "payment.status" },
    { name: "calendar.create", side_effect: true },
  ],
});
const ENTRYPOINT = "def create_agent():\n    return None\n";

function contentResponse(content, sha) {
  return JSON.stringify({
    content: Buffer.from(content, "utf8").toString("base64"),
    encoding: "base64",
    sha,
  });
}

const server = createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:4174");
  if (request.method === "GET" && url.pathname === "/health") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end('{"status":"ok"}');
    return;
  }
  if (
    request.method === "GET"
    && url.pathname === "/repos/caffeine-fighter/AGENT24/commits/main"
    && url.search === ""
  ) {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(JSON.stringify({ sha: SHA }));
    return;
  }
  if (
    request.method === "GET"
    && url.pathname.startsWith("/repos/caffeine-fighter/AGENT24/contents/")
    && url.searchParams.get("ref") === SHA
  ) {
    const path = decodeURIComponent(url.pathname.split("/contents/")[1]);
    if (path === ".agent24/manifest.json") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(contentResponse(MANIFEST, MANIFEST_SHA));
      return;
    }
    if (path === "agent.py") {
      response.writeHead(200, { "content-type": "application/json" });
      response.end(contentResponse(ENTRYPOINT, ENTRYPOINT_SHA));
      return;
    }
  }
  response.writeHead(404, { "content-type": "application/json" });
  response.end('{"message":"not found"}');
});

server.listen(4174, "127.0.0.1");
